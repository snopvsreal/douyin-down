"""Theo dõi tiến trình của việc đang chạy để hiện trên giao diện + nút Dừng.
Worker gọi begin(); các hàm edit/lồng tiếng/đăng gọi step()/detail() — không cần truyền tham số qua lại.

Dừng (`cancel()`, nút Dừng ở thẻ Chạy tự động / trang Hôm nay): đặt cờ, step() / detail() / check() kế tiếp của luồng
đang làm ném `Cancelled`. Việc nặng (tải / edit / quét) còn tắt luôn chương trình con đang chạy cho việc đó (ffmpeg,
Demucs…) để không phải chờ hết bước. Lúc đăng: chỉ dừng được TRƯỚC khi bấm nút đăng / lên lịch cuối cùng
(`no_return()`); sau đó việc đăng phải chạy xong — dừng giữa chừng thì không biết bài đã lên chưa."""

import subprocess
import threading
import time
import weakref

_lock = threading.Lock()
STATE: dict = {"task": None}
_cancel = threading.Event()
_ctl = {"owner": None, "no_return": False}
_procs: "weakref.WeakSet[subprocess.Popen]" = weakref.WeakSet()
KILLABLE = ("edit", "download", "scan")     # đăng: KHÔNG tắt tiến trình con (Playwright / trình duyệt giữa lúc bấm đăng)


class Cancelled(Exception):
    """Người dùng bấm Dừng."""

    def __init__(self, msg: str = "Bạn đã bấm Dừng"):
        super().__init__(msg)


def begin(kind: str, title: str, page: str = "", video: str = "", caption: str = "") -> None:
    with _lock:
        _cancel.clear()
        _ctl.update(owner=threading.get_ident(), no_return=False)
        STATE["task"] = {"kind": kind, "title": title, "page": page, "video": video, "caption": caption,
                         "started": time.time(), "steps": [], "detail": "", "stopping": False}


def check() -> None:
    """Ném Cancelled nếu người dùng đã bấm Dừng (gọi trong vòng lặp dài). Chỉ tác dụng trên luồng đang làm việc."""
    if _cancel.is_set() and threading.get_ident() == _ctl["owner"] and not _ctl["no_return"]:
        raise Cancelled()


def step(name: str, detail: str = "") -> None:
    """Bắt đầu 1 bước mới (bước trước tự đánh dấu xong)."""
    check()
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
    check()
    with _lock:
        if STATE["task"]:
            STATE["task"]["detail"] = text


def no_return() -> None:
    """Sắp bấm nút đăng / lên lịch cuối cùng: từ đây không dừng nữa (dừng thì không biết bài đã lên chưa)."""
    check()                    # bấm Dừng ngay trước đó thì vẫn kịp dừng
    with _lock:
        _ctl["no_return"] = True


def end() -> None:
    with _lock:
        STATE["task"] = None
        _ctl.update(owner=None, no_return=False)
        _cancel.clear()


def cancelled() -> bool:
    return _cancel.is_set()


def cancel() -> str:
    """Nút Dừng. Trả về 'none' (không có việc), 'late' (đã bấm đăng — để chạy xong), 'ok'."""
    with _lock:
        t = STATE["task"]
        if not t:
            return "none"
        if _ctl["no_return"]:
            return "late"
        _cancel.set()
        t["stopping"] = True
        kill = t["kind"] in KILLABLE
        procs = [p for p in list(_procs) if p.poll() is None] if kill else []
    for p in procs:            # cả cây tiến trình (Demucs / ffmpeg có thể sinh tiến trình con)
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, timeout=10)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    return "ok"


def snapshot() -> dict | None:
    with _lock:
        t = STATE["task"]
        if not t:
            return None
        now = time.time()
        return {**t, "elapsed": now - t["started"], "no_return": _ctl["no_return"],
                "steps": [{**s, "secs": (s["end"] or now) - s["start"], "running": s["end"] is None}
                          for s in t["steps"]]}


def _track_children() -> None:
    """Ghi lại tiến trình con do luồng đang làm việc mở (để nút Dừng tắt được ffmpeg / Demucs đang chạy)."""
    if getattr(subprocess.Popen, "_dytool_tracked", False):
        return
    orig = subprocess.Popen.__init__

    def __init__(self, *args, **kwargs):
        orig(self, *args, **kwargs)
        if _ctl["owner"] is not None and threading.get_ident() == _ctl["owner"]:
            _procs.add(self)

    subprocess.Popen.__init__ = __init__
    subprocess.Popen._dytool_tracked = True


_track_children()
