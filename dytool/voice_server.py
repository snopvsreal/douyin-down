"""Máy chủ giọng nói của dytool: chạy model OmniVoice (k2-fsa/OmniVoice, Apache-2.0) trong 1 tiến trình riêng —
thay cho backend của app VoiceStudio, KHÔNG cần cài VoiceStudio.

Giữ đúng phần API VoiceStudio mà dytool đang gọi (vs_api.py, dub.py, services.py) nên các trang Giọng nói / lồng
tiếng không phải đổi: /health, /model/status, /profiles, /v1/audio/speech, /generate, /history, /audio/<id>.wav,
/pronunciation, /transcribe, /design/describe, /sysinfo, chuyển model sang RAM, giải phóng bộ nhớ.

Tiến trình riêng (không nạp model trong app): tắt = trả hết VRAM / RAM (Python trên Windows không trả RAM đã cấp),
model lỗi không kéo app chết theo. stdin là dây sống: dytool đóng ống (thoát, kể cả bị tắt ngang) -> máy chủ tự thoát.

Chạy: python -m dytool.voice_server --port 3900"""
from __future__ import annotations

import argparse
import gc
import io
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

# ở đầu module (không import trong hàm): có `from __future__ import annotations` nên FastAPI đọc kiểu tham số theo tên
# trong module — import trong build_app thì Request bị hiểu thành tham số query (lỗi 422)
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "voice"
VERSION = "omnivoice-0.2.1"
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SR = 24000

# ============================ lưu trữ (JSON nhỏ + file wav) ============================
# data/voice/profiles.json   [{id, name, ref_text, instruct, language, seed, kind, created_at}]
# data/voice/profiles/<id>.wav  giọng mẫu · <id>.pt  mã giọng đã tính sẵn (VoiceClonePrompt)
# data/voice/history.json    [{id, text, mode, language, instruct, profile_id, duration_seconds, generation_time,
#                              seed, starred, created_at}] · data/voice/history/<id>.wav
# data/voice/pronunciation.json [{id, term, replacement, type, language, enabled, created_at}]
# data/voice/settings.json   {offload: bool}
_db_lock = threading.RLock()
HISTORY_MAX = 300


def _path(name: str) -> Path:
    return DATA / name


def _load(name: str, default):
    try:
        v = json.loads(_path(name).read_text("utf-8-sig"))
        return v if isinstance(v, type(default)) else default
    except (OSError, ValueError):
        return default


def _save(name: str, value) -> None:
    p = _path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, p)


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def check_id(x: str) -> str:
    if not ID_RE.match(x or ""):
        raise ApiError(400, "Mã không hợp lệ")
    return x


class ApiError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


# ============================ model ============================

