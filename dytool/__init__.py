"""dytool: Douyin -> edit -> lồng tiếng Việt -> Facebook Page / TikTok."""

import sys


def _hide_child_consoles() -> None:
    """App desktop chạy bằng pythonw (không có console): mỗi lần gọi chương trình dòng lệnh (ffmpeg, ffprobe,
    python của backend tải Douyin, thư viện bên ngoài…) Windows lại bật 1 cửa sổ cmd nhảy lên rồi tắt.
    Khi tiến trình này không có console -> mặc định mọi tiến trình con chạy với CREATE_NO_WINDOW.
    Chạy từ cmd / terminal (có console) thì không đổi gì: tiến trình con dùng chung console đó, không bật cửa sổ mới.
    App có giao diện (ShardBrowser, VoiceStudio…) không bị ảnh hưởng: cờ này chỉ áp cho chương trình console."""
    if sys.platform != "win32":
        return
    import ctypes
    import subprocess
    try:
        if ctypes.windll.kernel32.GetConsoleWindow():
            return
    except (AttributeError, OSError):
        return
    if getattr(subprocess.Popen, "_dytool_no_window", False):
        return
    orig = subprocess.Popen.__init__

    def __init__(self, *args, **kwargs):
        flags = kwargs.get("creationflags", 0) or 0
        if not flags & subprocess.CREATE_NEW_CONSOLE:     # ai cố ý mở console riêng thì để nguyên
            kwargs["creationflags"] = flags | subprocess.CREATE_NO_WINDOW
        orig(self, *args, **kwargs)

    subprocess.Popen.__init__ = __init__
    subprocess.Popen._dytool_no_window = True


_hide_child_consoles()
