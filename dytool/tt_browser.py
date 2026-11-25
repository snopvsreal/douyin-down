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


AVATAR_FILE = "tt_avatar.img"     # ảnh kênh tải lúc đăng nhập; app chép sang data/avatars (avatars.py)


def _whoami(page, avatar_to: Path | None = None) -> dict:
    """Kênh đang đăng nhập: {"username": @id, "nickname": tên kênh, "avatar": link ảnh}. Không được thì rỗng.
    1) API tài khoản (passport) — chắc chắn nhất cho @id + tên; 2) dữ liệu trang tiktok.com cho tên kênh + ảnh.
    Trước chỉ dùng (2): tiktok.com tự chuyển sang /foryou làm lệnh mở trang bị ngắt -> không đọc được gì, kênh lấy
    tên người dùng tự gõ. avatar_to: tải luôn ảnh kênh về file đó (link ảnh có chữ ký, hết hạn sau vài ngày)."""
    who = {"username": "", "nickname": "", "avatar": ""}
    try:
        r = page.context.request.get("https://www.tiktok.com/passport/web/account/info/?aid=1459&app_language=en",
                                     timeout=30_000)
        d = (r.json() or {}).get("data") or {}
        who.update(username=d.get("username") or "", nickname=d.get("screen_name") or "")
    except Exception as e:
        print(f"Không đọc được tài khoản TikTok qua API: {e}", flush=True)
    for attempt in range(3):
        try:
            try:
                page.goto("https://www.tiktok.com/", wait_until="domcontentloaded", timeout=60_000)
            except Exception as e:      # bị chuyển hướng giữa chừng (/foryou) -> chờ trang mới tải xong
                print(f"tiktok.com chuyển trang ({str(e)[:80]}) — chờ", flush=True)
            page.wait_for_load_state("domcontentloaded", timeout=60_000)
            page.wait_for_selector("#__UNIVERSAL_DATA_FOR_REHYDRATION__", state="attached", timeout=20_000)
            raw = page.locator("#__UNIVERSAL_DATA_FOR_REHYDRATION__").text_content() or "{}"
            user = json.loads(raw).get("__DEFAULT_SCOPE__", {}).get("webapp.app-context", {}).get("user") or {}
            if user.get("uniqueId"):
                who["username"] = who["username"] or user["uniqueId"]
                who["nickname"] = user.get("nickName") or who["nickname"]
                who["avatar"] = next((u for u in user.get("avatarUri") or [] if str(u).startswith("https://")), "")
            break
        except Exception as e:
            print(f"Đọc trang tiktok.com lần {attempt + 1} lỗi: {str(e)[:120]}", flush=True)
            time.sleep(2)
    if avatar_to and who["avatar"]:
        try:
            img = page.context.request.get(who["avatar"], timeout=30_000)
            if img.ok and len(img.body()) < 5 * 1024 * 1024:
                avatar_to.write_bytes(img.body())
        except Exception as e:
            print(f"Không tải được ảnh kênh TikTok: {e}", flush=True)
    if not who["username"]:
        print("Không đọc được tên kênh TikTok", flush=True)
    return who


