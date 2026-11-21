"""Trạng thái từng bước của cửa sổ đăng nhập (Douyin / Facebook) để giao diện vẽ thanh tiến trình.

Tiến trình đăng nhập chạy riêng (subprocess) nên trạng thái ghi ra 1 file JSON nhỏ; giao diện đọc file đó.
"""

import json
import time
from pathlib import Path

DONE_SHOW_SEC = 5   # sau khi đăng nhập xong, thanh tiến trình hiện thêm chừng này giây rồi tự ẩn
DOUYIN_STEPS =["Mở trình duyệt", "Tải douyin.com", "Chờ bạn quét mã QR", "Lưu cookie"]
FB_STEPS = ["Mở trình duyệt", "Tải facebook.com", "Chờ bạn đăng nhập", "Lưu phiên", "Tìm các Page"]
TT_STEPS = ["Mở trình duyệt", "Tải tiktok.com", "Chờ bạn đăng nhập", "Lưu phiên", "Lấy tên kênh"]

HINTS = {
    "Mở trình duyệt": "Đang mở cửa sổ Chromium riêng của tool…",
    "Tải douyin.com": "Trang douyin.com tải khá lâu (máy chủ ở Trung Quốc) — có thể mất tới 1 phút.",
    "Chờ bạn quét mã QR": "Trong cửa sổ Chromium vừa mở: bấm 登录 (Đăng nhập) → mở app Douyin trên điện thoại "
                          "→ quét mã QR. Tool tự nhận khi đăng nhập xong.",
    "Tải facebook.com": "Đang tải trang facebook.com…",
    "Tải tiktok.com": "Đang tải trang đăng nhập tiktok.com…",
    "Lấy tên kênh": "Đã đăng nhập — đang đọc tên kênh TikTok (@…).",
    "Chờ bạn đăng nhập": "Trong cửa sổ Chromium vừa mở: đăng nhập tài khoản (TikTok cho phép quét mã QR bằng app; "
                         "nhập mã 2 lớp nếu có). Tool không đọc mật khẩu, tự nhận khi đăng nhập xong.",
    "Lưu cookie": "Đã đăng nhập — đang lưu cookie vào config.yaml.",
    "Lưu phiên": "Đã đăng nhập — đang lưu phiên Facebook.",
    "Tìm các Page": "Đang tìm các Page bạn quản lý (khoảng 10–30 giây)…",
}


def write(path: Path, steps: list[str], step: str, *, wait_sec: int | None = None,
          state: str = "running", msg: str = "") -> None:
    """Ghi bước hiện tại. wait_sec: số giây tối đa của bước chờ (để đếm ngược)."""
    old = read(path) or {}
    now = time.time()
    data = {
        "steps": steps, "step": step, "state": state, "msg": msg,
        "started": old.get("started", now) if step != steps[0] else now,
        "step_started": now if old.get("step") != step else old.get("step_started", now),
        "deadline": now + wait_sec if wait_sec else old.get("deadline") if old.get("step") == step else None,
        "ts": now,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def fail(path: Path, msg: str) -> None:
    old = read(path)
    if old and old.get("state") == "running":
        write(path, old["steps"], old["step"], state="error", msg=msg)


def read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def view(path: Path, alive: bool) -> dict | None:
    """Dữ liệu cho template. alive=False mà vẫn 'running' -> cửa sổ đã bị đóng / tiến trình chết."""
    d = read(path)
    if not d:
        return None
    now = time.time()
    if d["state"] == "running" and not alive:
        d["state"], d["msg"] = "error", d.get("msg") or "Cửa sổ đăng nhập đã đóng trước khi xong"
    # xong: hiện "✓ Xong" vài giây rồi ẩn; lỗi: giữ 10 phút (hoặc tới khi bấm ×) để người dùng biết lý do
    if d["state"] == "done" and now - d["ts"] > DONE_SHOW_SEC:
        return None
    if d["state"] == "error" and now - d["ts"] > 600:
        return None
    steps = d["steps"]
    idx = steps.index(d["step"]) if d["step"] in steps else 0
    if d["state"] == "done":
        idx = len(steps)
    d["segs"] = [{"name": s, "status": "done" if i < idx else "now" if i == idx else "todo"}
                  for i, s in enumerate(steps)]
    d["percent"] = int(100 * idx / len(steps)) if d["state"] != "done" else 100
    d["hint"] = HINTS.get(d["step"], "")
    d["elapsed"] = int(now - d["started"])
    d["remaining"] = max(0, int(d["deadline"] - now)) if d.get("deadline") and d["state"] == "running" else None
    if d["remaining"] is not None:
        total = max(1, d["deadline"] - d["step_started"])
        d["wait_percent"] = int(100 * (now - d["step_started"]) / total)
    return d
