"""dytool: Douyin -> edit -> lồng tiếng Việt -> Facebook Page / TikTok."""

import sys

# Chương trình nặng CPU chạy lâu (edit video, tách giọng): chạy ở mức ưu tiên "dưới bình thường" -> vẫn dùng hết CPU
# lúc máy rảnh, nhưng nhường cửa sổ dytool / trình duyệt / việc người dùng đang làm -> app không đơ khi đang edit.
_LOW_PRIORITY = {"ffmpeg", "ffprobe"}
_PRIORITY_FLAGS = 0x20 | 0x40 | 0x80 | 0x100 | 0x4000 | 0x8000   # mọi *_PRIORITY_CLASS


def _heavy(args) -> bool:
    if isinstance(args, (str, bytes)) or not args:
        return False
    try:
        items = [str(a) for a in args]
    except TypeError:
        return False
    name = items[0].replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe")
    return name in _LOW_PRIORITY or (len(items) > 2 and items[1] == "-m" and items[2] == "demucs")


def _patch_popen() -> None:
    """Windows, mọi tiến trình con:
    - App desktop chạy bằng pythonw (không có console): mỗi lần gọi chương trình dòng lệnh (ffmpeg, ffprobe, python
      của backend tải Douyin…) Windows lại bật 1 cửa sổ cmd nhảy lên rồi tắt -> thêm CREATE_NO_WINDOW. Chạy từ cmd /
      terminal (có console) thì không thêm: tiến trình con dùng chung console đó. Ai cố ý mở console riêng thì để nguyên.
    - ffmpeg / ffprobe / Demucs: BELOW_NORMAL_PRIORITY_CLASS (trừ khi người gọi đã tự chọn mức ưu tiên)."""
    if sys.platform != "win32":
        return
    import ctypes
    import subprocess
    if getattr(subprocess.Popen, "_dytool_patched", False):
        return
    try:
        no_console = not ctypes.windll.kernel32.GetConsoleWindow()
    except (AttributeError, OSError):
        no_console = False
    orig = subprocess.Popen.__init__

    def __init__(self, *args, **kwargs):
        flags = kwargs.get("creationflags", 0) or 0
        if no_console and not flags & subprocess.CREATE_NEW_CONSOLE:
            flags |= subprocess.CREATE_NO_WINDOW
        if not flags & _PRIORITY_FLAGS and _heavy(args[0] if args else kwargs.get("args")):
            flags |= subprocess.BELOW_NORMAL_PRIORITY_CLASS
        if flags:
            kwargs["creationflags"] = flags
        orig(self, *args, **kwargs)

    subprocess.Popen.__init__ = __init__
    subprocess.Popen._dytool_patched = True


_patch_popen()
