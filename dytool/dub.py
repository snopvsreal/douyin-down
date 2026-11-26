"""Lồng tiếng Việt: tách giọng gốc khỏi nhạc nền (Demucs) -> đọc từng câu tiếng Việt bằng VoiceStudio
(API /v1/audio/speech chạy cục bộ) -> co giãn cho khớp thời lượng câu gốc -> trộn với nhạc nền."""

import json
import subprocess
import sys
from pathlib import Path

import httpx


class DubError(RuntimeError):
    pass


# Tốc độ đọc (Cài đặt kênh › Tốc độ đọc). rate = âm tiết / giây dùng để tính bản dịch dài bao nhiêu và đọc bao lâu;
# boost = được đọc nhanh hơn rate tối đa bao nhiêu để vừa khung (ép thời lượng -> model nén cả chỗ nghỉ, nghe dồn);
# drift = giọng được trễ sau hình tối đa bao nhiêu giây. "fast" = cách cũ (tốc độ người đọc TikTok nhanh, 5,3 âm tiết / s
# + ép 1,2 lần) — người dùng 2026-11-27 chê "đọc quá nhanh, dồn, ngắt nghỉ sai chỗ" -> mặc định "normal".
PACES = {"slow": {"rate": 4.4, "boost": 1.0, "drift": 1.0, "label": "Chậm rãi"},
         "normal": {"rate": 4.8, "boost": 1.08, "drift": 0.9, "label": "Vừa"},
         "fast": {"rate": 5.29, "boost": 1.2, "drift": 0.6, "label": "Nhanh (như cũ)"}}


def pace(cfg: dict) -> dict:
    return PACES.get(str(cfg.get("pace") or "normal"), PACES["normal"])


SPEAK_TIMEOUT = 1800       # giây chờ máy chủ đọc 1 lượt nói (CPU laptop có thể vài phút / câu)


def separate(video: Path, work: Path) -> tuple[Path, Path]:
    """Tách thành (giọng nói, nhạc nền) bằng Demucs htdemucs — cùng model VoiceStudio dùng nội bộ."""
    out_dir = work / "sep"
    wav = work / f"{video.stem}_src.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "2", "-ar", "44100",
                    str(wav)], check=True)
    base = [sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs", "-o", str(out_dir)]
    from .services import voice_device
    if voice_device() == "cpu":            # người dùng chọn chạy lồng tiếng bằng CPU (Cài đặt) -> không đụng card
        base += ["-d", "cpu"]
    # Demucs tự dùng GPU nếu torch có CUDA (bản cài "gpu", máy có card NVIDIA). Lỗi phía GPU (hết VRAM — card 4 GB
    # dùng chung với VoiceStudio, driver…) -> chạy lại bằng CPU thay vì làm hỏng cả video
    r = subprocess.run(base + [str(wav)], capture_output=True)
    if r.returncode != 0:
        from . import progress
        progress.check()               # bị nút Dừng tắt -> không chạy lại bằng CPU
        err = r.stderr.decode("utf-8", "replace")
        if any(w in err for w in ("CUDA", "cuda", "cuDNN", "out of memory")):
            print("Demucs lỗi trên GPU -> chạy lại bằng CPU", flush=True)
            r = subprocess.run(base + ["-d", "cpu", str(wav)], capture_output=True)
        if r.returncode != 0:
            raise subprocess.CalledProcessError(r.returncode, r.args, r.stdout, r.stderr)
    stem_dir = out_dir / "htdemucs" / wav.stem
    wav.unlink(missing_ok=True)
    return stem_dir / "vocals.wav", stem_dir / "no_vocals.wav"


def _duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True, check=True).stdout
    return float(out.strip() or 0)


def page_voices(s: dict) -> dict:
    """Giọng nam / nữ của 1 kênh (cài đặt kênh): chọn sẵn trong thư viện VoiceStudio -> dùng đúng giọng đó; không
    thì giọng thiết kế theo mô tả + seed — tên gắn với nội dung mô tả nên đổi mô tả / seed là tự tạo giọng mới."""
    import hashlib
    seed = int(s.get("voice_seed") or 7)
    tag = hashlib.md5(f"{s.get('voice_male', '')}|{s.get('voice_female', '')}|{seed}".encode()).hexdigest()[:6]
    out = {"male": {"name": f"dy-nam-{tag}", "instruct": s.get("voice_male", ""), "seed": seed},
           "female": {"name": f"dy-nu-{tag}", "instruct": s.get("voice_female", ""), "seed": seed + 4}}
    for g in ("male", "female"):
        picked = (s.get(f"voice_{g}_profile") or "").strip()
        if picked:
            out[g] = {"name": picked, "fixed": True}
    return out


