"""Model OmniVoice (github.com/k2-fsa/OmniVoice, Apache-2.0) cho lồng tiếng — tải về data/models/omnivoice.

Ghim đúng 1 bản trên Hugging Face (REVISION) và kiểm tra từng file: file lớn (LFS) theo SHA256, file nhỏ theo mã
blob git (SHA1) — sai là xoá, không dùng. Máy đã có app VoiceStudio tải sẵn đúng bản này -> tạo liên kết cứng (hard
link, không tốn thêm ổ đĩa) thay vì tải lại 3,3 GB. Tiến độ ghi ra data/voice_model.json cho giao diện."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "data" / "models" / "omnivoice"
STATE = ROOT / "data" / "voice_model.json"
REPO = "k2-fsa/OmniVoice"
# Bản có sẵn audio_tokenizer bên trong (bản main mới hơn bỏ thư mục này -> phải tải thêm repo khác)
REVISION = "c5fdb5ccb189668d56333f77ba2629f4cd7535f4"
# (đường dẫn, cỡ, "sha256:…" cho file LFS | "git:…" mã blob git)
FILES = (
    ("config.json", 2238, "git:dd518e816a5c4c8eba90820bb7efa49ceafbe223"),
    ("chat_template.jinja", 4168, "git:01be9b307daa2d425f7c168c9fb145a286e0afb4"),
    ("tokenizer_config.json", 533, "git:a7fb6f8558cce2a23d4cbb186a4a740bd2d9d481"),
    ("tokenizer.json", 11423986, "sha256:408f669b7e2b045fdf54201d815bd364e6667dbd845115da81239c40bc6dcfd1"),
    ("audio_tokenizer/config.json", 2531, "git:3f9e5ee43d2134dfdc1e38055fbcb24a18af1635"),
    ("audio_tokenizer/preprocessor_config.json", 206, "git:aa7431e986f9e13157c60e04be8f52501b718254"),
    ("audio_tokenizer/LICENSE", 9171, "git:000139f87ffdc03106fc422a3bd3cebebb9808b3"),
    ("audio_tokenizer/model.safetensors", 805665628,
     "sha256:fe7c5e8785e0a05833e1bfc3e002ec7f55af21e306b2e7154a448c1f54ccfb0d"),
    ("model.safetensors", 2450344112, "sha256:730839316de585f4c8298ec0e1712efc10fb19c6fa4e36eb741cb8d51ebcf6aa"),
)
TOTAL = sum(f[1] for f in FILES)
_READY = MODEL_DIR / ".ready"


class ModelError(RuntimeError):
    pass


def ready() -> bool:
    return (_READY.exists() and _READY.read_text("utf-8").strip() == REVISION
            and all((MODEL_DIR / p).is_file() and (MODEL_DIR / p).stat().st_size == n for p, n, _ in FILES))


def _digest(path: Path, want: str) -> bool:
    kind, hexd = want.split(":", 1)
    if kind == "sha256":
        h = hashlib.sha256()
    else:                                     # mã blob git = sha1("blob <cỡ>\0" + nội dung)
        h = hashlib.sha1()
        h.update(f"blob {path.stat().st_size}\0".encode())
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest() == hexd


def _vs_snapshot() -> Path | None:
    """Bản model app VoiceStudio đã tải (cùng REVISION) trên máy này, hoặc None."""
    roots = [Path(r"C:\VoiceStudio\runtime\VoiceStudio")]
    try:
        loc = json.loads((Path(os.environ.get("APPDATA", "")) / "VoiceStudio" / "runtime-location.json")
                         .read_text("utf-8-sig"))
        if isinstance(loc.get("root"), str) and loc["root"].strip():
            roots.insert(0, Path(loc["root"].strip()))
    except (OSError, ValueError, AttributeError):
        pass
    for r in roots:
        snap = r / "models" / "models--k2-fsa--OmniVoice" / "snapshots" / REVISION
        if (snap / "model.safetensors").exists():
            return snap
    return None


# ---------------- tiến độ (đọc bởi giao diện) ----------------

_lock = threading.Lock()
_thread: threading.Thread | None = None


def state() -> dict:
    """{'phase': 'ready'|'missing'|'downloading'|'verifying'|'error', 'done': byte, 'total': byte, 'error': str}."""
    if ready():
        return {"phase": "ready", "done": TOTAL, "total": TOTAL}
    try:
        s = json.loads(STATE.read_text("utf-8"))
        if s.get("phase") in ("downloading", "verifying") and not (_thread and _thread.is_alive()) \
                and time.time() - float(s.get("at", 0)) > 30:
            s["phase"] = "missing"            # tiến trình tải lần trước đã thoát giữa chừng
        return s
    except (OSError, ValueError):
        return {"phase": "missing", "done": 0, "total": TOTAL}


def _put(**kw) -> None:
    kw.setdefault("total", TOTAL)
    kw["at"] = time.time()
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(kw, ensure_ascii=False), "utf-8")
        os.replace(tmp, STATE)
    except OSError:
        pass


# ---------------- tải ----------------

def ensure(progress=None) -> Path:
    """Có model (đủ file, đúng mã băm) trong MODEL_DIR; chưa có thì lấy từ VoiceStudio (liên kết cứng) hoặc tải.
    progress(done, total) tuỳ chọn. Trả MODEL_DIR. Lỗi -> ModelError (lời nhắn tiếng Việt)."""
    if ready():
        return MODEL_DIR
    with _lock:
        if ready():
            return MODEL_DIR
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        _READY.unlink(missing_ok=True)
        snap = _vs_snapshot()
        done = 0
        report = progress or (lambda d, t: None)
        try:
            for rel, size, want in FILES:
                dest = MODEL_DIR / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.is_file() and dest.stat().st_size == size and _digest(dest, want):
                    done += size
                    continue
                dest.unlink(missing_ok=True)
                src = snap / rel if snap else None
                if src is not None and src.is_file() and src.stat().st_size == size:
                    _put(phase="verifying", done=done, file=rel)
                    try:
                        os.link(src, dest)            # cùng ổ -> không tốn thêm chỗ
                    except OSError:
                        shutil.copyfile(src, dest)
                    if _digest(dest, want):
                        done += size
                        report(done, TOTAL)
                        continue
                    dest.unlink(missing_ok=True)
                done = _download(rel, size, want, dest, done, report)
        except ModelError as e:
            _put(phase="error", done=done, error=str(e))
            raise
        except Exception as e:
            _put(phase="error", done=done, error=f"{type(e).__name__}: {e}")
            raise ModelError(f"Không tải được model giọng nói: {e}") from e
        _READY.write_text(REVISION, "utf-8")
        _put(phase="ready", done=TOTAL)
        return MODEL_DIR


def _download(rel: str, size: int, want: str, dest: Path, done: int, report) -> int:
    url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{rel}"
    part = dest.with_name(dest.name + ".part")
    for attempt in range(4):
        have = part.stat().st_size if part.exists() else 0
        if have > size:
            part.unlink()
            have = 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with httpx.stream("GET", url, headers=headers, follow_redirects=True,
                              timeout=httpx.Timeout(60, connect=20)) as r:
                if r.status_code == 200 and have:          # máy chủ không cho tải tiếp -> tải lại từ đầu
                    have = 0
                elif r.status_code not in (200, 206):
                    raise ModelError(f"Hugging Face trả lỗi {r.status_code} khi tải {rel}")
                last = 0.0
                with open(part, "ab" if have else "wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
                        have += len(chunk)
                        if time.time() - last > 0.5:
                            last = time.time()
                            _put(phase="downloading", done=done + have, file=rel)
                            report(done + have, TOTAL)
            if have == size:
                break
        except (httpx.HTTPError, OSError) as e:
            if attempt == 3:
                raise ModelError(f"Mạng lỗi khi tải {rel}: {e} — thử lại sau") from e
            time.sleep(3 * (attempt + 1))
    _put(phase="verifying", done=done + size, file=rel)
    if part.stat().st_size != size or not _digest(part, want):
        part.unlink(missing_ok=True)
        raise ModelError(f"File {rel} tải về không khớp mã băm — đã xoá, thử tải lại")
    os.replace(part, dest)
    return done + size


def ensure_background() -> None:
    """Tải ở luồng nền (mở app / bấm nút ở trang Giọng nói). Đang tải thì thôi."""
    global _thread
    if ready() or (_thread and _thread.is_alive()):
        return

    def run():
        try:
            ensure()
        except Exception as e:                 # đã ghi vào STATE
            print(f"Tải model giọng nói lỗi: {e}", flush=True)
    _thread = threading.Thread(target=run, daemon=True, name="voice-model")
    _thread.start()


def main() -> None:
    """`python -m dytool.voice_model [--machine]` — bước tải model của trình cài. Thường: in tiến độ từng 5%.
    --machine (cửa sổ cài dytool-Setup.exe): in "@@p|đã tải|tổng|tên" tối đa ~2 lần / giây."""
    import sys
    machine = "--machine" in sys.argv[1:]
    step, last = [-1], [0.0]
    if _vs_snapshot() and not ready():
        print("Dùng lại model của app VoiceStudio có sẵn trên máy (không tải lại)", flush=True)

    def show(d, t):
        if machine:
            if time.time() - last[0] >= 0.5 or d >= t:
                last[0] = time.time()
                print(f"@@p|{d}|{t}|OmniVoice (huggingface.co/{REPO})", flush=True)
            return
        p = int(d * 100 / t) // 5 * 5
        if p != step[0]:
            step[0] = p
            print(f"  model giong noi: {p}%", flush=True)
    ensure(show)
    print("Model giọng nói đã kiểm tra mã băm" if machine else "  model giong noi: xong", flush=True)


if __name__ == "__main__":
    main()
