"""Đăng video lên TikTok bằng trình duyệt (Playwright) qua TikTok Studio, không cần API.

Phần điền form đăng dùng thư viện wkaisertexas/tiktok-uploader (MIT, cũng chạy Playwright): truyền vào trang của
hồ sơ trình duyệt nhúng gắn với tài khoản (browsers.py) để dùng lại phiên đăng nhập.
Đăng nhập luôn do người dùng tự làm trong cửa sổ mở ra (tool không đọc mật khẩu).
Lưu ý: bộ chọn của tiktok-uploader viết theo giao diện tiếng Anh -> hồ sơ TikTok được tạo sẵn ở tiếng Anh (browsers.create_profile english=True).
"""

import datetime as dt
import json
import time
from pathlib import Path

LOGIN_STATE = "login_state.json"
TZ = "Asia/Ho_Chi_Minh"     # giờ dytool tính lịch; ô hẹn giờ TikTok dùng múi giờ của hồ sơ trình duyệt


class TTError(RuntimeError):
    pass


def _context(pw, acc_dir: Path, headless: bool):
    from . import browsers
    return browsers.attach(pw, acc_dir, headless)


def logged_in(ctx) -> bool:
    return any(c["name"] == "sessionid" and c["value"] for c in ctx.cookies("https://www.tiktok.com"))


