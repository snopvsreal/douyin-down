"""Bản chạy song song (app beta): thư mục cài có file `instance.json` (scripts/beta.ps1 tạo) -> đổi cổng giao diện,
tên khoá 1 phiên (app desktop + worker) và tên cửa sổ để mở cùng lúc với app chính; tắt tự cập nhật (trình cài bản
chính sẽ cài đè vào C:\\dytool và đóng app). Không có file = app chính, mọi thứ như cũ."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load() -> dict:
    try:
        d = json.loads((ROOT / "instance.json").read_text("utf-8-sig"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


_d = _load()
NAME = re.sub(r"[^a-z0-9]", "", str(_d.get("name") or "").lower())[:16]     # "" = app chính
try:
    PORT = int(_d.get("port") or 8000) if NAME else 8000
except (TypeError, ValueError):
    PORT = 8001
TITLE = f"dytool {NAME.capitalize()}" if NAME else "dytool"
IS_BETA = bool(NAME)


def mutex(base: str) -> str:
    """Tên khoá Windows riêng cho bản này: Local\\dytool-worker -> Local\\dytool-worker-beta."""
    return f"{base}-{NAME}" if NAME else base
