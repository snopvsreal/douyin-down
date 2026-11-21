"""Đọc cookie douyin.com từ Chrome đã đăng nhập (dùng pycookiecheat) và ghi vào config.yaml.
macOS sẽ hỏi quyền truy cập Keychain "Chrome Safe Storage" -> bấm Cho phép/Allow."""

import re
from pathlib import Path

CHROME_DIR = Path.home() / "Library/Application Support/Google/Chrome"
LOGIN_KEYS = ("sessionid", "sessionid_ss", "sid_guard")


def read_douyin_cookie(profile: str = "Default") -> dict:
    from pycookiecheat import chrome_cookies
    cookie_file = CHROME_DIR / profile / "Cookies"
    return chrome_cookies("https://www.douyin.com/", cookie_file=cookie_file)


def to_header(cookies: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def save_to_config(header: str, config_path: Path) -> None:
    text = config_path.read_text(encoding="utf-8")
    safe = header.replace("'", "''")   # escape cho chuỗi YAML nháy đơn
    new, n = re.subn(r"(?m)^(  cookie:).*$", lambda m: f"{m.group(1)} '{safe}'", text, count=1)
    if not n:
        raise SystemExit("Không thấy dòng `  cookie:` trong mục douyin của config.yaml")
    config_path.write_text(new, encoding="utf-8")


def is_logged_in(cookies: dict) -> bool:
    return any(cookies.get(k) for k in LOGIN_KEYS)


def parse_header(header: str) -> dict:
    return dict(p.strip().split("=", 1) for p in (header or "").split(";") if "=" in p)


def cookie_status(header: str) -> dict:
    """Trạng thái đăng nhập Douyin từ chuỗi cookie trong config.yaml (không gọi mạng).
    sid_guard = "<sid>|<lúc tạo>|<số giây hiệu lực>|<ngày hết hạn>" -> tính lúc hết hạn (thường ~60 ngày)."""
    import time
    from urllib.parse import unquote
    c = parse_header(header)
    expires = None
    parts = unquote(c.get("sid_guard", "")).split("|")
    if len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
        expires = int(parts[1]) + int(parts[2])
    logged = is_logged_in(c) and (expires is None or expires > time.time())
    return {"logged_in": logged, "expired": bool(is_logged_in(c) and expires and expires <= time.time()),
            "expires": expires, "days_left": int((expires - time.time()) // 86400) if expires else None}


def douyin_login(data_dir: Path, config_path: Path, timeout_min: int = 10) -> int:
    """Đa nền tảng (Windows/macOS/Linux, không cần quyền admin): mở Chromium riêng của tool để BẠN tự đăng nhập
    douyin.com (quét QR bằng app Douyin), rồi lưu cookie vào config.yaml. Trả về số cookie đã lưu."""
    import sys
    import time
    from playwright.sync_api import sync_playwright
    from . import loginstate as ls
    st = data_dir / "douyin_login.json"
    steps = ls.DOUYIN_STEPS

    def fail(msg: str):
        ls.fail(st, msg)
        raise SystemExit(msg)

    ls.write(st, steps, "Mở trình duyệt")
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(data_dir / "douyin_profile"), headless=False, channel="chromium", locale="zh-CN",
            viewport={"width": 1280, "height": 860},
            args=["--disable-blink-features=AutomationControlled"]
                 + (["--use-mock-keychain", "--password-store=basic"] if sys.platform == "darwin" else []))
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        ls.write(st, steps, "Tải douyin.com", wait_sec=90)
        try:   # douyin.com tải rất lâu (mạng VN): không chờ "load", hết giờ vẫn giữ cửa sổ cho người dùng đăng nhập
            page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=90_000)
        except Exception as e:
            if not ctx.pages:
                fail("Bạn đã đóng cửa sổ đăng nhập")
            print(f"Trang douyin.com tải chậm ({type(e).__name__}) — cứ đăng nhập trong cửa sổ đang mở")
        print("Hãy đăng nhập douyin.com trong cửa sổ vừa mở (quét mã QR bằng app Douyin)...", flush=True)
        ls.write(st, steps, "Chờ bạn quét mã QR", wait_sec=timeout_min * 60)
        deadline = time.time() + timeout_min * 60
        data = {}
        while time.time() < deadline:
            try:
                if not ctx.pages:
                    raise RuntimeError("không còn tab nào")
                data = {c["name"]: c["value"] for c in ctx.cookies("https://www.douyin.com")}
            except Exception:   # TargetClosedError: cửa sổ bị đóng (bởi người dùng hoặc Chromium tự tắt)
                fail("Cửa sổ đăng nhập đã bị đóng trước khi đăng nhập xong — bấm Đăng nhập Douyin để thử lại")
            if is_logged_in(data):
                break
            time.sleep(2)
        if not is_logged_in(data):
            ctx.close()
            fail(f"Hết {timeout_min} phút chờ mà chưa đăng nhập Douyin — bấm Đăng nhập Douyin để thử lại")
        ls.write(st, steps, "Lưu cookie")
        time.sleep(3)
        data = {c["name"]: c["value"] for c in ctx.cookies("https://www.douyin.com")}
        ctx.close()
    save_to_config(to_header(data), config_path)
    ls.write(st, steps, "Lưu cookie", state="done", msg=f"Đã đăng nhập Douyin, lưu {len(data)} cookie")
    return len(data)
