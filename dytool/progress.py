"""Theo dõi tiến trình của việc đang chạy để hiện trên giao diện.
Worker gọi begin(); các hàm edit/lồng tiếng/đăng gọi step()/detail() — không cần truyền tham số qua lại."""

import threading
import time

_lock = threading.Lock()
STATE: dict = {"task": None}


def begin(kind: str, title: str, page: str = "", video: str = "", caption: str = "") -> None:
    with _lock:
        STATE["task"] = {"kind": kind, "title": title, "page": page, "video": video, "caption": caption,
                         "started": time.time(), "steps": [], "detail": ""}


def step(name: str, detail: str = "") -> None:
    """Bắt đầu 1 bước mới (bước trước tự đánh dấu xong)."""
    with _lock:
        t = STATE["task"]
        if not t:
            return
        now = time.time()
        for s in t["steps"]:
            if s["end"] is None:
                s["end"] = now
        t["steps"].append({"name": name, "start": now, "end": None})
        t["detail"] = detail


def detail(text: str) -> None:
    with _lock:
        if STATE["task"]:
            STATE["task"]["detail"] = text


def end() -> None:
    with _lock:
        STATE["task"] = None


def snapshot() -> dict | None:
    with _lock:
        t = STATE["task"]
        if not t:
            return None
        now = time.time()
        return {**t, "elapsed": now - t["started"],
                "steps": [{**s, "secs": (s["end"] or now) - s["start"], "running": s["end"] is None}
                          for s in t["steps"]]}