class VoiceStudio:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        from .services import vs_api
        self.api = vs_api(cfg)
        # máy chủ ở ngay máy này: chết thật thì kết nối đứt ngay (ReadError), không đứng chờ -> chờ đọc rất lâu được.
        # Đọc bằng CPU trên laptop 1 câu có thể quá 5 phút (người dùng 2026-11-27: "không trả lời: timed out")
        self.http = httpx.Client(timeout=httpx.Timeout(SPEAK_TIMEOUT, connect=10.0, write=60.0, pool=60.0))
        self.voice = cfg.get("voice", "dytool-vi")

    def check(self) -> None:
        try:
            self.http.get(self.api + "/health", timeout=5).raise_for_status()
        except httpx.HTTPError as e:
            raise DubError(f"Không kết nối được máy chủ giọng nói tại {self.api}. Bật ở chip Giọng nói trên cùng rồi thử lại.") from e

    def _post_waiting(self, body: dict) -> httpx.Response:
        """Gửi yêu cầu đọc ở luồng phụ, luồng này chờ từng giây: bấm Dừng có tác dụng ngay (không phải đợi máy chủ
        đọc xong), bảng tiến trình hiện đã chờ bao lâu. Bị Dừng thì bỏ mặc yêu cầu — máy chủ đọc nốt rồi thôi."""
        import threading
        import time
        from . import progress
        box: dict = {}

        def go():
            try:
                box["r"] = self.http.post(self.api + "/v1/audio/speech", json=body)
            except BaseException as e:       # noqa: BLE001 — chuyển sang luồng chính
                box["e"] = e
        th = threading.Thread(target=go, daemon=True, name="vs-speak")
        t0 = time.time()
        th.start()
        base = progress.STATE.get("task", {}) or {}
        base = base.get("detail", "") if isinstance(base, dict) else ""
        while th.is_alive():
            th.join(1.0)
            progress.check()
            waited = time.time() - t0
            if waited >= 20 and int(waited) % 5 == 0:
                progress.detail(f"{base} · đang đọc {int(waited)} s" if base else f"đang đọc {int(waited)} s")
        if "e" in box:
            raise box["e"]
        return box["r"]

    def _speech(self, body: dict) -> httpx.Response:
        """POST /v1/audio/speech. Card đồ hoạ lỗi -> máy chủ trả 503 + x-restart rồi tự thoát để chạy lại bằng CPU
        (hoặc chết ngang lúc đọc — kết nối đứt): bật lại máy chủ, chờ nạp model, gửi lại 1 lần.
        Hết giờ chờ (SPEAK_TIMEOUT) KHÔNG phải máy chủ chết mà là đang bận đọc: không gửi lại — câu gửi lại phải xếp
        hàng sau câu cũ vẫn đang đọc (bản 0.5.2 làm vậy -> lỗi "không trả lời: timed out" lần 2)."""
        for attempt in (0, 1):
            try:
                r = self._post_waiting(body)
            except httpx.TimeoutException as e:
                raise DubError(f"Máy chủ giọng nói đọc 1 câu quá {SPEAK_TIMEOUT // 60:.0f} phút — máy đọc bằng CPU "
                               "quá chậm. Xem thời gian đọc từng câu ở Nhật ký trang Giọng nói") from e
            except httpx.TransportError as e:
                if attempt:
                    raise DubError(f"Máy chủ giọng nói không trả lời: {e}") from e
                self._wait_restart()
                continue
            if r.status_code == 503 and r.headers.get("x-restart") and not attempt:
                self._wait_restart()
                continue
            return r
        return r

    def _wait_restart(self, timeout: float = 420) -> None:
        """Chờ máy chủ cũ thoát, bật lại (services), chờ model nạp xong. Không do dytool bật -> chỉ chờ nó lên lại."""
        import time
        from . import progress, services
        progress.detail("Card đồ hoạ lỗi — đang khởi động lại giọng nói bằng CPU…")
        end = time.time() + 20                      # máy chủ tự thoát ~1 s sau khi trả lời
        while services.vs_ours_running() and time.time() < end:
            progress.check()
            time.sleep(0.5)
        end = time.time() + timeout
        while time.time() < end:
            progress.check()
            r = services.start_voicestudio(self.cfg)
            if r == "remote":
                r = services.vs_state(self.cfg)
            if r == "ok":
                try:
                    m = self.http.get(self.api + "/model/status", timeout=5).json()
                    if m.get("loaded"):
                        return
                    if m.get("state") == "error" and m.get("error"):
                        raise DubError(f"Máy chủ giọng nói không nạp được model: {m['error']}")
                except (httpx.HTTPError, ValueError):
                    pass
            elif r in ("crashed", "missing"):
                raise DubError("Máy chủ giọng nói bật lại không được (xem Nhật ký ở trang Giọng nói)")
            time.sleep(2)
        raise DubError("Máy chủ giọng nói khởi động lại quá lâu")

    REF_TEXT = ("Xin chào các bạn, hôm nay mình sẽ kể cho các bạn nghe một câu chuyện rất thú vị. "
                "Nhớ theo dõi đến cuối video nhé.")

    def voice_spec(self, gender: str | None) -> dict:
        """Chọn cấu hình giọng theo giới tính (voices.male / voices.female), không có thì dùng giọng chung."""
        voices = self.cfg.get("voices") or {}
        if gender in voices:
            return voices[gender]
        return {"name": self.cfg.get("voice", "dytool-vi"), "instruct": self.cfg.get("voice_instruct", ""),
                "seed": self.cfg.get("voice_seed", 42), "ref_audio": self.cfg.get("voice_ref_audio", ""),
                "ref_text": self.cfg.get("voice_ref_text", "")}

    def ensure_voice(self, work: Path, spec: dict) -> str:
        """Đảm bảo có voice profile cố định. Không có giọng mẫu thì OmniVoice sinh giọng NGẪU NHIÊN mỗi lần gọi
        -> mỗi câu một giọng. Nên: tạo 1 câu mẫu (thiết kế theo instruct + seed cố định, hoặc file giọng của bạn)
        rồi clone giọng đó cho mọi câu."""
        name = spec["name"]
        profiles = self.http.get(self.api + "/profiles").json()
        profiles = profiles.get("profiles", profiles) if isinstance(profiles, dict) else profiles
        if any(p.get("name", "").lower() == name.lower() or p.get("id") == name for p in profiles):
            return name
        if spec.get("fixed"):        # giọng chọn sẵn trong thư viện (Cài đặt kênh) đã bị xoá -> không tự sinh giọng khác
            raise DubError(f"Giọng '{name}' không còn trong Giọng đã lưu: chọn giọng khác ở Cài đặt kênh › Edit & lồng tiếng")
        ref_path = spec.get("ref_audio")          # file giọng mẫu của bạn (tuỳ chọn, cần có quyền dùng giọng)
        ref_text = spec.get("ref_text", "")
        if not ref_path:
            ref_text = self.REF_TEXT
            body = {"model": self.cfg.get("model", "omnivoice"), "voice": "default", "input": ref_text,
                    "language": "vi", "seed": int(spec.get("seed", 42)), "response_format": "wav"}
            if spec.get("instruct"):
                body["instructions"] = spec["instruct"]
            r = self._speech(body)
            if r.status_code != 200:
                raise DubError(f"Không tạo được giọng mẫu: {r.text[:300]}")
            ref_path = work / f"{name}_ref.wav"
            Path(ref_path).write_bytes(r.content)
        with open(ref_path, "rb") as f:
            r = self.http.post(self.api + "/profiles", data={"name": name, "ref_text": ref_text, "language": "vi",
                                                              "kind": "clone", "instruct": spec.get("instruct", "")},
                               files={"ref_audio": (Path(ref_path).name, f, "audio/wav")})
        if r.status_code >= 300:
            raise DubError(f"Không tạo được voice profile: {r.text[:300]}")
        return name

    def speak(self, text: str, dest: Path, duration: float | None = None, voice: str | None = None) -> Path:
        from .phrasing import with_pauses
        body = {
            "model": self.cfg.get("model", "omnivoice"),
            "voice": voice or self.voice,
            "input": with_pauses(text),          # nghỉ thật giữa các câu trong lượt nói
            "language": self.cfg.get("language", "vi"),
            "seed": int(self.cfg.get("voice_seed", 42)),
            "response_format": "wav",
        }
        # Truyền thời lượng mong muốn: không có thì OmniVoice ước lượng thiếu cho tiếng Việt -> CỤT CHỮ CUỐI.
        if duration:
            body["duration"] = round(duration, 2)
        if self.cfg.get("num_step"):
            body["num_step"] = int(self.cfg["num_step"])
        r = self._speech(body)
        if r.status_code != 200:
            raise DubError(f"Máy chủ giọng nói lỗi {r.status_code}: {r.text[:300]}")
        raw = dest.with_suffix(".raw.wav")
        raw.write_bytes(r.content)
        # chỉ cắt lặng ĐẦU; đuôi chỉ bỏ phần im lặng hẳn (-60dB) dài hơn 0.15s để giữ âm cuối tắt dần
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af",
                        "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.02,"
                        "areverse,silenceremove=start_periods=1:start_threshold=-60dB:start_silence=0.15,areverse",
                        str(dest)], check=True)
        raw.unlink(missing_ok=True)
        return dest