class Engine:
    """Nạp model 1 lần ở luồng nền; đọc tuần tự (1 GPU). Chuyển sang RAM / giải phóng theo yêu cầu."""

    def __init__(self):
        self.model = None
        self.device = "cpu"
        self.state = "idle"            # idle | loading | loaded | error
        self.detail = ""
        self.error = ""
        self.lock = threading.Lock()   # 1 lần đọc / nạp tại 1 thời điểm
        self.on_cpu = False            # đã chuyển sang RAM (offload) — lần đọc sau đưa lại lên GPU
        self.force_cpu = False         # card đồ hoạ lỗi -> chạy CPU tới hết phiên này
        self.fallback = ""             # lý do đang chạy CPU thay cho GPU ("" = không)
        self._prompts: dict[str, object] = {}

    def _pick_device(self) -> str:
        import torch
        if self.force_cpu or not torch.cuda.is_available():
            return "cpu"
        fb = _settings().get("cpu_fallback") or {}
        if fb and time.time() - float(fb.get("at") or 0) < FALLBACK_HOURS * 3600:
            self.force_cpu, self.fallback = True, str(fb.get("reason") or "card đồ hoạ lỗi lần trước")
            return "cpu"
        return "cuda"

    def fall_back_to_cpu(self, e: Exception) -> None:
        """Card đồ hoạ lỗi (driver cũ, card không hợp torch, hết VRAM…) -> bỏ model trên GPU, lần nạp sau chạy CPU
        (chậm hơn nhưng lồng tiếng vẫn chạy). Lỗi không phải hết VRAM thì nhớ FALLBACK_HOURS giờ (khởi động lại máy
        chủ không phải thử GPU hỏng lần nữa); nút 'Thử lại card đồ hoạ' ở trang Giọng nói xoá dấu này."""
        reason = f"{type(e).__name__}: {e}".strip()[:300]
        print(f"Card đồ hoạ lỗi -> chuyển sang CPU: {reason}", flush=True)
        self.force_cpu, self.fallback = True, reason
        if not _is_oom(e):
            with _db_lock:
                s = _settings()
                s["cpu_fallback"] = {"reason": reason, "at": time.time()}
                _save("settings.json", s)
        self.model, self.on_cpu, self.device = None, False, "cpu"
        self._prompts.clear()
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass

    def use_gpu_again(self) -> None:
        """Xoá dấu chạy CPU; lần nạp sau thử lại GPU (gọi trong self.lock)."""
        with _db_lock:
            s = _settings()
            if s.pop("cpu_fallback", None) is not None:
                _save("settings.json", s)
        self.force_cpu, self.fallback = False, ""
        self.unload()

    def start_loading(self) -> None:
        if self.state in ("loading", "loaded"):
            return
        self.state, self.error = "loading", ""
        threading.Thread(target=self._load_guarded, daemon=True, name="ov-load").start()

    def _load_guarded(self) -> None:
        with self.lock:
            try:
                self._load()
            except Exception as e:      # lỗi nạp -> hiện ở trang, lần đọc sau thử lại
                self.state, self.error = "error", f"{type(e).__name__}: {e}"
                self.detail = "Không nạp được model giọng nói"
                print(f"Nạp model lỗi: {self.error}", flush=True)

    def _load(self) -> None:
        import torch
        from . import voice_model
        if self.model is not None:
            self.state = "loaded"
            return
        self.state = "loading"
        self.device = self._pick_device()
        self.detail = "Đang kiểm tra file model…"
        path = voice_model.ensure()
        from omnivoice.models.omnivoice import OmniVoice
        t = time.time()
        while True:
            self.detail = ("Đang nạp model vào card đồ hoạ…" if self.device == "cuda"
                           else "Đang nạp model (CPU — chậm)…")
            # fp16 trên GPU (nửa VRAM: ~2 GB, vừa card 4 GB); CPU không có phép nhân fp16 nhanh -> fp32
            dtype = torch.float16 if self.device == "cuda" else torch.float32
            try:
                self.model = OmniVoice.from_pretrained(str(path), device_map=self.device, dtype=dtype)
                break
            except Exception as e:
                if self.device != "cuda" or not _is_cuda_error(e):
                    raise
                self.fall_back_to_cpu(e)          # nạp lên card lỗi -> nạp lại trên CPU
        self.on_cpu = False
        self.state, self.detail = "loaded", ""
        print(f"Đã nạp OmniVoice trên {self.device} ({time.time() - t:.0f} s)"
              + (f" — chạy CPU vì card đồ hoạ lỗi: {self.fallback}" if self.fallback else ""), flush=True)

    def ensure(self):
        """Model sẵn sàng trên thiết bị đọc (gọi trong self.lock). Trả model."""
        if self.model is None:
            self._load()
        if self.on_cpu and self.device == "cuda":
            self.model.to("cuda")
            self.model.audio_tokenizer.to("cuda")
            self.on_cpu = False
        return self.model

    def after_generate(self) -> None:
        if self.device != "cuda" or self.model is None:
            return
        if _settings().get("offload"):
            self.offload()
        else:
            # trả phần VRAM bộ đệm của torch giữ lại sau lần đọc (đo: còn trống 0 GB / 4 GB) -> Demucs tách giọng
            # (tiến trình khác) vẫn chạy được trên GPU
            import torch
            torch.cuda.empty_cache()

    def offload(self) -> None:
        import torch
        self.model.to("cpu")
        self.model.audio_tokenizer.to("cpu")
        self.on_cpu = True
        torch.cuda.empty_cache()

    def unload(self) -> None:
        import torch
        self.model = None
        self._prompts.clear()
        self.state, self.on_cpu = "idle", False
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    # ---------- giọng mẫu ----------

    def prompt_for(self, prof: dict):
        """Mã giọng (VoiceClonePrompt) của 1 giọng đã lưu: tính 1 lần từ file mẫu rồi lưu ra <id>.pt."""
        from omnivoice.models.omnivoice import VoiceClonePrompt
        pid = prof["id"]
        if pid in self._prompts:
            return self._prompts[pid]
        pt, wav = DATA / "profiles" / f"{pid}.pt", DATA / "profiles" / f"{pid}.wav"
        p = None
        if pt.exists() and pt.stat().st_mtime >= wav.stat().st_mtime:
            try:
                p = VoiceClonePrompt.load(str(pt))
            except Exception:
                p = None
        if p is None:
            p = self.model.create_voice_clone_prompt(ref_audio=str(wav), ref_text=prof.get("ref_text") or None)
            try:
                p.save(str(pt))
            except OSError:
                pass
        self._prompts[pid] = p
        return p

    def forget(self, pid: str) -> None:
        self._prompts.pop(pid, None)


FALLBACK_HOURS = 24          # card lỗi (không phải hết VRAM) -> chạy CPU bấy nhiêu giờ rồi mới thử lại GPU

_CUDA_WORDS = ("cuda", "cudnn", "cublas", "cufft", "curand", "nvrtc", "out of memory", "no kernel image",
               "acceleratorerror", "nvidia driver", "device-side assert")


def _is_cuda_error(e: BaseException) -> bool:
    s = f"{type(e).__name__} {e}".lower()
    return any(w in s for w in _CUDA_WORDS)


def _is_oom(e: BaseException) -> bool:
    return "out of memory" in f"{type(e).__name__} {e}".lower()


ENGINE = Engine()


def _seed_all(seed: int) -> None:
    import numpy as np
    import torch
    random.seed(seed)
    np.random.seed(seed % (2 ** 32))
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


# ============================ xử lý văn bản ============================

_PAUSE_RE = re.compile(r"\[pause\s+(\d+(?:[.,]\d+)?)\s*(ms|s)\]", re.I)
_INLINE_RE = re.compile(r"\[\[([^\[\]|]{0,200})(?:\|([^\[\]]{0,200}))?\]\]")
PAUSE_MAX_MS = 10_000


def apply_pronunciation(text: str, language: str) -> str:
    """Từ điển phát âm (mọi giọng) + sửa cách đọc 1 lần [[từ|cách đọc]] / [[cách đọc]]."""
    text = _INLINE_RE.sub(lambda m: (m.group(2) if m.group(2) is not None else m.group(1)).strip(), text)
    for e in _load("pronunciation.json", []):
        if not e.get("enabled", True) or not e.get("term"):
            continue
        if e.get("language", "*") not in ("*", language):
            continue
        text = re.sub(rf"(?<!\w){re.escape(e['term'])}(?!\w)", e.get("replacement", ""), text, flags=re.I)
    return text