def _whoami(page) -> dict:
    """Tên kênh (@uniqueId) đọc từ dữ liệu trang tiktok.com; không được thì trả rỗng."""
    try:
        page.goto("https://www.tiktok.com/", wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_selector("#__UNIVERSAL_DATA_FOR_REHYDRATION__", state="attached", timeout=20_000)
        raw = page.locator("#__UNIVERSAL_DATA_FOR_REHYDRATION__").text_content() or "{}"
        user = json.loads(raw).get("__DEFAULT_SCOPE__", {}).get("webapp.app-context", {}).get("user") or {}
        return {"username": user.get("uniqueId") or "", "nickname": user.get("nickName") or ""}
    except Exception as e:
        print(f"Không đọc được tên kênh TikTok: {e}", flush=True)
        return {"username": "", "nickname": ""}


def login(acc_dir: Path, timeout_min: int = 10) -> dict:
    """Mở Chromium để BẠN tự đăng nhập TikTok (QR / số điện thoại / email). Trả về {"username", "nickname"}."""
    from playwright.sync_api import sync_playwright
    from . import loginstate as ls
    st = acc_dir / LOGIN_STATE
    steps = ls.TT_STEPS

    def fail(msg: str):
        ls.fail(st, msg)
        raise TTError(msg)

    ls.write(st, steps, "Mở trình duyệt")
    with sync_playwright() as pw:
        from . import browsers
        try:
            ctx = _context(pw, acc_dir, headless=False)
        except browsers.BrowserError as e:     # chưa có hồ sơ / nhân trình duyệt chưa tải xong
            fail(str(e))
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        ls.write(st, steps, "Tải tiktok.com", wait_sec=90)
        try:   # mạng chậm: không chờ "load", hết giờ vẫn giữ cửa sổ cho người dùng đăng nhập
            page.goto("https://www.tiktok.com/login", wait_until="domcontentloaded", timeout=90_000)
        except Exception as e:
            if not ctx.pages:
                fail("Bạn đã đóng cửa sổ đăng nhập")
            print(f"Trang tiktok.com tải chậm ({type(e).__name__}) — cứ đăng nhập trong cửa sổ đang mở")
        print("Hãy đăng nhập TikTok trong cửa sổ vừa mở (tool không đọc mật khẩu)...", flush=True)
        ls.write(st, steps, "Chờ bạn đăng nhập", wait_sec=timeout_min * 60)
        deadline = time.time() + timeout_min * 60
        while time.time() < deadline:
            try:
                if not ctx.pages:
                    raise RuntimeError("không còn tab nào")
                if logged_in(ctx):
                    break
            except Exception:   # TargetClosedError: cửa sổ bị đóng (bởi người dùng hoặc Chromium tự tắt)
                fail("Cửa sổ đăng nhập đã bị đóng trước khi đăng nhập xong — bấm Đăng nhập TikTok để thử lại")
            time.sleep(2)
        if not logged_in(ctx):
            ctx.close()
            fail(f"Hết {timeout_min} phút chờ mà chưa đăng nhập TikTok — bấm Đăng nhập TikTok để thử lại")
        ls.write(st, steps, "Lưu phiên")
        time.sleep(3)              # phiên nằm sẵn trong profile riêng, chờ TikTok ghi nốt cookie
        ls.write(st, steps, "Lấy tên kênh")
        who = _whoami(ctx.pages[0] if ctx.pages else ctx.new_page())
        ctx.close()
    name = f"@{who['username']}" if who["username"] else "kênh TikTok"
    ls.write(st, steps, "Lấy tên kênh", state="done", msg=f"Đã đăng nhập TikTok: {name}")
    return who


def valid_schedule(when: dt.datetime | None) -> dt.datetime | None:
    """Giờ hẹn hợp lệ cho TikTok (UTC, phút chia hết cho 5, sau 20 phút và trong 10 ngày) hoặc None nếu không hợp lệ."""
    import pytz
    from tiktok_uploader import upload as tu
    if when is None:
        return None
    aware = when if when.tzinfo else pytz.timezone(TZ).localize(when)
    utc = tu._get_valid_schedule_minute(aware.astimezone(pytz.UTC), 5)
    return utc if tu._check_valid_schedule(utc) else None


def publish(acc_dir: Path, video: Path, caption: str, schedule_at: dt.datetime | None = None,
            dry_run: bool = False, headless: bool = False, expect_user: str = "") -> dict:
    """Đăng (hoặc hẹn giờ) 1 video lên TikTok. dry_run=True: điền hết form rồi DỪNG trước nút Post.
    schedule_at: giờ VN (naive) — phải sau ít nhất 20 phút và trong 10 ngày (giới hạn của TikTok).
    expect_user: @tên kênh đã lưu — hồ sơ đang đăng nhập kênh khác (đổi hồ sơ / đăng nhập nhầm) thì dừng."""
    from playwright.sync_api import sync_playwright
    from tiktok_uploader import upload as tu
    from . import progress
    sched = valid_schedule(schedule_at) if schedule_at else None
    if schedule_at and sched is None:
        raise TTError(f"TikTok chỉ cho hẹn giờ từ 20 phút tới 10 ngày tới (giờ yêu cầu: {schedule_at:%H:%M %d/%m})")
    if not video.exists():
        raise TTError(f"Không thấy file video {video}")
    log_dir = acc_dir / "tt_logs"
    log_dir.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    progress.step("Mở TikTok Studio")
    with sync_playwright() as pw:
        ctx = _context(pw, acc_dir, headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        real_post = tu._post_video
        try:
            if not logged_in(ctx):
                raise TTError("Phiên đăng nhập TikTok đã hết — vào Tài khoản & Page bấm Đăng nhập TikTok")
            if expect_user:
                progress.step("Kiểm tra đúng kênh")
                who = _whoami(page).get("username") or ""
                if who and who.lstrip("@").lower() != expect_user.lstrip("@").lower():
                    raise TTError(f"Hồ sơ trình duyệt đang đăng nhập @{who}, không phải kênh @{expect_user.lstrip('@')} "
                                  "— dừng để tránh đăng nhầm kênh. Đăng nhập lại đúng kênh ở Tài khoản & kênh")
                if not who:
                    print("Không đọc được tên kênh để đối chiếu — vẫn đăng", flush=True)
            progress.step("Tải video lên + điền mô tả" + (" + hẹn giờ" if sched else ""))
            if dry_run:     # đi hết các bước nhưng không bấm Post
                tu._post_video = lambda p: print("[dry_run] dừng trước nút Post", flush=True)
            else:
                progress.step("Bấm " + ("Schedule" if sched else "Post"))

                def post(p):           # nút Dừng: dừng được tới ngay trước khi bấm Post, sau đó thì không
                    progress.no_return()
                    return real_post(p)
                tu._post_video = post
            tu.complete_upload_form(page, str(video.resolve()), caption[:2200], sched, False, num_retries=2,
                                    headless=headless)
            if dry_run:
                page.screenshot(path=str(log_dir / f"{stamp}_{video.stem}_dryrun.png"), full_page=True)
                return {"status": "dry_run"}
            return {"status": "scheduled" if sched else "published", "url": page.url}
        except Exception as e:
            try:
                page.screenshot(path=str(log_dir / f"{stamp}_{video.stem}_error.png"), full_page=True)
            except Exception:
                pass
            if isinstance(e, (TTError, progress.Cancelled)):
                raise
            raise TTError(f"{type(e).__name__}: {str(e)[:300]} (ảnh lỗi: {log_dir.name}/{stamp}_*)") from e
        finally:
            tu._post_video = real_post
            ctx.close()