def refresh_info(acc_dir: Path) -> dict:
    """Đọc lại tên + ảnh kênh bằng phiên đã đăng nhập (nút "Cập nhật tên & ảnh" ở trang Tài khoản)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx = _context(pw, acc_dir, headless=False)
        try:
            if not logged_in(ctx):
                raise TTError("Phiên đăng nhập TikTok đã hết — bấm Đăng nhập lại")
            return _whoami(ctx.pages[0] if ctx.pages else ctx.new_page(), acc_dir / AVATAR_FILE)
        finally:
            ctx.close()


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
        (acc_dir / AVATAR_FILE).unlink(missing_ok=True)      # không dùng nhầm ảnh của lần đăng nhập trước
        who = _whoami(ctx.pages[0] if ctx.pages else ctx.new_page(), acc_dir / AVATAR_FILE)
        ctx.close()
    name = who["nickname"] or (f"@{who['username']}" if who["username"] else "kênh TikTok")
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


# Hộp / bong bóng TikTok Studio tự bật lên che form (tiktok-uploader không biết đóng -> click nào cũng trượt, điền
# mô tả / hẹn giờ hỏng). (chữ nhận diện, nút bấm). "Cancel" ở hộp content checks: KHÔNG tự đổi cài đặt tài khoản.
# "Allow" ở hộp lưu video chờ hẹn giờ: bắt buộc để hẹn giờ — người dùng đã đồng ý cho tự bấm (2026-11-24).
_KNOWN_MODALS = (("automatic content checks", "Cancel"),
                 ("saved for scheduled posting", "Allow"))
_OVERLAY_JS = """() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const modals = [...document.querySelectorAll('.TUXModal-overlay[data-transition-status=open]')].filter(vis)
                   .map(e => e.innerText.replace(/\\s+/g, ' ').trim().slice(0, 200));
  const tips = [...document.querySelectorAll('.react-joyride__tooltip, .tutorial-tooltip')].filter(vis).length;
  const mention = [...document.querySelectorAll('.mention-list-popover')].filter(vis).length;
  return {modals, tips, mention};
}"""


# bấm nút có chữ đúng `btn` nằm trong khối nhỏ nhất chứa chữ `key` (key rỗng: nút trong bong bóng hướng dẫn).
# Dùng JS vì phần hộp của TikTok bị ẩn khỏi cây trợ năng -> get_by_role không thấy nút.
_CLICK_IN_JS = """([key, btn]) => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  for (const b of [...document.querySelectorAll('button')].filter(vis)) {
    if (b.innerText.trim() !== btn) continue;
    let e = b.parentElement;
    while (e && e !== document.body && !(e.innerText || '').toLowerCase().includes(key)) e = e.parentElement;
    if (!e || e === document.body || (e.innerText || '').length > 1500) continue;
    if (!key && !e.closest('.react-joyride__tooltip, .tutorial-tooltip')) continue;
    b.click();
    return true;
  }
  return false;
}"""


def key_of(text: str) -> str:
    return next((k for k, _ in _KNOWN_MODALS if k in text.lower()), "")


def _dismiss_overlays(page, rounds: int = 4) -> None:
    """Đóng các hộp che form đăng. Gặp hộp lạ (không biết bấm gì cho an toàn) -> TTError, không đăng mò."""
    for _ in range(rounds):
        st = page.evaluate(_OVERLAY_JS)
        if not (st["modals"] or st["tips"] or st["mention"]):
            return
        for text in st["modals"]:
            known = next((btn for key, btn in _KNOWN_MODALS if key in text.lower()), None)
            if not known:
                raise TTError(f"TikTok Studio hiện hộp thoại lạ, dừng để không đăng sai: “{text[:160]}”")
            if not page.evaluate(_CLICK_IN_JS, [key_of(text), known]):
                raise TTError(f"Không bấm được “{known}” trong hộp TikTok: “{text[:120]}”")
            print(f"Đã đóng hộp TikTok: {text[:60]}… -> {known}", flush=True)
        if st["tips"]:
            page.evaluate(_CLICK_IN_JS, ["new editing features", "Got it"]) or \
                page.evaluate(_CLICK_IN_JS, ["", "Got it"])
        if st["mention"]:
            page.keyboard.press("Escape")       # danh sách gợi ý #hashtag / @mention còn mở
        time.sleep(0.8)
    st = page.evaluate(_OVERLAY_JS)
    if st["modals"]:
        raise TTError(f"Không đóng được hộp thoại TikTok Studio: “{st['modals'][0][:160]}”")


_TOP_SUGGEST_JS = """() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const box = [...document.querySelectorAll('.mention-list-popover')].find(vis);
  if (!box) return null;
  const first = box.querySelector('[class*=hashtag-suggestion-item], [role=option], li') || box.firstElementChild;
  return first ? first.innerText.trim().split(/\\s+/)[0] : '';
}"""


def _fill_description(page, caption: str) -> None:
    """Điền mô tả thay cho tiktok-uploader (nó gõ từng phím: chữ có dấu tiếng Việt bị đảo / mất trong ô DraftJS,
    vd "Chủ quan rồi" -> "quan rồi … ch"). Chữ thường: chèn thẳng (insert_text); xuống dòng: Enter;
    #hashtag (không dấu): gõ rồi chỉ chọn gợi ý khi gợi ý đầu tiên đúng y hashtag đó, không thì để nguyên chữ."""
    import re
    ed = page.locator("div[contenteditable='true']").first
    ed.wait_for(state="visible", timeout=30_000)
    ed.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    time.sleep(0.5)
    for part in re.split(r"(#[^\s#]+)", caption):
        if not part:
            continue
        if part.startswith("#") and part.isascii():
            page.keyboard.type(part, delay=60)
            top = None
            for _ in range(10):                 # chờ danh sách gợi ý (tối đa ~3 s)
                time.sleep(0.3)
                top = page.evaluate(_TOP_SUGGEST_JS)
                if top:
                    break
            if top and top.lower() == part.lower():
                page.keyboard.press("Enter")    # chọn đúng thẻ -> thành hashtag xanh
                time.sleep(0.4)
            continue
        if page.evaluate(_TOP_SUGGEST_JS) is not None:
            page.keyboard.press("Escape")       # đóng gợi ý trước khi gõ tiếp (Enter sẽ chọn nhầm gợi ý)
        for i, line in enumerate(part.split("\n")):
            if i:
                page.keyboard.press("Enter")
            if line:
                page.keyboard.insert_text(line)
        time.sleep(0.2)
    if page.evaluate(_TOP_SUGGEST_JS) is not None:
        page.keyboard.press("Escape")