def group_utterances(segments: list[dict], max_gap: float = 0.35, max_len: float = 10.0) -> list[dict]:
    """Gom các đoạn Whisper liên tiếp thành 1 LƯỢT NÓI để đọc liền một hơi:
    khoảng lặng giữa 2 đoạn < max_gap (người nói chưa dừng) và tổng độ dài <= max_len."""
    utts: list[dict] = []
    for s in segments:
        if utts and s["start"] - utts[-1]["end"] < max_gap and s["end"] - utts[-1]["start"] <= max_len:
            u = utts[-1]
            u["end"] = s["end"]
            u["text"] += s["text"]
            u["asr"] += s.get("asr", "")
            u["parts"].append(s)
        else:
            utts.append({"start": s["start"], "end": s["end"], "text": s["text"], "asr": s.get("asr", ""),
                         "parts": [s]})
    return utts


def make_line(vs: VoiceStudio, text: str, window: float, clip: Path, cfg: dict, sub_cfg: dict,
              voice: str) -> tuple[str, float]:
    """Đọc 1 lượt nói vừa `window` giây mà KHÔNG tua/cắt audio:
    thời lượng tự nhiên (theo số liệu giọng thật) -> cho phép đọc nhanh hơn tối đa max_rate_boost
    (qua tham số duration của VoiceStudio) -> vẫn dài thì nhờ Claude rút gọn rồi đọc lại."""
    from . import phrasing
    from .subtitles import shorten
    p = pace(cfg)
    rate, boost = p["rate"], p["boost"]
    for attempt in range(int(cfg.get("max_shorten", 2)) + 1):
        text = phrasing.fix_commas(text)
        nat = phrasing.natural_duration(text, rate)
        dur = max(min(nat, window), nat / boost)
        if dur <= window + 0.05 or attempt == int(cfg.get("max_shorten", 2)):
            break
        text = shorten(text, phrasing.max_syllables(window * boost, rate), sub_cfg)
    d = _duration(vs.speak(text, clip, dur, voice))
    return text, d


