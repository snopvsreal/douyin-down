"""Tải trước các model AI (~1.5 GB) để lần edit đầu tiên không phải chờ. Gọi từ scripts/setup_app.ps1."""
import sys

sys.stdout.reconfigure(encoding="utf-8")
steps = [
    ("Whisper (nghe giọng nói)", lambda: __import__("faster_whisper").WhisperModel("small", device="cpu", compute_type="int8")),
    ("Demucs (tách giọng / nhạc nền)", lambda: __import__("demucs.pretrained", fromlist=["get_model"]).get_model("htdemucs")),
    ("RapidOCR (đọc phụ đề trên video)", lambda: __import__("rapidocr").RapidOCR()),
    ("Nhận giọng nam / nữ", lambda: __import__("dytool.gender", fromlist=["_model"])._model()),
]
failed = 0
for name, load in steps:
    print(f"   - {name} ...", flush=True)
    try:
        load()
        print("     xong", flush=True)
    except Exception as e:      # thiếu 1 model không chặn cài đặt: tool tự tải lại khi cần
        failed += 1
        print(f"     chưa tải được ({type(e).__name__}: {e}) — sẽ tự tải khi dùng lần đầu", flush=True)
sys.exit(1 if failed else 0)
