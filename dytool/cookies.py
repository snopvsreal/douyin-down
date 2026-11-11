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


def douyin_login(data_dir: Path, config_path: Path, timeout_min: int = 10) -> int:
    """Đa nền tảng (Windows/macOS/Linux, không cần quyền admin): mở Chromium riêng của tool để BẠN tự đăng nhập
    douyin.com (quét QR bằng app Douyin), rồi lưu cookie vào config.yaml. Trả về số cookie đã lưu."""
    import sys
    import time
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(data_dir / "douyin_profile"), headless=False, channel="chromium", locale="zh-CN",
            viewport={"width": 1280, "height": 860},
            args=["--disable-blink-features=AutomationControlled"]
                 + (["--use-mock-keychain", "--password-store=basic"] if sys.platform == "darwin" else []))
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.douyin.com/")
        print("Hãy đăng nhập douyin.com trong cửa sổ vừa mở (quét mã QR bằng app Douyin)...")
        deadline = time.time() + timeout_min * 60
        data = {}
        while time.time() < deadline:
            data = {c["name"]: c["value"] for c in ctx.cookies("https://www.douyin.com")}
            if is_logged_in(data):
                break
            time.sleep(2)
        time.sleep(3)
        data = {c["name"]: c["value"] for c in ctx.cookies("https://www.douyin.com")}
        ctx.close()
    if not is_logged_in(data):
        raise SystemExit("Hết thời gian chờ đăng nhập Douyin")
    save_to_config(to_header(data), config_path)
    return len(data)