def split_cues(words: list[str], max_chars: int = 34, comma_chars: int = 14) -> list[list[int]]:
    """Chia câu thành các cụm phụ đề (mỗi cụm ≤ ~2 dòng): luôn tách ở cuối câu (. ! ? …), tách ở dấu phẩy
    khi cụm đã đủ dài, cụm quá max_chars thì lùi về chỗ ngắt đẹp nhất (dấu phẩy > trước chữ nối > không
    cắt đôi từ ghép). Không để cụm cuối chỉ 1-2 chữ."""
    from .subtitles import _LEAD, splits_word
    out, cur = [], []

    def chars(idx):
        return sum(len(words[k]) + 1 for k in idx) - 1

    for i, w in enumerate(words):
        cur.append(i)
        left = len(words) - 1 - i
        if left == 0:
            break
        if left <= 2:
            continue
        if (w[-1] in ".!?…" and len(cur) >= 2) or (w[-1] in ",;:" and chars(cur) >= comma_chars):
            out.append(cur)
            cur = []
        elif chars(cur) > max_chars and len(cur) >= 4:
            # quá dài: chọn chỗ ngắt tốt nhất trong nửa sau cụm
            best, score = len(cur) - 1, -1
            for p in range(len(cur) // 2, len(cur)):
                j = cur[p]                          # ngắt TRƯỚC words[j]
                s = 3 if words[j - 1][-1] in ",;:" else 2 if words[j].lower() in _LEAD else \
                    0 if splits_word(words, j) else 1
                if s >= score:
                    best, score = p, s
            out.append(cur[:best])
            cur = cur[best:]
    if cur:
        out.append(cur)
    return [x for x in out if x]


def align_subtitles(clip: Path, offset: float, text: str) -> list[dict]:
    """Phụ đề khớp đúng giọng đọc: Whisper nghe lại clip TTS lấy mốc thời gian từng từ, chia cụm bằng
    split_cues. Ngắt dòng trong cụm do subtitles.write_ass làm theo độ rộng chữ. Không nhận dạng được
    thì chia đều."""
    from .subtitles import word_timestamps
    words = text.split()
    times = word_timestamps(clip, len(words))
    lines = split_cues(words)
    out = []
    for idx in lines:
        t0, t1 = times[idx[0]][0], times[idx[-1]][1]
        out.append({"start": offset + t0, "end": offset + t1,
                    "text": " ".join(words[k] for k in idx).rstrip(",;:")})
    return out


BG_UNDER_DB = 10.0         # nhạc nền phải nhỏ hơn giọng đọc ít nhất bấy nhiêu dB (mức trung bình)


def _mean_db(paths) -> float | None:
    """Mức trung bình (dBFS) của các file âm thanh, cộng theo năng lượng, có trọng số độ dài (FFmpeg volumedetect)."""
    import math
    import re
    tot_e = tot_t = 0.0
    for p in paths:
        try:
            r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(p), "-af", "volumedetect", "-f", "null",
                                "-"], capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired):
            continue
        # FFmpeg in 2 khối kết quả (khối đầu "n_samples: 0") -> lấy khối cuối
        m = re.findall(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr or "")
        d = re.findall(r"n_samples:\s*(\d+)", r.stderr or "")
        if not m:
            continue
        w = float(d[-1]) if d else 1.0
        tot_e += w * 10 ** (float(m[-1]) / 10)
        tot_t += w
    return 10 * math.log10(tot_e / tot_t) if tot_t and tot_e > 0 else None


