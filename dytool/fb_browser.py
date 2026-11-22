"""Đăng Reels lên Facebook Page bằng trình duyệt (Playwright), không cần Graph API token.
Trình duyệt = hồ sơ trình duyệt nhúng (nhân ShardX) gắn với tài khoản (browsers.py); phiên đăng nhập nằm trong hồ sơ đó.
Đăng qua Meta Business Suite (business.facebook.com) — giao diện ổn định nhất cho Page."""

import time
from pathlib import Path

STATE = "fb_state.json"   # bản sao cookie phiên sau khi đăng nhập (nhạy cảm, nằm trong data/ đã .gitignore)
LOGIN_STATE = "login_state.json"   # bước đăng nhập hiện tại cho thanh tiến trình trên giao diện (loginstate.py)


class FBError(RuntimeError):
    pass


def _noop(pct: int, text: str) -> None:
    pass


def _context(pw, data_dir: Path, headless: bool):
    from . import browsers
    return browsers.attach(pw, data_dir, headless)


def _save_state(ctx, data_dir: Path) -> None:
    import os
    path = data_dir / STATE
    ctx.storage_state(path=str(path))
    try:
        os.chmod(path, 0o600)       # Windows bỏ qua quyền kiểu Unix
    except OSError:
        pass


def logged_in(ctx) -> bool:
    return any(c["name"] == "c_user" for c in ctx.cookies("https://www.facebook.com"))


def login(data_dir: Path, timeout_min: int = 10) -> None:
    """Mở Chromium để BẠN tự đăng nhập Facebook (kể cả 2FA). Tự đóng khi đăng nhập xong."""
    from playwright.sync_api import sync_playwright
    from . import loginstate as ls
    st = data_dir / LOGIN_STATE
    steps = ls.FB_STEPS

    def fail(msg: str):
        ls.fail(st, msg)
        raise FBError(msg)

    ls.write(st, steps, "Mở trình duyệt")
    with sync_playwright() as pw:
        from . import browsers
        try:
            ctx = _context(pw, data_dir, headless=False)
        except browsers.BrowserError as e:     # chưa có hồ sơ / nhân trình duyệt chưa tải xong
            fail(str(e))
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        ls.write(st, steps, "Tải facebook.com", wait_sec=90)
        try:   # mạng chậm: không chờ "load", hết giờ vẫn giữ cửa sổ cho người dùng đăng nhập
            page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=90_000)
        except Exception as e:
            if not ctx.pages:
                fail("Bạn đã đóng cửa sổ đăng nhập")
            print(f"Trang facebook.com tải chậm ({type(e).__name__}) — cứ đăng nhập trong cửa sổ đang mở")
        print("Hãy đăng nhập Facebook trong cửa sổ vừa mở (tool không đọc mật khẩu)...", flush=True)
        ls.write(st, steps, "Chờ bạn đăng nhập", wait_sec=timeout_min * 60)
        deadline = time.time() + timeout_min * 60
        while time.time() < deadline:
            try:
                if not ctx.pages:
                    raise RuntimeError("không còn tab nào")
                if logged_in(ctx):
                    break
            except Exception:   # TargetClosedError: cửa sổ bị đóng (bởi người dùng hoặc Chromium tự tắt)
                fail("Cửa sổ đăng nhập đã bị đóng trước khi đăng nhập xong — bấm Đăng nhập Facebook để thử lại")
            time.sleep(2)
        if not logged_in(ctx):
            ctx.close()
            fail(f"Hết {timeout_min} phút chờ mà chưa đăng nhập Facebook — bấm Đăng nhập Facebook để thử lại")
        ls.write(st, steps, "Lưu phiên")
        time.sleep(3)
        _save_state(ctx, data_dir)
        ctx.close()
    print("Đã lưu phiên đăng nhập.")