def split_pauses(text: str) -> list[tuple[str, int]]:
    """[(đoạn chữ, ms nghỉ sau đoạn)] theo dấu [pause 500ms] / [pause 1s]."""
    out, last = [], 0
    for m in _PAUSE_RE.finditer(text):
        ms = float(m.group(1).replace(",", ".")) * (1000 if m.group(2).lower() == "s" else 1)
        chunk = text[last:m.start()]
        if not chunk.strip() and out:
            out[-1] = (out[-1][0], min(PAUSE_MAX_MS, out[-1][1] + int(ms)))
        else:
            out.append((chunk, min(PAUSE_MAX_MS, int(ms))))
        last = m.end()
    tail = text[last:]
    if tail.strip() or not out:
        out.append((tail, 0))
    return out


# ============================ đọc ============================

def _lang(language: str | None) -> str | None:
    return None if not language or language == "Auto" else language


def _find_profile(voice: str) -> dict | None:
    v = (voice or "").strip().lower()
    for p in _load("profiles.json", []):
        if p.get("id", "").lower() == v or p.get("name", "").lower() == v:
            return p
    return None


def synthesize(text: str, *, language: str | None = "vi", profile: dict | None = None, ref_audio: Path | None = None,
               ref_text: str = "", instruct: str = "", seed: int | None = None, duration: float | None = None,
               speed: float | None = None, gen: dict | None = None,
               pronounce: bool = True) -> tuple["object", int, float]:
    """Đọc văn bản -> (mảng float32 24 kHz, seed đã dùng, giây chạy). Có [pause …] thì đọc từng đoạn rồi chèn lặng
    (duration chia theo tỉ lệ số chữ)."""
    import numpy as np
    text = (text or "").strip()
    if pronounce:
        text = apply_pronunciation(text, language or "*")
    if not text:
        raise ApiError(400, "Văn bản trống")
    parts = split_pauses(text)
    seed = int(seed) if seed is not None else random.randint(0, 2 ** 31 - 1)
    gen = {k: v for k, v in (gen or {}).items() if v is not None}
    t0 = time.time()
    speech_chars = sum(len(p[0].strip()) for p in parts) or 1
    pause_s = sum(p[1] for p in parts) / 1000

    def run() -> list:
        model = ENGINE.ensure()
        kw: dict = {"language": _lang(language)}
        if profile is not None:
            kw["voice_clone_prompt"] = ENGINE.prompt_for(profile)
        elif ref_audio is not None:
            kw["voice_clone_prompt"] = model.create_voice_clone_prompt(ref_audio=str(ref_audio),
                                                                       ref_text=ref_text or None)
        elif instruct:
            kw["instruct"] = instruct
        out = []
        for chunk, pause_ms in parts:
            if chunk.strip():
                _seed_all(seed)
                d = None
                if duration:
                    d = max(0.3, (float(duration) - pause_s) * len(chunk.strip()) / speech_chars)
                audio = model.generate(text=chunk.strip(), duration=d, speed=None if d else speed,
                                       **kw, **gen)[0]
                out.append(np.asarray(audio, dtype=np.float32))
            if pause_ms:
                out.append(np.zeros(int(SR * pause_ms / 1000), dtype=np.float32))
        return out

    with ENGINE.lock:
        retried_oom = False
        while True:
            try:
                out = run()
                break
            except ApiError:
                raise
            except ValueError as e:              # mô tả giọng sai từ khoá, ngôn ngữ lạ…
                raise ApiError(400, str(e)) from e
            except Exception as e:
                # card đồ hoạ lỗi -> hết VRAM thì dọn bộ nhớ đệm thử GPU thêm 1 lần, còn lại chuyển CPU rồi đọc lại
                if ENGINE.device != "cuda" or not _is_cuda_error(e):
                    raise
                if _is_oom(e) and not retried_oom:
                    retried_oom = True
                    print(f"Hết VRAM, dọn bộ nhớ rồi thử lại: {e}", flush=True)
                    gc.collect()
                    try:
                        import torch
                        torch.cuda.empty_cache()
                    except Exception:
                        pass
                    continue
                ENGINE.fall_back_to_cpu(e)
            finally:
                try:
                    ENGINE.after_generate()
                except Exception as e:           # card hỏng ngay lúc dọn -> lần đọc sau tự chuyển CPU
                    print(f"Dọn VRAM sau khi đọc lỗi: {e}", flush=True)
    audio = np.concatenate(out) if out else np.zeros(1, dtype=np.float32)
    return audio, seed, time.time() - t0


def wav_bytes(audio) -> bytes:
    import soundfile as sf
    buf = io.BytesIO()
    sf.write(buf, audio, SR, format="WAV", subtype="PCM_16")
    return buf.getvalue()


# hiệu ứng sau khi đọc (trang Tạo giọng nói) — dựng lại các preset của VoiceStudio bằng bộ lọc FFmpeg
EFFECTS = {
    "raw": "",
    "broadcast": "highpass=f=80,dynaudnorm=f=400:g=15:m=6:p=0.9,bass=g=1.5,treble=g=2,alimiter=limit=0.89",
    "cinematic": "highpass=f=60,acompressor=threshold=-15dB:ratio=1.8:attack=10:release=150,"
                 "aecho=0.85:0.6:60|90:0.18|0.12,alimiter=limit=0.84",
    "podcast": "highpass=f=100,agate=threshold=-40dB:release=200,"
               "acompressor=threshold=-20dB:ratio=4:attack=2:release=60,bass=g=-1,equalizer=f=1500:t=q:w=1:g=2,"
               "treble=g=1.5,alimiter=limit=0.94",
    "warm": "highpass=f=60,bass=g=3,equalizer=f=1000:t=q:w=1:g=1,treble=g=-1,"
            "acompressor=threshold=-16dB:ratio=2:attack=8:release=120,aecho=0.9:0.7:40:0.06",
    "bright": "highpass=f=80,bass=g=-1,treble=g=4,acompressor=threshold=-14dB:ratio=2.5:attack=3:release=80,"
              "alimiter=limit=0.89",
}