def voice_spans(vocals: Path, coarse) -> list[tuple[float, float]]:
    """Khúc có tiếng NGƯỜI trong phần giọng Demucs tách ra: VAD Silero (có sẵn trong faster-whisper) từng khúc ngắn,
    chỉ lấy phần nằm trong các câu Whisper nghe được (nới 0,3 s) — ngoài câu nói thì dù VAD bắt nhầm (mèo kêu) vẫn
    giữ. Câu Whisper gồm cả khoảng lặng / tiếng mèo giữa các từ; tắt cả câu thì mất tiếng mèo lúc đang lồng tiếng
    (người dùng 2026-11-27). Lỗi VAD -> dùng nguyên các câu Whisper."""
    coarse = [(float(a), float(b)) for a, b in coarse]
    if not coarse:
        return []
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        from .subtitles import load_pcm
        audio = load_pcm(vocals)
        ts = get_speech_timestamps(audio, VadOptions(threshold=0.5, min_speech_duration_ms=100,
                                                     min_silence_duration_ms=150, speech_pad_ms=50))
        vad = [(t["start"] / 16000, t["end"] / 16000) for t in ts]
    except Exception as e:                    # noqa: BLE001 — VAD chỉ để giữ thêm tiếng mèo, lỗi thì như cũ
        print(f"VAD lỗi, tắt tiếng gốc theo cả câu: {e}", flush=True)
        return coarse
    out = []
    for a, b in vad:
        for c, d in coarse:
            lo, hi = max(a, c - 0.3), min(b, d + 0.3)
            if hi - lo > 0.05:
                out.append((round(lo, 3), round(hi, 3)))
    return out


