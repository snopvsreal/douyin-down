"""Lưu kết quả từng bước edit để Thử lại không phải chạy lại từ đầu (người dùng 2026-11-27: "dò phụ đề xong lỗi
chạy lại từ đầu thì mất thời gian quá").

Mỗi bước (dò dải sub, Whisper, OCR, Demucs, nam / nữ, dịch AI, từng câu lồng tiếng…) ghi kết quả vào
work/<video>_steps/<tên bước>.json kèm khoá = kích thước + giờ sửa file gốc + tham số của bước (cài đặt ảnh hưởng tới
kết quả). Khoá khác (đổi cài đặt, tải lại video) -> chạy lại bước đó. Bước tạo file (Demucs, clip giọng đọc) ghi kèm
kích thước + giờ sửa của file; file mất / bị ghi đè -> chạy lại.

editor.edit xoá thư mục khi edit XONG (Edit lại sau đó vẫn dịch / đọc mới); lỗi hoặc bấm Dừng thì giữ lại cho lần thử
sau. worker._cleanup xoá work/ cũ hơn 1 ngày như các file tạm khác."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

_MISS = object()


def _plain(o):
    """numpy số / mảng -> số Python (default=str sẽ biến thành chuỗi, lần sau cộng trừ thì lỗi)."""
    if hasattr(o, "tolist"):
        return o.tolist()
    if hasattr(o, "item"):
        return o.item()
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"không lưu được kiểu {type(o).__name__}")


def _plain_or_str(o):
    try:
        return _plain(o)
    except TypeError:
        return str(o)


def _fsig(p: Path) -> list | None:
    try:
        st = Path(p).stat()
    except OSError:
        return None
    return [st.st_size, st.st_mtime_ns]


class Steps:
    def __init__(self, work: Path, src: Path):
        self.dir = Path(work) / f"{Path(src).stem}_steps"
        self.base = _fsig(src)
        self.reused: list[str] = []

    def _key(self, params) -> str:
        # số numpy -> số Python như lúc lưu kết quả: lần đầu tham số có thể là float32 của OCR, lần thử lại là float
        # đọc từ JSON — phải ra cùng khoá
        raw = json.dumps(json.loads(json.dumps(params, default=_plain_or_str)), sort_keys=True, ensure_ascii=False)
        raw = json.dumps([self.base, raw])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _path(self, name: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:80]
        return self.dir / f"{safe}.json"

    def get(self, name: str, params):
        """Kết quả đã lưu của bước, hoặc _MISS."""
        if self.base is None:
            return _MISS
        try:
            rec = json.loads(self._path(name).read_text("utf-8"))
        except (OSError, ValueError):
            return _MISS
        if not isinstance(rec, dict) or rec.get("key") != self._key(params):
            return _MISS
        for f, sig in (rec.get("files") or {}).items():
            if _fsig(Path(f)) != sig:
                return _MISS
        return rec.get("value")

    def put(self, name: str, params, value, files=()) -> None:
        if self.base is None:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        rec = {"key": self._key(params), "value": value,
               "files": {str(Path(f)): _fsig(Path(f)) for f in files}}
        p = self._path(name)
        tmp = p.with_suffix(".tmp")
        try:
            text = json.dumps(rec, ensure_ascii=False, default=_plain)
        except (TypeError, ValueError):
            return                    # kết quả lạ không lưu được: lần sau chạy lại bước này, không làm hỏng edit
        tmp.write_text(text, "utf-8")
        os.replace(tmp, p)

    def run(self, name: str, params, fn, label: str | None = None, files=None):
        """Chạy fn() hoặc lấy kết quả lần trước. label: tên bước hiện ở bảng tiến trình. files(value) -> các file
        kết quả cần còn nguyên để dùng lại."""
        from . import progress
        hit = self.get(name, params)
        if hit is not _MISS:
            self.reused.append(name)
            if label:
                progress.step(f"{label} — dùng lại kết quả lần trước")
            return hit
        if label:
            progress.step(label)
        value = fn()
        self.put(name, params, value, files(value) if files else ())
        return value

    def clear(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


class NoSteps(Steps):
    """Không lưu gì (gọi dub_audio / pick_source từ chỗ khác không có thư mục làm việc riêng)."""

    def __init__(self):
        self.dir, self.base, self.reused = Path("."), None, []

    def clear(self) -> None:
        pass