def apply_effect(wav: bytes, effect: str) -> bytes:
    flt = EFFECTS.get(effect or "", "")
    if not flt:
        return wav
    try:
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "wav", "-i", "pipe:0", "-af", flt, "-ar", str(SR),
                            "-ac", "1", "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"],
                           input=wav, capture_output=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return wav
    return r.stdout if r.returncode == 0 and r.stdout else wav


def to_wav_file(data: bytes, filename: str, dest: Path) -> Path:
    """File người dùng tải lên (mp3 / m4a / wav…) -> wav mono 24 kHz bằng FFmpeg (không dùng librosa)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename or "a.wav").suffix[:8] or ".bin"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
        f.write(data)
        src = f.name
    try:
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-ac", "1", "-ar", str(SR), "-t", "60",
                            "-c:a", "pcm_s16le", str(dest)], capture_output=True, timeout=120)
    finally:
        os.unlink(src)
    if r.returncode != 0 or not dest.exists():
        raise ApiError(400, "Không đọc được file âm thanh này — thử file .wav / .mp3 khác")
    return dest


# ============================ nghe -> chữ ============================

_whisper = None


def transcribe(path: Path, language: str | None) -> str:
    global _whisper
    from faster_whisper import WhisperModel
    from .subtitles import load_pcm
    if _whisper is None:
        _whisper = WhisperModel("small", device="cpu", compute_type="int8")
    segs, _info = _whisper.transcribe(load_pcm(path), language=_lang(language), vad_filter=True)
    return " ".join(s.text.strip() for s in segs).strip()


# ============================ mô tả giọng tự do -> từ khoá OmniVoice ============================

_DESCRIBE = (
    ("Gender", "female", r"\b(female|woman|women|girl|lady|feminine)\b"),
    ("Gender", "male", r"\b(male|man|men|boy|guy|gentleman|masculine)\b"),
    ("Age", "child", r"\b(child|kid|children|little)\b"),
    ("Age", "teenager", r"\b(teen|teenager|teenage|adolescent)\b"),
    ("Age", "middle-aged", r"\b(middle[- ]aged|mature|forties|fifties)\b"),
    ("Age", "elderly", r"\b(elderly|old|aged|senior|grandpa|grandma|grandfather|grandmother)\b"),
    ("Age", "young adult", r"\b(young|youthful|twenties)\b"),
    ("Pitch", "very low pitch", r"\b(very (low|deep)|extremely deep|bass)\b"),
    ("Pitch", "very high pitch", r"\b(very high|squeaky|shrill)\b"),
    ("Pitch", "low pitch", r"\b(low|deep|baritone|husky)\b"),
    ("Pitch", "high pitch", r"\b(high|bright|soprano)\b"),
    ("Pitch", "moderate pitch", r"\b(moderate|medium|neutral|average)\b"),
    ("Style", "whisper", r"\b(whisper\w*|breathy|hushed)\b"),
    ("EnglishAccent", "american accent", r"\b(american|us)\b"),
    ("EnglishAccent", "british accent", r"\b(british|english|uk|london)\b"),
    ("EnglishAccent", "australian accent", r"\baustralian\b"),
    ("EnglishAccent", "canadian accent", r"\bcanadian\b"),
    ("EnglishAccent", "indian accent", r"\bindian\b"),
    ("EnglishAccent", "chinese accent", r"\bchinese\b"),
    ("EnglishAccent", "korean accent", r"\bkorean\b"),
    ("EnglishAccent", "japanese accent", r"\bjapanese\b"),
    ("EnglishAccent", "portuguese accent", r"\b(portuguese|brazilian)\b"),
    ("EnglishAccent", "russian accent", r"\brussian\b"),
)
_FILLER = {"a", "an", "the", "voice", "with", "and", "accent", "pitch", "speaker", "tone", "sounding", "very", "of",
           "in", "who", "is", "aged", "adult"}


def describe(text: str) -> dict:
    low = text.lower()
    attrs: dict[str, str] = {}
    used: set[str] = set()
    for key, value, pat in _DESCRIBE:
        m = re.search(pat, low)
        if m and key not in attrs:
            attrs[key] = value
            used.update(m.group(0).split())
    order = ("Gender", "Age", "Pitch", "Style", "EnglishAccent")
    instruct = ", ".join(attrs[k] for k in order if k in attrs)
    unmatched = [w for w in re.findall(r"[a-z'-]+", low) if w not in used and w not in _FILLER]
    return {"attrs": attrs, "instruct": instruct, "unmatched": unmatched}


# ============================ hệ thống ============================

def _settings() -> dict:
    return _load("settings.json", {})


def sysinfo() -> dict:
    out: dict = {}
    try:
        import psutil
        vm = psutil.virtual_memory()
        out.update(ram=(vm.total - vm.available) / 1e9, total_ram=vm.total / 1e9,
                   process_ram=psutil.Process().memory_info().rss / 1e9)
    except Exception:
        pass
    try:
        import torch
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            out.update(vram=(total - free) / 1e9, total_vram=total / 1e9, gpu=torch.cuda.get_device_name(0))
    except Exception:
        pass
    return out


# ============================ ứng dụng web ============================

def build_app():

    app = FastAPI(title="dytool voice", docs_url=None, redoc_url=None)

    @app.exception_handler(ApiError)
    async def _api_err(_req, e: ApiError):
        return JSONResponse({"detail": str(e)}, status_code=e.status)

    @app.exception_handler(Exception)
    async def _any_err(_req, e: Exception):
        # lỗi chưa lường trước (CUDA, thiếu file, nạp model…) -> trả đúng tên lỗi thay cho "Internal Server Error"
        # trơn để dytool hiện ra; Starlette vẫn ghi traceback đầy đủ vào data/voicestudio.log
        msg = f"{type(e).__name__}: {e}".strip()
        low = msg.lower()
        if "out of memory" in low:
            msg += " — card đồ hoạ hết VRAM: đóng app khác dùng GPU / bật 'chuyển model sang RAM' rồi thử lại"
        elif "no kernel image" in low or "not compatible with the current pytorch" in low:
            msg += " — card đồ hoạ này không hợp bản torch đã cài: chạy 'Cài đặt lại thư viện' (DYTOOL_TORCH=cpu để dùng CPU)"
        return JSONResponse({"detail": msg[:1000]}, status_code=500)

    @app.middleware("http")
    async def _guard(request: Request, call_next):
        # chỉ nhận yêu cầu từ chính máy này (không có đăng nhập) — chặn trang web lạ gọi qua trình duyệt
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        if request.headers.get("origin"):
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        return await call_next(request)

    # ---------- trạng thái ----------

    @app.get("/health")
    def health():
        import torch
        try:
            dev = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU"
        except Exception:                    # driver hỏng: /health vẫn phải trả lời, không thì dytool tưởng máy chủ tắt
            dev = "CPU"
        if ENGINE.fallback:
            dev = f"CPU (card {dev} lỗi)" if dev != "CPU" else "CPU (card đồ hoạ lỗi)"
        return {"status": "ok", "version": VERSION, "device": dev}

    @app.post("/model/use-gpu")
    def use_gpu():
        if not ENGINE.lock.acquire(timeout=5):
            raise ApiError(409, "Đang đọc — đợi đọc xong rồi thử lại")
        try:
            ENGINE.use_gpu_again()
        finally:
            ENGINE.lock.release()
        ENGINE.start_loading()
        return {"ok": True}

    @app.get("/model/status")
    def model_status():
        from . import voice_model
        s = voice_model.state()
        if ENGINE.state == "idle" and s.get("phase") != "ready":
            ENGINE.start_loading()            # tải / kiểm tra model ở nền
        prog = int(s.get("done", 0) * 100 / max(1, s.get("total", 1))) if s.get("phase") == "downloading" else None
        detail = ENGINE.detail
        if s.get("phase") == "downloading":
            detail = f"Đang tải model giọng nói ({s.get('done', 0) / 1e9:.1f} / {s.get('total', 0) / 1e9:.1f} GB)…"
        return {"loaded": ENGINE.state == "loaded", "state": ENGINE.state, "progress": prog,
                "detail": ENGINE.error or detail, "error": ENGINE.error, "on_cpu": ENGINE.on_cpu,
                "checkpoint": f"{voice_model.REPO}@{voice_model.REVISION[:7]}", "device": ENGINE.device,
                "fallback": ENGINE.fallback}

    @app.get("/startup/progress")
    def startup_progress():
        return {"label": ENGINE.detail or "Đang khởi động…"}

    @app.get("/sysinfo")
    def get_sysinfo():
        return sysinfo()

    @app.get("/api/settings/perf/offload-after-generation")
    def get_offload():
        return {"enabled": bool(_settings().get("offload"))}

    @app.put("/api/settings/perf/offload-after-generation")
    async def put_offload(request: Request):
        on = bool((await request.json()).get("enabled"))
        with _db_lock:
            s = _settings()
            s["offload"] = on
            _save("settings.json", s)
        if on and ENGINE.model is not None and ENGINE.device == "cuda" and not ENGINE.on_cpu:
            def run():                       # chép 3 GB sang RAM mất vài chục giây: làm ở nền, trả lời ngay
                with ENGINE.lock:
                    if ENGINE.model is not None and not ENGINE.on_cpu:
                        ENGINE.offload()
            threading.Thread(target=run, daemon=True, name="ov-offload").start()
        return {"enabled": on}

    @app.post("/system/flush-memory")
    def flush():
        before = sysinfo()
        if not ENGINE.lock.acquire(timeout=5):
            raise ApiError(409, "Đang đọc — đợi đọc xong rồi giải phóng")
        try:
            ENGINE.unload()
        finally:
            ENGINE.lock.release()
        global _whisper
        _whisper = None
        gc.collect()
        after = sysinfo()
        return {"freed_vram_gb": round(before.get("vram", 0) - after.get("vram", 0), 2)}

    # ---------- giọng đã lưu ----------

    @app.get("/profiles")
    def list_profiles():
        return [{**p, "has_audio": (DATA / "profiles" / f"{p['id']}.wav").exists()}
                for p in _load("profiles.json", [])]

    @app.get("/profiles/{pid}/audio")
    def profile_audio(pid: str):
        f = DATA / "profiles" / f"{check_id(pid)}.wav"
        if not f.exists():
            raise ApiError(404, "Giọng không có file mẫu")
        return Response(f.read_bytes(), media_type="audio/wav")

    @app.post("/profiles")
    async def add_profile(name: str = Form(...), ref_text: str = Form(""), language: str = Form("vi"),
                          kind: str = Form("clone"), instruct: str = Form(""), seed: str = Form(""),
                          ref_audio: UploadFile = File(...)):
        name = " ".join(name.split())[:60]
        if not name:
            raise ApiError(400, "Đặt tên cho giọng")
        data = await ref_audio.read()
        if not data:
            raise ApiError(400, "File giọng mẫu trống")
        with _db_lock:
            items = _load("profiles.json", [])
            if any(p.get("name", "").lower() == name.lower() for p in items):
                raise ApiError(409, f"Đã có giọng tên '{name}'")
            pid = _new_id()
            to_wav_file(data, ref_audio.filename or "ref.wav", DATA / "profiles" / f"{pid}.wav")
            prof = {"id": pid, "name": name, "ref_text": ref_text.strip()[:2000], "instruct": instruct.strip(),
                    "language": language or "vi", "seed": int(seed) if seed.strip().lstrip("-").isdigit() else None,
                    "kind": kind or "clone", "created_at": time.time()}
            items.append(prof)
            _save("profiles.json", items)
        return prof

    @app.put("/profiles/{pid}")
    async def edit_profile(pid: str, request: Request):
        check_id(pid)
        body = await request.json()
        with _db_lock:
            items = _load("profiles.json", [])
            p = next((x for x in items if x.get("id") == pid), None)
            if p is None:
                raise ApiError(404, "Không còn giọng này")
            if "name" in body:
                name = " ".join(str(body["name"]).split())[:60]
                if not name:
                    raise ApiError(400, "Đặt tên cho giọng")
                if any(x.get("name", "").lower() == name.lower() and x.get("id") != pid for x in items):
                    raise ApiError(409, f"Đã có giọng tên '{name}'")
                p["name"] = name
            if "ref_text" in body:
                p["ref_text"] = str(body["ref_text"])[:2000]
                (DATA / "profiles" / f"{pid}.pt").unlink(missing_ok=True)
                ENGINE.forget(pid)
            _save("profiles.json", items)
        return p

    @app.delete("/profiles/{pid}")
    def delete_profile(pid: str):
        check_id(pid)
        with _db_lock:
            items = [p for p in _load("profiles.json", []) if p.get("id") != pid]
            _save("profiles.json", items)
        for ext in ("wav", "pt"):
            (DATA / "profiles" / f"{pid}.{ext}").unlink(missing_ok=True)
        ENGINE.forget(pid)
        return {"ok": True}

    @app.post("/transcribe")
    async def do_transcribe(audio: UploadFile = File(...), language: str = Form("")):
        data = await audio.read()
        with tempfile.TemporaryDirectory() as d:
            wav = to_wav_file(data, audio.filename or "a.wav", Path(d) / "a.wav")
            from starlette.concurrency import run_in_threadpool
            text = await run_in_threadpool(transcribe, wav, language or None)
        return {"text": text}

    @app.post("/design/describe")
    async def do_describe(request: Request):
        return describe(str((await request.json()).get("description") or "")[:300])

    # ---------- đọc ----------

    def _record(audio_wav: bytes, *, text, mode, language, instruct, profile_id, seed, gen_time) -> dict:
        hid = _new_id()
        (DATA / "history").mkdir(parents=True, exist_ok=True)
        (DATA / "history" / f"{hid}.wav").write_bytes(audio_wav)
        import soundfile as sf
        try:
            dur = sf.info(io.BytesIO(audio_wav)).duration
        except Exception:
            dur = None
        item = {"id": hid, "text": text, "mode": mode, "language": language, "instruct": instruct,
                "profile_id": profile_id, "duration_seconds": dur, "generation_time": round(gen_time, 2),
                "seed": seed, "starred": False, "created_at": time.time()}
        with _db_lock:
            items = _load("history.json", [])
            items.append(item)
            # giữ HISTORY_MAX bản mới nhất (bản gắn sao không xoá)
            extra = len(items) - HISTORY_MAX
            if extra > 0:
                drop = {h["id"] for h in sorted((h for h in items if not h.get("starred")),
                                                key=lambda h: h.get("created_at") or 0)[:extra]}
                for d in drop:
                    (DATA / "history" / f"{d}.wav").unlink(missing_ok=True)
                items = [h for h in items if h["id"] not in drop]
            _save("history.json", items)
        return item

    def _num(v, as_int=False):
        try:
            x = float(str(v).replace(",", "."))
        except (TypeError, ValueError):
            return None
        return int(round(x)) if as_int else x

    @app.post("/v1/audio/speech")
    async def speech(request: Request):
        """Kiểu OpenAI (lồng tiếng / đọc thử / thiết kế giọng). voice = tên hoặc mã giọng đã lưu; 'default' + instructions
        = giọng thiết kế theo mô tả. Không ghi lịch sử."""
        from starlette.concurrency import run_in_threadpool
        b = await request.json()
        text = str(b.get("input") or "")[:20000]
        voice = str(b.get("voice") or "default")
        prof = None if voice in ("", "default") else _find_profile(voice)
        if prof is None and voice not in ("", "default"):
            raise ApiError(404, f"Không có giọng '{voice}'")
        gen = {"num_step": _num(b.get("num_step"), True)}
        audio, seed, gt = await run_in_threadpool(
            synthesize, text, language=b.get("language") or "vi", profile=prof,
            instruct="" if prof else str(b.get("instructions") or ""),
            seed=_num(b.get("seed"), True) if b.get("seed") is not None else (prof or {}).get("seed"),
            duration=_num(b.get("duration")), speed=_num(b.get("speed")), gen=gen)
        return Response(wav_bytes(audio), media_type="audio/wav",
                        headers={"x-seed": str(seed), "x-gen-time": f"{gt:.2f}"})

    @app.post("/generate")
    async def generate(text: str = Form(...), language: str = Form("vi"), speed: str = Form("1"),
                       profile_id: str = Form(""), instruct: str = Form(""), ref_text: str = Form(""),
                       seed: str = Form(""), duration: str = Form(""), effect_preset: str = Form("broadcast"),
                       denoise: str = Form("true"), postprocess_output: str = Form(""),
                       num_step: str = Form(""), guidance_scale: str = Form(""), t_shift: str = Form(""),
                       position_temperature: str = Form(""), class_temperature: str = Form(""),
                       layer_penalty_factor: str = Form(""), pronounce: str = Form("true"),
                       audio_chunk_duration: str = Form(""), audio_chunk_threshold: str = Form(""),
                       record: str = Form("true"), ref_audio: UploadFile | None = File(None)):
        """Trang Tạo giọng nói: đọc + hiệu ứng + ghi Lịch sử."""
        from starlette.concurrency import run_in_threadpool
        prof = None
        if profile_id:
            prof = next((p for p in _load("profiles.json", []) if p.get("id") == check_id(profile_id)), None)
            if prof is None:
                raise ApiError(404, "Không còn giọng này")
        gen = {"num_step": _num(num_step, True), "guidance_scale": _num(guidance_scale), "t_shift": _num(t_shift),
               "position_temperature": _num(position_temperature), "class_temperature": _num(class_temperature),
               "layer_penalty_factor": _num(layer_penalty_factor), "denoise": denoise != "false",
               # văn bản dài: OmniVoice tự chia thành đoạn ~N giây khi ước lượng dài hơn ngưỡng
               "audio_chunk_duration": _num(audio_chunk_duration), "audio_chunk_threshold": _num(audio_chunk_threshold)}
        if postprocess_output:
            gen["postprocess_output"] = postprocess_output != "false"
        sp = _num(speed)
        tmp_ref = None
        try:
            if ref_audio is not None and not prof:
                data = await ref_audio.read()
                if data:
                    tmp_ref = to_wav_file(data, ref_audio.filename or "ref.wav",
                                          Path(tempfile.mkdtemp()) / "ref.wav")
                    if not ref_text.strip():
                        ref_text = await run_in_threadpool(transcribe, tmp_ref, language)
            audio, used_seed, gt = await run_in_threadpool(
                synthesize, text, pronounce=pronounce != "false", language=language, profile=prof,
                ref_audio=tmp_ref, ref_text=ref_text, instruct="" if (prof or tmp_ref) else instruct,
                seed=_num(seed, True) if seed.strip() else (prof or {}).get("seed"), duration=_num(duration),
                speed=sp if sp and abs(sp - 1) > 0.01 else None, gen=gen)
        finally:
            if tmp_ref is not None:
                tmp_ref.unlink(missing_ok=True)
        wav = await run_in_threadpool(apply_effect, wav_bytes(audio), effect_preset)
        mode = "clone" if (prof or tmp_ref) else "design" if instruct else "auto"
        if record == "false":           # đọc hàng loạt: không ghi Lịch sử (300 bản sẽ bị đẩy mất)
            item = {"id": "", "duration_seconds": len(audio) / SR}
        else:
            item = _record(wav, text=text, mode=mode, language=language, instruct=instruct if mode == "design" else "",
                           profile_id=prof["id"] if prof else None, seed=used_seed, gen_time=gt)
        return Response(wav, media_type="audio/wav",
                        headers={"x-audio-id": item["id"], "x-audio-duration": str(item["duration_seconds"] or ""),
                                 "x-gen-time": f"{gt:.2f}", "x-seed": str(used_seed)})

    # ---------- lịch sử ----------

    @app.get("/history")
    def history():
        return _load("history.json", [])

    @app.get("/audio/{name}")
    def history_audio(name: str):
        hid, _, ext = name.rpartition(".")
        f = DATA / "history" / f"{check_id(hid)}.wav"
        if ext != "wav" or not f.exists():
            raise ApiError(404, "Không có file")
        return Response(f.read_bytes(), media_type="audio/wav")

    @app.put("/history/{hid}/starred")
    async def star(hid: str, request: Request):
        check_id(hid)
        on = bool((await request.json()).get("starred"))
        with _db_lock:
            items = _load("history.json", [])
            for h in items:
                if h.get("id") == hid:
                    h["starred"] = on
            _save("history.json", items)
        return {"ok": True}

    @app.delete("/history/{hid}")
    def delete_history(hid: str):
        check_id(hid)
        with _db_lock:
            _save("history.json", [h for h in _load("history.json", []) if h.get("id") != hid])
        (DATA / "history" / f"{hid}.wav").unlink(missing_ok=True)
        return {"ok": True}

    @app.delete("/history")
    def clear_history():
        with _db_lock:
            items = _load("history.json", [])
            keep = [h for h in items if h.get("starred")]
            for h in items:
                if not h.get("starred"):
                    (DATA / "history" / f"{h['id']}.wav").unlink(missing_ok=True)
            _save("history.json", keep)
        return {"ok": True}

    # ---------- phát âm ----------

    @app.get("/pronunciation")
    def pron_list():
        return {"entries": _load("pronunciation.json", [])}

    @app.post("/pronunciation")
    async def pron_add(request: Request):
        b = await request.json()
        term, rep = " ".join(str(b.get("term") or "").split())[:100], " ".join(str(b.get("replacement") or "").split())[:200]
        if not term or not rep:
            raise ApiError(400, "Gõ cả từ và cách đọc")
        with _db_lock:
            items = _load("pronunciation.json", [])
            items.append({"id": _new_id(), "term": term, "replacement": rep, "type": "respelling",
                          "language": str(b.get("language") or "*")[:10], "enabled": bool(b.get("enabled", True)),
                          "created_at": time.time()})
            _save("pronunciation.json", items)
        return {"ok": True}

    @app.put("/pronunciation/{eid}")
    async def pron_toggle(eid: str, request: Request):
        check_id(eid)
        on = bool((await request.json()).get("enabled"))
        with _db_lock:
            items = _load("pronunciation.json", [])
            for e in items:
                if e.get("id") == eid:
                    e["enabled"] = on
            _save("pronunciation.json", items)
        return {"ok": True}

    @app.delete("/pronunciation/{eid}")
    def pron_delete(eid: str):
        check_id(eid)
        with _db_lock:
            _save("pronunciation.json", [e for e in _load("pronunciation.json", []) if e.get("id") != eid])
        return {"ok": True}

    @app.post("/pronunciation/test")
    async def pron_test(request: Request):
        b = await request.json()
        return {"substituted": apply_pronunciation(str(b.get("text") or "")[:1000], str(b.get("language") or "*"))}

    return app


# ============================ chuyển dữ liệu từ app VoiceStudio (1 lần) ============================

def migrate_from_voicestudio() -> int:
    """Chép giọng đã lưu + từ điển phát âm từ CSDL của app VoiceStudio (nếu có trên máy) sang data/voice — giữ
    nguyên mã + tên giọng để Cài đặt kênh đang chọn giọng đó vẫn đúng. Chạy 1 lần (dấu migrated.json)."""
    import shutil
    import sqlite3
    mark = DATA / "migrated.json"
    if mark.exists():
        return 0
    from .voice_model import _vs_snapshot
    snap = _vs_snapshot()
    roots = [snap.parents[3]] if snap else []
    roots.append(Path(r"C:\VoiceStudio\runtime\VoiceStudio"))
    db = next((r / "data" / "omnivoice.db" for r in roots if (r / "data" / "omnivoice.db").exists()), None)
    n = 0
    if db is not None:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute("SELECT * FROM voice_profiles WHERE COALESCE(is_demo,0)=0").fetchall()
            prons = con.execute("SELECT * FROM pronunciation_entries").fetchall()
        except sqlite3.Error:
            rows, prons = [], []
        finally:
            con.close()
        with _db_lock:
            items = _load("profiles.json", [])
            have = {p["id"] for p in items} | {p["name"].lower() for p in items}
            for r in rows:
                src = Path(r["ref_audio_path"] or "")
                if not src.is_absolute():            # VoiceStudio lưu tên file trong data/voices/
                    src = next((c for c in (db.parent / "voices" / src, db.parent / src) if c.is_file()), src)
                if not ID_RE.match(r["id"] or "") or r["id"] in have or (r["name"] or "").lower() in have \
                        or not src.is_file():
                    continue
                (DATA / "profiles").mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, DATA / "profiles" / f"{r['id']}.wav")
                items.append({"id": r["id"], "name": r["name"], "ref_text": r["ref_text"] or "",
                              "instruct": r["instruct"] or "", "language": r["language"] or "vi",
                              "seed": r["seed"], "kind": r["kind"] or "clone",
                              "created_at": r["created_at"] or time.time()})
                n += 1
            _save("profiles.json", items)
            pr = _load("pronunciation.json", [])
            for e in prons:
                if ID_RE.match(str(e["id"])) and not any(x["id"] == e["id"] for x in pr):
                    pr.append({"id": str(e["id"]), "term": e["term"], "replacement": e["replacement"],
                               "type": e["type"], "language": e["language"], "enabled": bool(e["enabled"]),
                               "created_at": e["created_at"]})
            _save("pronunciation.json", pr)
    _save("migrated.json", {"at": time.time(), "profiles": n, "from": str(db or "")})
    return n


# ============================ chạy ============================

def _lifeline() -> None:
    """stdin đóng (dytool thoát / bị tắt) -> thoát ngay, trả VRAM.

    Windows: KHÔNG đọc chặn stdin — 1 lệnh ReadFile đang chờ trên ống đồng bộ chặn mọi thao tác khác trên cùng ống
    (GetFileType / isatty mà thư viện gọi lúc import) -> nạp model kẹt mãi. Hỏi ống mỗi giây bằng PeekNamedPipe: ống
    bị đóng phía dytool thì báo lỗi ERROR_BROKEN_PIPE."""
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                      ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        try:
            h = msvcrt.get_osfhandle(sys.stdin.fileno())
        except (OSError, AttributeError, ValueError):
            return                       # không có stdin (chạy tay) -> không theo dõi
        avail = wintypes.DWORD()
        while True:
            if not k32.PeekNamedPipe(h, None, 0, None, ctypes.byref(avail), None):
                break                    # ống đã đóng (hoặc stdin không phải ống — lúc đó cũng thôi theo dõi)
            time.sleep(1)
        if ctypes.get_last_error() != 109:      # ERROR_BROKEN_PIPE: dytool đã đóng ống
            return
        os._exit(0)
    try:
        while sys.stdin.buffer.read(1024):
            pass
    except Exception:
        pass
    os._exit(0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=3900)
    ap.add_argument("--no-lifeline", action="store_true", help="không theo dõi stdin (chạy tay để thử)")
    args = ap.parse_args()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")          # model đã ở thư mục cục bộ, không hỏi mạng
    os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")
    os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
    DATA.mkdir(parents=True, exist_ok=True)
    try:
        n = migrate_from_voicestudio()
        if n:
            print(f"Đã chép {n} giọng từ app VoiceStudio", flush=True)
    except Exception as e:
        print(f"Chép giọng từ VoiceStudio lỗi: {e}", flush=True)
    if not args.no_lifeline:
        threading.Thread(target=_lifeline, daemon=True, name="lifeline").start()
    from . import voice_model
    if voice_model.ready():
        ENGINE.start_loading()                 # nạp sẵn: lần đọc đầu không phải chờ thêm
    import uvicorn
    uvicorn.run(build_app(), host="127.0.0.1", port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
