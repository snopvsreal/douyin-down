"""Bật/kiểm tra các dịch vụ phụ: backend tải Douyin (TikTokDownloader Web API)."""

import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def tdl_alive(cfg: dict) -> bool:
    try:
        return httpx.get(cfg["douyin"]["api"].rstrip("/") + "/docs", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


_proc: subprocess.Popen | None = None      # backend tải đã bật lần gần nhất


def ensure_tdl(cfg: dict, config_path: str = "config.yaml", wait: float = 90) -> bool:
    """Backend tải chưa chạy thì bật lên (tiến trình tách riêng: không tắt theo giao diện) và chờ sẵn sàng."""
    global _proc
    if tdl_alive(cfg):
        return True
    data = Path(cfg["_data"])
    if _proc is None or _proc.poll() is not None:    # lần trước còn đang khởi động -> chờ tiếp, không bật thêm
        detach = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
                  if sys.platform == "win32" else {"start_new_session": True})
        with open(data / "tdl_server.log", "a") as log:     # tiến trình con giữ handle riêng; đóng bản của mình
            _proc = subprocess.Popen([sys.executable, "-m", "dytool.cli", "-c", str(ROOT / config_path), "server"],
                                     cwd=ROOT, stdout=log, stderr=log, stdin=subprocess.DEVNULL, **detach)
    deadline = time.time() + wait
    while time.time() < deadline:
        if tdl_alive(cfg):
            time.sleep(10)          # backend còn cập nhật tham số Douyin vài giây sau khi mở cổng
            return True
        time.sleep(2)
    return False