def mute_expr(spans, pad_before: float = 0.15, pad_after: float = 0.3, fade: float = 0.12,
              floor: float = 0.0) -> str:
    """Biểu thức FFmpeg `volume` (eval=frame): 1 ngoài các đoạn có người nói, `floor` trong đoạn, mép chuyển dần
    trong `fade` giây. Các đoạn (đã nới pad) chồng / sát nhau thì gộp — biểu thức ngắn lại."""
    iv = sorted((max(0.0, float(a) - pad_before), float(b) + pad_after) for a, b in spans if float(b) > float(a))
    merged: list[list[float]] = []
    for a, b in iv:
        if merged and a <= merged[-1][1] + 2 * fade:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    if not merged:
        return "1"
    f = max(0.01, fade)
    # w = 1 trong [a, b], giảm dần về 0 trong f giây ngoài mép; gain = tích (1 - w)
    terms = [f"(1-clip((t-{a - f:.3f})/{f:.3f},0,1)*clip(({b + f:.3f}-t)/{f:.3f},0,1))" for a, b in merged]
    g = "*".join(terms)
    return f"max({floor:.3f},{g})" if floor > 0 else g


def dub_audio(video: Path, segments: list[dict], work: Path, cfg: dict,
              sub_cfg: dict | None = None, steps=None, speech_spans=None) -> tuple[Path, list[dict]]:
    """Lồng tiếng Việt. Trả về (file .m4a gồm nhạc nền + giọng Việt, các dòng phụ đề khớp giọng đọc).
    1. Gom đoạn Whisper thành lượt nói (đọc liền), nhận giọng nam/nữ từng lượt.
    2. Claude dịch từng lượt, có giới hạn âm tiết và quy tắc ngắt nghỉ học từ giọng đọc thật.
    3. OmniVoice đọc CẢ lượt trong 1 lần (ngữ điệu liền mạch), thời lượng = tốc độ người Việt thật.
    4. Xếp lịch tuần tự: không chồng giọng, nghỉ giữa các lượt như người thật, trễ tối đa max_drift.
    steps (stepcache.Steps): bước nào xong ở lần trước (Demucs, nam / nữ, dịch, từng câu đọc) thì dùng lại."""
    from . import phrasing
    from .gender import detect_genders
    from .stepcache import NoSteps
    from .subtitles import translate_utterances
    from .editor import _ai_cfg
    sub_cfg = sub_cfg or {}
    steps = steps or NoSteps()
    vs = VoiceStudio(cfg)
    vs.check()
    from . import progress
    sep = steps.run("demucs", [], lambda: [str(p) for p in separate(video, work)],
                    label="Tách giọng gốc khỏi nhạc nền (Demucs)", files=lambda v: v)
    vocals, background = Path(sep[0]), Path(sep[1])
    total = _duration(video)
    utts = group_utterances(segments, float(cfg.get("merge_gap", 0.35)), float(cfg.get("max_utterance", 10.0)))
    mode = cfg.get("gender", "auto")
    if mode == "auto":
        genders = steps.run("gender", [[[u["start"], u["end"]] for u in utts]],
                            lambda: detect_genders(vocals, utts), label="Nhận giọng nam/nữ")
    else:
        genders = [mode] * len(utts)
    voice_names = {g: vs.ensure_voice(work, vs.voice_spec(g)) for g in set(genders)}

    pc = pace(cfg)
    max_drift = pc["drift"]
    windows = [((utts[i + 1]["start"] if i + 1 < len(utts) else total) - u["start"]) for i, u in enumerate(utts)]
    src_lines, asr = [u["text"] for u in utts], [u["asr"] for u in utts]
    # bản dịch dài vừa tốc độ đọc đã chọn (đọc chậm hơn -> câu dịch gọn hơn), không phải đọc ép
    texts = steps.run("translate_dub", [src_lines, windows, asr, _ai_cfg(sub_cfg), pc["rate"]],
                      lambda: translate_utterances(src_lines, windows, sub_cfg, asr=asr, rate=pc["rate"]),
                      label=f"Dịch sang tiếng Việt (Claude) · {len(utts)} lượt nói")
    progress.step("Lồng tiếng (OmniVoice)")

    clip_dir = work / f"{video.stem}_tts"
    clip_dir.mkdir(exist_ok=True)
    inputs, filters, labels, subs, plan = ["-i", str(background)], [], [], [], []
    cursor = 0.0
    for i, (u, text) in enumerate(zip(utts, texts)):
        if not text.strip():
            continue
        start = max(u["start"], cursor)
        if start >= total - 0.3:
            # các câu trước đọc dài đẩy câu này ra sát / quá cuối video: bỏ câu (trước: atrim độ dài âm -> ffmpeg lỗi
            # cả video)
            progress.detail(f"bỏ câu {i + 1}: hết thời lượng video")
            continue
        nxt = utts[i + 1]["start"] if i + 1 < len(utts) else total
        window = max(0.4, min(total, nxt + max_drift) - start - phrasing.COMMA_PAUSE * 0.5)
        clip = clip_dir / f"{i:03d}.wav"
        progress.detail(f"câu {i + 1}/{len(utts)}: {text[:60]}")
        voice = voice_names[genders[i]]
        # câu đã đọc ở lần trước (cùng chữ, khung thời gian, giọng, cài đặt đọc) -> dùng lại clip + mốc phụ đề
        tts_cfg = {k: cfg.get(k) for k in ("pace", "max_shorten", "num_step", "language", "voice_seed", "model")}
        tts_cfg["v"] = 2                     # 2 = có [pause] giữa câu (clip đọc ở bản cũ không dùng lại)

        def read_line(text=text, window=window, clip=clip, voice=voice, start=start):
            t, dd = make_line(vs, text, window, clip, cfg, sub_cfg, voice)
            return {"text": t, "d": dd, "subs": align_subtitles(clip, start, t)}
        line = steps.run(f"tts_{i:03d}", [text, round(window, 3), round(start, 3), voice, tts_cfg, _ai_cfg(sub_cfg)],
                         read_line, files=lambda v, clip=clip: [clip])
        text, d = line["text"], line["d"]
        d = min(d, total - start)
        if d <= 0.05:
            continue
        n = len(inputs) // 2
        inputs += ["-i", str(clip)]
        delay = int(start * 1000)
        filters.append(f"[{n}:a]atrim=0:{d:.3f},aresample=44100,adelay={delay}|{delay}[v{n}]")
        labels.append(f"[v{n}]")
        subs += line["subs"]
        plan.append({"start": round(start, 2), "end": round(start + d, 2), "src": [round(u["start"], 2), round(u["end"], 2)],
                     "zh": u["text"], "vi": text, "gender": genders[i], "voice": voice_names[genders[i]],
                     "rate_syl_s": round(phrasing.syl(text) / max(d, 0.1), 2)})
        # nghỉ giữa 2 lượt nói như người thật (~0.34s), nhưng không sớm hơn hình gốc
        cursor = start + d + phrasing.COMMA_PAUSE * 0.5

    progress.step("Trộn giọng Việt với nhạc nền")
    out = work / f"{video.stem}_dub.m4a"
    bg_vol = float(cfg.get("background_volume", 0.9))
    voice_vol = float(cfg.get("voice_volume", 1.0))
    keep = float(cfg.get("original_voice_volume", 0.0))
    sfx = cfg.get("keep_sfx", True)
    duck = cfg.get("duck_bg", True) and bool(labels)
    thr_bg, thr_sfx = 0.03, 0.05           # ngưỡng ducking (biên độ giọng Việt); đo được mức giọng thì tính theo nó
    if duck:
        # nhạc nền to hơn giọng đọc (người dùng 2026-11-27: "nhạc nền to quá thì giảm bớt") -> hạ cho thấp hơn giọng
        # BG_UNDER_DB (so mức trung bình: nền cả bài, giọng = các câu đã đọc); không bao giờ tăng nhạc nền
        v_db = _mean_db([Path(x) for x in inputs[3::2]])
        b_db = _mean_db([background])
        if v_db is not None:
            # giọng đọc nhỏ / to khác nhau theo giọng mẫu -> ngưỡng theo mức giọng thật: nhạc nền nhỏ xuống ~9 dB
            # khi giọng ở mức trung bình (dưới giọng 14 dB, tỉ lệ 3), tiếng mèo chỉ ~3 dB (dưới 6 dB, tỉ lệ 2)
            v_lin = 10 ** (v_db / 20) * voice_vol
            thr_bg, thr_sfx = max(1e-4, v_lin * 10 ** (-14 / 20)), max(1e-4, v_lin * 10 ** (-6 / 20))
        if v_db is not None and b_db is not None:
            import math
            over = ((b_db + 20 * math.log10(max(bg_vol, 1e-4)))
                    - (v_db + 20 * math.log10(max(voice_vol, 1e-4)) - BG_UNDER_DB))
            if over > 0.5:
                bg_vol *= 10 ** (-over / 20)
                progress.detail(f"nhạc nền to hơn giọng đọc — giảm {over:.0f} dB")
    parts = list(filters) + [f"[0:a]volume={bg_vol:.4f}[bg0]"]
    bed = ["[bg0]"]
    if sfx:
        # Demucs xếp tiếng mèo kêu, tiếng cười, tiếng hát… vào phần "giọng" -> trước đây bỏ cả phần giọng thì mất
        # luôn tiếng mèo. Giờ trả phần giọng gốc lại, CHỈ tắt đúng các khúc có tiếng người (VAD trong câu Whisper
        # nghe được — tiếng mèo xen giữa các từ vẫn còn), mép mờ dần. keep > 0 = vẫn để lọt chút giọng Trung.
        coarse = speech_spans if speech_spans is not None else [(s["start"], s["end"]) for s in segments]
        fine = steps.run("vad", [[[round(float(a), 3), round(float(b), 3)] for a, b in coarse]],
                         lambda: [list(x) for x in voice_spans(vocals, coarse)])
        inputs += ["-i", str(vocals)]
        k = len(inputs) // 2 - 1
        gain = mute_expr(fine, pad_before=0.06, pad_after=0.12, fade=0.08, floor=keep)
        parts.append(f"[{k}:a]volume='{gain}':eval=frame,volume={float(cfg.get('sfx_volume', 1.0))}[sfx0]")
        bed.append("[sfx0]")
    elif keep > 0:     # giữ lại chút giọng gốc nếu muốn
        inputs += ["-i", str(vocals)]
        k = len(inputs) // 2 - 1
        parts.append(f"[{k}:a]volume={keep}[orig]")
        labels.append("[orig]")
    if labels:
        # giọng Việt kéo đủ dài cả video (apad) để làm tín hiệu điều khiển ducking không cắt cụt nền
        vb = f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0,volume={voice_vol},apad=whole_dur={total:.3f}"
        if duck:
            n_sc = len(bed)
            parts.append(vb + f",asplit={n_sc + 1}[voice]" + "".join(f"[sc{j}]" for j in range(n_sc)))
            # nhạc nền nhỏ xuống khi giọng Việt đang đọc, tiếng mèo chỉ hơi nhỏ, hết câu thì lên lại
            parts.append(f"[bg0][sc0]sidechaincompress=threshold={thr_bg:.5f}:ratio=3:attack=20:release=400:knee=4[bgd]")
            bed[0] = "[bgd]"
            if len(bed) > 1:
                parts.append(f"[sfx0][sc1]sidechaincompress=threshold={thr_sfx:.5f}:ratio=2:attack=20:release=300:knee=4[sfxd]")
                bed[1] = "[sfxd]"
        else:
            parts.append(vb + "[voice]")
    if len(bed) > 1:
        parts.append(f"{''.join(bed)}amix=inputs={len(bed)}:normalize=0:duration=first[bed]")
    else:
        parts.append(f"{bed[0]}anull[bed]")
    parts.append("[bed][voice]amix=inputs=2:normalize=0:duration=first[out]" if labels else "[bed]anull[out]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(parts),
                    "-map", "[out]", "-t", f"{total:.3f}", "-c:a", "aac", "-b:a", "160k", str(out)], check=True)
    (work / f"{video.stem}_dub.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    return out, subs