def _set_schedule(page, when_utc: dt.datetime) -> None:
    """Hẹn giờ trên giao diện TikTok Studio mới (radio Now / Schedule + ô ngày DatePicker + ô giờ TimePicker).
    Bộ chọn của tiktok-uploader (#tux-1, date-picker-input…) đã hết dùng được. Đặt xong đọc lại 2 ô: sai -> TTError
    (thà không đăng còn hơn đăng ngay / sai giờ)."""
    import pytz
    tz = pytz.timezone(page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone"))
    t = when_utc.astimezone(tz)
    radio = page.locator("input[name=postSchedule][value=schedule]")
    radio.wait_for(state="attached", timeout=30_000)
    radio.locator("xpath=..").click(timeout=15_000)
    time.sleep(1.5)
    _dismiss_overlays(page)                     # hộp "Allow your video to be saved for scheduled posting?"
    if not radio.is_checked():
        raise TTError("Không chọn được Schedule trên TikTok Studio — chưa đăng gì")
    trig = page.locator(".schedule-picker .Picker__trigger")
    trig.nth(1).wait_for(state="visible", timeout=15_000)

    # ngày: chuyển tới đúng tháng rồi bấm ô có nhãn "…, November 25th, 2026"
    trig.nth(0).click()
    panel = page.locator(".DatePicker__panel")
    panel.wait_for(state="visible", timeout=10_000)
    want_month = f"{t:%B} {t.year}"
    for _ in range(13):
        cur = f"{panel.locator('.DatePicker__titleMonth').inner_text().strip()} " \
              f"{panel.locator('.DatePicker__titleYear').inner_text().strip()}"
        if cur == want_month:
            break
        later = dt.datetime.strptime(cur, "%B %Y") < dt.datetime.strptime(want_month, "%B %Y")
        panel.get_by_label("Next month" if later else "Previous month").click()
        time.sleep(0.4)
    day = t.day
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    btn = panel.locator(f"button.DatePicker__day[data-outside='false'][aria-label$='{t:%B} {day}{suffix}, {t.year}']")
    if btn.count() != 1 or btn.get_attribute("data-disabled") == "true":
        raise TTError(f"TikTok không cho chọn ngày {t:%d/%m/%Y} để hẹn giờ — chưa đăng gì")
    btn.click()
    time.sleep(0.8)
    if panel.is_visible():
        page.keyboard.press("Escape")
        time.sleep(0.4)

    # giờ: bấm "Hours HH" rồi "Minutes MM" (bước 5 phút)
    trig.nth(1).click()
    tp = page.locator(".TimePicker__panel")
    tp.wait_for(state="visible", timeout=10_000)
    for label in (f"Hours {t.hour:02d}", f"Minutes {t.minute:02d}"):
        opt = tp.locator(f"button.TimePicker__item[aria-label='{label}']")
        if opt.count() != 1 or opt.get_attribute("data-disabled") == "true":
            raise TTError(f"TikTok không cho chọn giờ {t:%H:%M} ({label}) — chưa đăng gì")
        opt.scroll_into_view_if_needed()
        opt.click()
        time.sleep(0.5)
    if tp.is_visible():
        page.keyboard.press("Escape")
        time.sleep(0.4)

    got_date, got_time = trig.nth(0).inner_text().strip(), trig.nth(1).inner_text().strip()
    want_date = f"{t:%b} {t.day}, {t.year}"
    if got_date != want_date or got_time != f"{t:%H:%M}":
        raise TTError(f"Hẹn giờ TikTok không khớp: ô đang là {got_date} {got_time}, cần {want_date} {t:%H:%M} "
                      "— chưa đăng gì")
    print(f"Đã đặt hẹn giờ TikTok: {got_date} {got_time} ({tz.zone})", flush=True)


_DONE_JS = r"""() => {
  if (/\/tiktokstudio\/content/.test(location.pathname)) return 'content-page';
  const t = document.body.innerText;
  const m = t.match(/Your video (has been|was) (uploaded|scheduled|posted)[^\n]*|Video (published|scheduled|posted)[^\n]*|scheduled successfully[^\n]*|视频已发布/i);
  return m ? m[0].slice(0, 120) : '';
}"""


def _click_post(page, shot: Path) -> str:
    """Bấm nút Post / Schedule ĐÚNG 1 LẦN rồi chờ dấu hiệu TikTok đã nhận bài. Không thấy -> TTError "kiểm tra trên
    TikTok" (worker trả video về Chờ duyệt kèm lời nhắc, KHÔNG bấm lại -> không đăng trùng)."""
    from . import progress
    btn = page.locator("button[data-e2e='post_video_button']")
    btn.wait_for(state="visible", timeout=60_000)
    for _ in range(90):                       # video còn đang tải / xử lý -> nút bị khoá
        if btn.get_attribute("data-disabled") != "true" and btn.get_attribute("aria-disabled") != "true":
            break
        progress.check()
        time.sleep(2)
    else:
        raise TTError("Nút Post trên TikTok Studio khoá quá 3 phút (video chưa xử lý xong) — chưa đăng gì")
    btn.scroll_into_view_if_needed()
    progress.no_return()                      # từ đây nút Dừng không ngắt được nữa
    btn.click(timeout=15_000)
    deadline = time.time() + 120
    while time.time() < deadline:
        time.sleep(1.5)
        try:
            done = page.evaluate(_DONE_JS)
        except Exception:                     # trang đang chuyển (sang trang Nội dung) -> hỏi lại
            continue
        if done:
            try:
                page.screenshot(path=str(shot), full_page=False)
            except Exception:
                pass
            return done
        post_now = page.locator(".TUXModal-overlay[data-transition-status=open] button", has_text="Post now")
        if post_now.count():                  # TikTok còn đang kiểm tra nội dung -> hỏi "Post now?"
            post_now.first.click()
    try:
        page.screenshot(path=str(shot), full_page=True)
    except Exception:
        pass
    raise TTError("Đã bấm đăng nhưng không thấy TikTok xác nhận trong 2 phút — KIỂM TRA trong TikTok Studio xem "
                  f"bài đã lên / đã hẹn giờ chưa (ảnh: {shot.parent.name}/{shot.name})")


def _norm(s: str) -> str:
    import unicodedata
    return " ".join(unicodedata.normalize("NFC", s).split()).lower()


def _check_description(page, caption: str) -> None:
    """tiktok-uploader nuốt lỗi điền mô tả (chỉ print) -> tự đọc lại ô mô tả; trống / sai thì dừng, không đăng."""
    want = _norm(caption.encode("utf-8", "ignore").decode("utf-8"))[:40]
    try:
        got = _norm(page.locator("div[contenteditable='true']").first.inner_text(timeout=10_000))
    except Exception as e:
        raise TTError(f"Không đọc được ô mô tả TikTok: {e}") from e
    # so phần chữ trước hashtag đầu tiên (hashtag TikTok có thể tự đổi thành thẻ)
    head = want.split("#", 1)[0].strip()[:30]
    if head and head not in got:
        raise TTError(f"Điền mô tả TikTok không được (ô mô tả đang là “{got[:80]}”) — dừng, chưa đăng gì")


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
        real = {n: getattr(tu, n) for n in ("_set_interactivity", "_set_description", "_set_schedule_video")}

        def guarded(name):
            def run(p, *a, **kw):
                progress.check()
                _dismiss_overlays(p)
                if name == "_set_interactivity":
                    return None     # bộ chọn cũ: luôn hỏng sau 30 s chờ; mặc định TikTok đã cho bình luận / duet / stitch
                if name == "_set_description":
                    _fill_description(p, caption[:2200])
                    _dismiss_overlays(p)
                    _check_description(p, caption[:2200])
                    return None
                if name == "_set_schedule_video":
                    _set_schedule(p, a[0] if a else kw["schedule"])
                    _dismiss_overlays(p)
                    return None
                return real[name](p, *a, **kw)
            return run
        for n in real:
            setattr(tu, n, guarded(n))
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
            def last_check(p):         # ngay trước nút cuối: đúng Now / Schedule như yêu cầu, không còn hộp che
                _dismiss_overlays(p)
                is_sched = p.locator("input[name=postSchedule][value=schedule]").is_checked()
                if is_sched != bool(sched):
                    raise TTError(f"Ô “When to post” đang là {'Schedule' if is_sched else 'Now'}, khác yêu cầu "
                                  "— dừng, chưa đăng gì")
            if dry_run:     # đi hết các bước nhưng không bấm Post
                def no_post(p):
                    last_check(p)
                    print("[dry_run] dừng trước nút " + ("Schedule" if sched else "Post"), flush=True)
                tu._post_video = no_post
            else:
                result = {}

                def post(p):           # nút Dừng: dừng được tới ngay trước khi bấm Post, sau đó thì không
                    last_check(p)
                    progress.step("Bấm " + ("Schedule" if sched else "Post"))
                    result["done"] = _click_post(p, log_dir / f"{stamp}_{video.stem}_posted.png")
                tu._post_video = post
            tu.complete_upload_form(page, str(video.resolve()), caption[:2200], sched, False, num_retries=2,
                                    headless=headless)
            if dry_run:
                page.screenshot(path=str(log_dir / f"{stamp}_{video.stem}_dryrun.png"), full_page=True)
                return {"status": "dry_run"}
            print(f"TikTok xác nhận: {result.get('done')}", flush=True)
            return {"status": "scheduled" if sched else "published", "url": page.url, "confirm": result.get("done")}
        except Exception as e:
            try:
                page.screenshot(path=str(log_dir / f"{stamp}_{video.stem}_error.png"), full_page=True)
            except Exception:
                pass
            if isinstance(e, (TTError, progress.Cancelled)):
                raise
            if type(e).__name__ == "FailedToUpload":    # tiktok-uploader: hẹn giờ / tải video hỏng (không có chi tiết)
                raise TTError(f"TikTok Studio: {'đặt giờ hẹn' if sched else 'tải video lên'} không được — chưa đăng gì "
                              f"(ảnh lỗi: {log_dir.name}/{stamp}_*)") from e
            raise TTError(f"{type(e).__name__}: {str(e)[:300]} (ảnh lỗi: {log_dir.name}/{stamp}_*)") from e
        finally:
            tu._post_video = real_post
            for n, f in real.items():
                setattr(tu, n, f)
            ctx.close()


# ---------- bài trong TikTok Studio (bài hẹn giờ tới giờ -> Đã đăng + link video) ----------
CONTENT = "https://www.tiktok.com/tiktokstudio/content"


def content_items(acc_dir: Path, username: str = "", find: list[str] | None = None, rows: dict | None = None) -> list[dict]:
    """CHỈ ĐỌC: danh sách video của kênh trong TikTok Studio › Nội dung (API item_list/v1 trang tự gọi).
    Mỗi video: item_id, title (= mô tả), post_time (giờ đăng / giờ hẹn), scheduled (còn chờ giờ hẹn), url.
    Video hẹn giờ đã có item_id từ lúc hẹn; còn chờ thì TikTok ghi "Scheduled posts cannot be edited".
    find + rows: ghi vào rows chữ hiện trên dòng của từng caption (trạng thái TikTok báo: "Under review", "Only me"…)."""
    import json
    from playwright.sync_api import sync_playwright
    bodies: list[str] = []
    with sync_playwright() as pw:
        ctx = _context(pw, acc_dir, headless=True)
        try:
            if not logged_in(ctx):
                raise TTError("Phiên TikTok hết hạn — đăng nhập lại ở trang Tài khoản")
            page = ctx.new_page()

            def on_resp(r):
                if "/creator/manage/item_list/" in r.url:
                    try:
                        bodies.append(r.text())
                    except Exception:
                        pass
            page.on("response", on_resp)
            page.goto(CONTENT, wait_until="domcontentloaded", timeout=90000)
            for _ in range(15):                     # chờ trang gọi API danh sách (tối đa ~30 s)
                page.wait_for_timeout(2000)
                if bodies:
                    page.wait_for_timeout(2000)     # trang gọi vài lần liền nhau (cùng dữ liệu)
                    break
            if not bodies and "login" in page.url:
                raise TTError("TikTok chuyển sang trang đăng nhập — đăng nhập lại ở trang Tài khoản")
            if find and rows is not None:
                from .fb_browser import ROW_JS
                rows.update(page.evaluate(ROW_JS, [c for c in find if c]))
        finally:
            ctx.close()
    items: dict[str, dict] = {}
    for b in bodies:
        try:
            lst = json.loads(b).get("item_list") or []
        except ValueError:
            continue
        for it in lst:
            iid = str(it.get("item_id") or "")
            if not iid:
                continue
            perms = json.dumps(it.get("permissions") or {})
            items[iid] = {
                "item_id": iid, "title": it.get("desc") or "",
                "post_time": int(it.get("post_time") or it.get("schedule_time") or it.get("create_time") or 0),
                "scheduled": "Scheduled post" in perms, "in_review": bool(it.get("in_review")),
                "public": it.get("visibility") == 1,
                "url": f"https://www.tiktok.com/@{username}/video/{iid}" if username else "",
            }
    return sorted(items.values(), key=lambda x: -x["post_time"])