def probe(data_dir: Path, url: str, out: Path) -> None:
    """Mở 1 trang bằng phiên đã lưu, chụp màn hình + lưu HTML để dò giao diện (dùng khi phát triển)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx = _context(pw, data_dir, headless=True)
        if not logged_in(ctx):
            raise FBError("Chưa đăng nhập. Chạy `uv run dytool fb-login` trước.")
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(8000)
        out.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out / "probe.png"), full_page=False)
        (out / "probe.html").write_text(page.content(), encoding="utf-8")
        print(page.url)
        ctx.close()


COMPOSER = "https://business.facebook.com/latest/reels_composer/?asset_id={asset_id}"
SAFE_TEXT = "an toàn để đăng"            # "Video của bạn an toàn để đăng! Không phát hiện vấn đề nào về bản quyền."
COPYRIGHT_WARN = ("bản quyền", "copyright")


FB_TZ_OK = ("Asia/Jakarta", "Asia/Ho_Chi_Minh", "Asia/Saigon", "Asia/Bangkok")   # cùng UTC+7 với giờ Việt Nam


def _set_schedule(page, when) -> None:
    """Chọn tab Lên lịch rồi điền ngày/giờ (giờ Việt Nam). Kiểm tra múi giờ của Page là UTC+7."""
    import re
    page.get_by_role("button", name="Lên lịch").first.click()
    page.wait_for_timeout(1500)
    tz = page.get_by_text(re.compile(r"^[A-Z][a-z]+/[A-Za-z_]+$")).first.inner_text() \
        if page.get_by_text(re.compile(r"^[A-Z][a-z]+/[A-Za-z_]+$")).count() else ""
    if tz and tz not in FB_TZ_OK:
        raise FBError(f"Múi giờ lên lịch của Page là {tz}, khác giờ Việt Nam — dừng để tránh đăng sai giờ")
    date = page.locator('input[placeholder="dd/mm/yyyy"]').first
    date.click(); page.keyboard.press("ControlOrMeta+A"); page.keyboard.type(when.strftime("%d/%m/%Y")); page.keyboard.press("Enter")
    page.wait_for_timeout(600)
    hh = page.locator('input[aria-label="giờ"]').first
    mm = page.locator('input[aria-label="phút"]').first
    hh.click(); page.keyboard.press("ControlOrMeta+A"); page.keyboard.type(when.strftime("%H"))
    mm.click(); page.keyboard.press("ControlOrMeta+A"); page.keyboard.type(when.strftime("%M"))
    page.get_by_text("Chọn ngày và giờ trong tương lai").first.click()
    page.wait_for_timeout(800)
    # kiểm tra lại bằng chữ hiển thị (ô giờ/phút là ô có định dạng, không đọc được value)
    want_date = f"{when.day} Tháng {when.month}, {when.year}"
    row = hh.locator("xpath=ancestor::div[4]").text_content() or ""
    if want_date not in date.input_value() or when.strftime("%H:%M") not in re.sub(r"\s+", "", row):
        raise FBError(f"Không điền được giờ lên lịch (thấy '{date.input_value()}' / '{row.strip()[:40]}')")


def _set_groups(page, groups: list[str], text: str = "") -> list[str]:
    """Bật "Chia sẻ lên nhóm" và chọn các nhóm (Page phải đã tham gia nhóm). Trả về danh sách nhóm đã chọn.
    Lỗi thì tắt lại công tắc để bài vẫn đăng lên Page bình thường."""
    toggle = page.locator('input[aria-label="Chia sẻ lên nhóm"]').first
    if not toggle.is_checked():
        toggle.click()
        page.wait_for_timeout(1500)
    chosen = []
    try:
        for g in groups[:3]:
            combo = page.get_by_role("combobox").filter(has_text="nhóm").first
            combo.scroll_into_view_if_needed()
            combo.click()
            search = page.get_by_placeholder("Tìm kiếm nhóm").first
            search.wait_for(timeout=10000)
            search.fill(g)
            page.wait_for_timeout(2500)
            if page.get_by_text("Không có kết quả phù hợp").count():
                raise FBError(f"Không tìm thấy nhóm '{g}' — Page đã tham gia nhóm này chưa?")
            opt = page.get_by_role("option").filter(has_text=g).first
            if not opt.count():
                opt = page.get_by_text(g, exact=True).last
            opt.click()
            page.wait_for_timeout(1200)
            page.keyboard.press("Escape")
            chosen.append(g)
        if text:
            page.get_by_role("button", name="Thêm văn bản").first.click()
            page.wait_for_timeout(1200)
            box = page.locator('[role="dialog"] [contenteditable="true"], [role="dialog"] textarea').first
            if box.count():
                box.click()
                page.keyboard.insert_text(text)
                done = page.locator('[role="dialog"]').get_by_role("button", name="Lưu")
                (done.first if done.count() else page.locator('[role="dialog"]').get_by_role("button").last).click()
                page.wait_for_timeout(800)
        return chosen
    except Exception:
        page.keyboard.press("Escape")
        if toggle.is_checked():
            toggle.click()            # tắt chia sẻ nhóm để không chặn việc đăng lên Page
        raise


def publish(data_dir: Path, video: Path, caption: str, cfg: dict, dry_run: bool = False, schedule_at=None) -> dict:
    """Đăng 1 Reels lên Page qua Meta Business Suite. dry_run=True: đi hết các bước nhưng KHÔNG bấm Chia sẻ.
    schedule_at (datetime giờ Việt Nam): dùng tính năng Lên lịch của Facebook — Facebook tự đăng đúng giờ.
    Dừng (không đăng) nếu: sai Page, upload lỗi, hoặc Facebook báo vấn đề bản quyền."""
    from playwright.sync_api import sync_playwright
    asset_id, page_name = cfg["asset_id"], cfg["page_name"]
    log_dir = data_dir / "fb_logs"
    log_dir.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    with sync_playwright() as pw:
        ctx = _context(pw, data_dir, headless=not cfg.get("show_browser", False))
        if not logged_in(ctx):
            ctx.close()
            raise FBError("Phiên Facebook hết hạn. Chạy `uv run dytool fb-login`.")
        from . import progress
        page = ctx.new_page()
        try:
            progress.step("Mở Meta Business Suite")
            page.goto(COMPOSER.format(asset_id=asset_id), wait_until="domcontentloaded")
            page.get_by_role("button", name="Thêm video").first.wait_for(timeout=60000)
            page.wait_for_timeout(2000)
            # an toàn: Business Suite phải vẫn ở đúng Page (asset_id trên địa chỉ — tài khoản không quản lý Page đó thì
            # Business Suite tự chuyển sang Page khác) VÀ có tên Page trên trang
            if f"asset_id={asset_id}" not in page.url:
                raise FBError(f"Business Suite đã chuyển sang Page khác (không còn asset_id {asset_id}) — dừng để "
                              f"tránh đăng nhầm. Tài khoản này còn quản lý Page '{page_name}' không?")
            if page.get_by_text(page_name, exact=True).count() == 0:
                raise FBError(f"Ô 'Đăng lên' không phải Page '{page_name}' — dừng để tránh đăng nhầm")

            with page.expect_file_chooser(timeout=20000) as fc:
                page.get_by_role("button", name="Thêm video").first.click()
            fc.value.set_files(str(video.resolve()))
            progress.step("Tải video lên Facebook")
            # chờ upload xong: nút Tiếp cuối trang bật lên
            nxt = page.get_by_role("button", name="Tiếp", exact=True).last
            deadline = time.time() + 600
            while time.time() < deadline and nxt.is_disabled():
                page.wait_for_timeout(2000)
            if nxt.is_disabled():
                raise FBError("Upload video quá lâu / lỗi")

            if caption:
                progress.step("Điền mô tả")
                box = page.locator('[contenteditable="true"][role="textbox"]').first
                box.click()
                page.keyboard.insert_text(caption)
                page.wait_for_timeout(800)
                first_line = caption.strip().splitlines()[0][:20]
                if first_line not in box.inner_text():
                    raise FBError("Không điền được mô tả (caption)")
                page.screenshot(path=str(log_dir / f"{stamp}_{video.stem}_caption.png"))
                # đóng gợi ý hashtag đang mở bằng cách bấm ra ngoài
                page.get_by_text("Chi tiết về thước phim").first.click()
                page.wait_for_timeout(500)

            nxt.click()                                           # -> Chỉnh sửa
            page.get_by_role("button", name="Âm thanh").first.wait_for(timeout=60000)
            page.get_by_role("button", name="Tiếp", exact=True).last.click()   # -> Chia sẻ
            page.get_by_role("button", name="Chia sẻ ngay").first.wait_for(timeout=60000)
            page.get_by_role("button", name="Chia sẻ ngay").first.click()
            if schedule_at:
                _set_schedule(page, schedule_at)
            group_info = {}
            if cfg.get("groups"):
                try:
                    group_info["groups"] = _set_groups(page, cfg["groups"], cfg.get("group_text", ""))
                except Exception as e:      # lỗi nhóm không chặn việc đăng lên Page
                    group_info["group_error"] = str(e)[:200]

            progress.step("Facebook kiểm tra bản quyền")
            # chờ Facebook kiểm tra bản quyền
            verdict = "unknown"
            for _ in range(60):
                body = page.inner_text("body")
                if SAFE_TEXT in body:
                    verdict = "safe"
                    break
                page.wait_for_timeout(2000)
            if verdict != "safe" and any(w in page.inner_text("body").lower() for w in COPYRIGHT_WARN):
                verdict = "copyright_issue"
            page.screenshot(path=str(log_dir / f"{stamp}_{video.stem}_share.png"))
            if verdict == "copyright_issue" and not cfg.get("post_on_copyright_issue", False):
                raise FBError("Sau 2 phút Facebook chưa báo 'an toàn để đăng' (có thể vướng bản quyền, hoặc kiểm tra "
                              "lâu) — chưa đăng; xem ảnh *_share.png trong data/fb_logs rồi duyệt lại nếu ổn")
            if dry_run:
                return {"status": "dry_run", "copyright": verdict, **group_info}

            final = "Lên lịch" if schedule_at else "Chia sẻ"
            progress.step("Xác nhận " + ("lên lịch" if schedule_at else "đăng"))
            before = page.inner_text("body").lower()
            progress.no_return()       # từ đây không dừng được nữa: bấm rồi thì phải chờ biết bài đã lên chưa
            page.get_by_role("button", name=final, exact=True).last.click()
            # thành công = HIỆN RA câu báo đã đăng / đã lên lịch mà trước khi bấm chưa có trên trang.
            # Không còn coi "rời trang composer" là thành công: bị đá sang trang đăng nhập / checkpoint cũng rời trang
            done_phrases = ("đang xử lý thước phim", "đã được chia sẻ", "đã lên lịch")
            ok = False
            for _ in range(90):
                page.wait_for_timeout(2000)
                body = page.inner_text("body").lower()
                # "Đang xử lý thước phim — Khi xử lý xong, hệ thống sẽ đăng thước phim..." = đã gửi đăng thành công
                if any(p in body and p not in before for p in done_phrases):
                    ok = True
                    break
                if any(w in page.url for w in ("/login", "checkpoint")):
                    raise FBError("Facebook chuyển sang trang đăng nhập / kiểm tra bảo mật sau khi bấm "
                                  f"{final} — chưa chắc đã đăng, xem ảnh trong data/fb_logs")
            page.screenshot(path=str(log_dir / f"{stamp}_{video.stem}_done.png"))
            if ok and page.get_by_role("button", name="Xong").count():
                page.get_by_role("button", name="Xong").first.click()
                page.wait_for_timeout(1500)
            if not ok:
                raise FBError("Không xác nhận được đã đăng — xem ảnh trong data/fb_logs")
            return {"status": "scheduled" if schedule_at else "published", "copyright": verdict, "url": page.url,
                    **group_info}
        except Exception:
            page.screenshot(path=str(log_dir / f"{stamp}_{video.stem}_error.png"))
            raise
        finally:
            ctx.close()


def _page_links(page) -> dict[str, str]:
    import re
    found: dict[str, str] = {}
    for a in page.query_selector_all('a[href*="facebook.com/"]'):
        name = (a.inner_text() or "").strip()
        href = a.get_attribute("href") or ""
        m = re.search(r"profile\.php\?id=(\d+)|facebook\.com/([A-Za-z0-9.]{5,})/?$", href)
        if not name or "\n" in name or len(name) > 80 or not m or name == "Meta Business Suite":
            continue
        found.setdefault(name, m.group(1) or m.group(2))
    return found


def discover_pages(acc_dir: Path, on_step=_noop) -> dict:
    """Quét các Page mà tài khoản quản lý. Trả về {"fb_user": tên, "pages": [{name, profile_id, asset_id}]}.
    asset_id (Page ID trong Business Suite) đọc từ trường delegate_page_id trong trang Page."""
    import re
    from playwright.sync_api import sync_playwright
    on_step(40, "Mở danh sách Page bạn quản lý")
    with sync_playwright() as pw:
        ctx = _context(pw, acc_dir, headless=True)
        try:
            if not logged_in(ctx):
                raise FBError("Tài khoản chưa đăng nhập")
            page = ctx.new_page()
            found: dict[str, str] = {}
            # ngay sau khi đăng nhập, danh sách Page tải chậm -> chờ tới khi số Page đứng yên (tối đa ~30s, thử 2 lần)
            for attempt in range(2):
                page.goto("https://www.facebook.com/pages/?category=your_pages", wait_until="domcontentloaded")
                prev, stable = -1, 0
                for _ in range(15):
                    page.wait_for_timeout(2000)
                    found = _page_links(page)
                    on_step(45, f"Đang đọc danh sách Page… thấy {len(found)}")
                    stable = stable + 1 if len(found) == prev and found else 0
                    prev = len(found)
                    if stable >= 2:
                        break
                if found:
                    break
            pages = []
            total = len(found)
            for i, (name, pid) in enumerate(found.items()):
                on_step(50 + 45 * i // max(total, 1), f"Lấy mã Page {i + 1}/{total}: {name}")
                url = (f"https://www.facebook.com/profile.php?id={pid}" if pid.isdigit()
                       else f"https://www.facebook.com/{pid}")
                page.goto(url, wait_until="domcontentloaded")
                m = None
                for _ in range(5):          # chờ tối đa ~10s cho tới khi có delegate_page_id
                    page.wait_for_timeout(2000)
                    m = re.search(r'"delegate_page_id":"(\d+)"', page.content())
                    if m:
                        break
                if m:
                    pages.append({"name": name, "profile_id": pid, "asset_id": m.group(1)})
            user = next((c["value"] for c in ctx.cookies("https://www.facebook.com") if c["name"] == "c_user"), "")
            on_step(100, f"Xong — tìm thấy {len(pages)} Page")
            return {"fb_user": user, "pages": pages}
        finally:
            ctx.close()


def login_and_discover(acc_dir: Path) -> dict:
    """Giao diện gọi trong tiến trình riêng: đăng nhập rồi tìm Page, ghi từng bước cho thanh tiến trình."""
    from . import loginstate as ls
    st = acc_dir / LOGIN_STATE
    login(acc_dir)
    ls.write(st, ls.FB_STEPS, "Tìm các Page")
    try:
        res = discover_pages(acc_dir)
    except Exception as e:
        ls.fail(st, f"Đã đăng nhập nhưng không tìm được Page: {e}")
        raise
    ls.write(st, ls.FB_STEPS, "Tìm các Page", state="done",
             msg=f"Đã đăng nhập Facebook, tìm thấy {len(res['pages'])} Page")
    return res
