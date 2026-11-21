"""Giao diện web quản lý nhiều tài khoản Facebook / nhiều Page.  Chạy: uv run dytool ui  -> http://127.0.0.1:8000"""

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import config
from ..store import DEFAULT_PAGE_SETTINGS, Store
from ..worker import Worker

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

STATUS_VI = {
    "new": "Mới tìm thấy", "downloaded": "Đã tải", "review": "Chờ duyệt", "approved": "Chờ đăng",
    "publish_now": "Đăng ngay", "schedule_pending": "Chờ gửi lịch lên FB", "scheduled": "Đã lên lịch trên FB", "uploaded": "Đã đăng", "rejected": "Đã bỏ", "failed": "Lỗi", "skipped": "Bỏ qua (lọc)",
}


def create_app(config_path: str = "config.yaml", autostart: bool = False) -> FastAPI:
    cfg = config.load(ROOT / config_path)
    data: Path = (ROOT / cfg["_data"]).resolve() if not Path(cfg["_data"]).is_absolute() else cfg["_data"]
    cfg["_data"] = data
    store = Store(data / "app.db")
    worker = Worker(cfg, store)
    procs: dict[str, subprocess.Popen] = {}

    app = FastAPI(title="dytool")
    app.state.worker = worker          # app desktop (desktop.py) dùng để tạm dừng / bật từ icon khay
    app.state.show_window = None       # desktop.py gán hàm đưa cửa sổ lên trước (khi mở dytool.exe lần 2)
    if autostart:
        worker.start()          # tự bật chạy tự động khi mở giao diện
    app.mount("/media", StaticFiles(directory=str(data)), name="media")
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    tpl = Jinja2Templates(directory=str(HERE / "templates"))
    # css_v: đổi theo lần sửa app.css để trình duyệt không dùng bản CSS cũ trong cache
    tpl.env.globals.update(STATUS_VI=STATUS_VI, time=time,
                           css_v=int((HERE / "static" / "app.css").stat().st_mtime))
    tpl.env.filters["ago"] = lambda ts: _ago(ts)

    def render(request: Request, name: str, **ctx):
        # navc: số video theo trạng thái cho huy hiệu trên menu (Chờ duyệt / Lỗi)
        return tpl.TemplateResponse(request, name, {"worker": worker, "navc": store.counts(), **ctx})

    def health() -> dict:
        def ok(url):
            try:
                return httpx.get(url, timeout=2).status_code < 500
            except httpx.HTTPError:
                return False
        return {
            "tdl": ok(cfg["douyin"]["api"].rstrip("/") + "/docs"),
            "voicestudio": ok(cfg["edit"].get("dub", {}).get("api", "http://127.0.0.1:3900") + "/health"),
        }

    def _sub_cfg() -> dict:
        return cfg["edit"].setdefault("subtitles", {})

    def voicestudio_exe() -> Path | None:
        import os
        for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", "")):
            p = Path(base) / ("Programs" if "Local" in base else "") / "VoiceStudio" / "VoiceStudio.exe"
            if base and p.exists():
                return p
        return None

    # ---------------- App desktop ----------------
    @app.post("/app/show")
    def app_show():
        """dytool.exe mở lần 2 gọi vào đây để đưa cửa sổ đang chạy lên trước thay vì mở thêm cửa sổ."""
        if app.state.show_window:
            app.state.show_window()
            return HTMLResponse("ok")
        return HTMLResponse("no window", status_code=404)

    @app.post("/services/voicestudio")
    def open_voicestudio():
        exe = voicestudio_exe()
        if exe:
            subprocess.Popen([str(exe)], cwd=str(exe.parent))
            store.log("Đang mở VoiceStudio — chờ khoảng 1–3 phút để app khởi động xong")
        else:
            store.log("Không tìm thấy VoiceStudio — cài từ https://github.com/debpalash/VoiceStudio/releases", "error")
        return HTMLResponse("", headers={"HX-Trigger": "services-changed"})

    @app.post("/settings/api-key")
    def save_api_key(key: str = Form("")):
        """Lưu Anthropic API key (dịch phụ đề / lồng tiếng) vào config.yaml và cấu hình đang chạy."""
        import re
        key = key.strip()
        if not key.startswith("sk-ant-"):
            return RedirectResponse("/?key_error=1", 303)
        path = ROOT / config_path
        text = path.read_text(encoding="utf-8")
        safe = key.replace('"', "")
        text, n = re.subn(r'(?m)^(\s*anthropic_api_key:\s*)"[^"\n]*"', lambda m: f'{m.group(1)}"{safe}"', text, count=1)
        if n:
            path.write_text(text, encoding="utf-8")
        _sub_cfg()["anthropic_api_key"] = key      # dùng ngay, không cần khởi động lại
        store.log("Đã lưu Anthropic API key")
        return RedirectResponse("/?key_saved=1", 303)

    # ---------------- Tổng quan ----------------
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        import os
        pages = store.q("SELECT p.*, a.name acc_name FROM pages p JOIN accounts a ON a.id=p.account_id "
                        "WHERE p.enabled=1 ORDER BY p.id")
        stats = {p["id"]: store.counts(p["id"]) for p in pages}
        need_key = not (_sub_cfg().get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY"))
        return render(request, "home.html", pages=pages, stats=stats, totals=store.counts(), need_key=need_key)

    @app.get("/partials/services", response_class=HTMLResponse)
    def services_partial(request: Request):
        from ..cookies import cookie_status
        return render(request, "_services.html", health=health(), vs_installed=bool(voicestudio_exe()),
                      douyin=cookie_status(cfg["douyin"].get("cookie", "")), dy_logging_in=_alive("douyin"))

    @app.get("/partials/log", response_class=HTMLResponse)
    def log_partial(request: Request):
        logs = store.q("SELECT * FROM logs ORDER BY id DESC LIMIT 40")
        return render(request, "_log.html", logs=logs)

    @app.get("/partials/progress", response_class=HTMLResponse)
    def progress_partial(request: Request):
        from .. import progress
        return render(request, "_progress.html", task=progress.snapshot(),
                      upcoming=worker.upcoming() if worker.running else [], backlog=worker.backlog(), now=time.time())

    @app.post("/worker/{action}")
    def worker_ctl(action: str):
        worker.start() if action == "start" else worker.stop()
        return RedirectResponse("/", 303)

    @app.post("/services/douyin-login")
    def douyin_login():
        """Mở Chromium để đăng nhập douyin.com, lưu cookie vào config (chạy được trên Windows)."""
        p = procs.get("douyin")
        if not p or p.poll() is not None:
            from .. import loginstate
            loginstate.write(data / "douyin_login.json", loginstate.DOUYIN_STEPS, "Mở trình duyệt")
            log = open(data / "douyin_login.log", "w")
            procs["douyin"] = p = subprocess.Popen([sys.executable, "-m", "dytool.cli", "-c", str(ROOT / config_path),
                                                    "douyin-login"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            store.log("Mở trình duyệt đăng nhập Douyin — đăng nhập xong cookie sẽ tự lưu vào config.yaml")

            def report():   # báo kết quả lên khung Trạng thái (trước đây lỗi chỉ nằm trong douyin_login.log)
                p.wait()
                log.close()
                lines = [l for l in (data / "douyin_login.log").read_text("utf-8", "replace").splitlines() if l.strip()]
                if p.returncode == 0:
                    # nạp cookie mới vào cấu hình đang chạy (DouyinClient giữ chính dict cfg["douyin"]),
                    # trước đây phải khởi động lại tool mới dùng được cookie vừa đăng nhập
                    cfg["douyin"]["cookie"] = config.load(ROOT / config_path)["douyin"].get("cookie", "")
                    store.log("Đăng nhập Douyin xong, đã lưu cookie")
                else:
                    err = lines[-1][:200] if lines else f"mã {p.returncode}"
                    loginstate.fail(data / "douyin_login.json", err)
                    store.log(f"Đăng nhập Douyin lỗi: {err} (chi tiết: data/douyin_login.log)", "error")
            import threading
            threading.Thread(target=report, daemon=True).start()
        return RedirectResponse("/", 303)

    def _alive(key: str) -> bool:
        p = procs.get(key)
        return bool(p and p.poll() is None)

    announced: dict[str, tuple] = {}     # bước đăng nhập đã đọc cho trình đọc màn hình, theo file trạng thái

    def _login_view(request: Request, title: str, path: Path, alive: bool, url: str):
        from .. import loginstate
        lg = loginstate.view(path, alive)   # không có lần đăng nhập nào gần đây -> trả rỗng, khung tự ẩn (CSS :empty)
        if not lg:
            return HTMLResponse("")
        # chỉ gửi câu đọc to (#sr-live) khi bước / trạng thái đổi — khung tự làm mới mỗi giây vì đồng hồ đếm ngược
        key = (lg["step"], lg["state"])
        say = None
        if announced.get(str(path)) != key:
            announced[str(path)] = key
            say = {"running": f"{title}: {lg['step']}", "done": f"{title}: xong. {lg['msg']}",
                   "error": f"{title} dừng ở bước {lg['step']}: {lg['msg']}"}[lg["state"]]
        return render(request, "_login.html", title=title, lg=lg, dismiss_url=url + "/dismiss", say=say)

    def _dismiss(path: Path, alive: bool):
        if not alive:               # chỉ ẩn khi cửa sổ đăng nhập không còn chạy
            path.unlink(missing_ok=True)
        return HTMLResponse("")

    @app.get("/partials/login/douyin", response_class=HTMLResponse)
    def douyin_login_partial(request: Request):
        return _login_view(request, "Đăng nhập Douyin", data / "douyin_login.json", _alive("douyin"),
                           "/partials/login/douyin")

    @app.post("/partials/login/douyin/dismiss", response_class=HTMLResponse)
    def douyin_login_dismiss():
        return _dismiss(data / "douyin_login.json", _alive("douyin"))

    @app.get("/partials/login/fb/{aid}", response_class=HTMLResponse)
    def fb_login_partial(request: Request, aid: int):
        acc = store.one("SELECT platform FROM accounts WHERE id=?", (aid,))
        title = "Đăng nhập TikTok" if acc and acc["platform"] == "tiktok" else "Đăng nhập Facebook"
        return _login_view(request, title, store.account_dir(data, aid) / "login_state.json",
                           _alive(f"login{aid}"), f"/partials/login/fb/{aid}")

    @app.post("/partials/login/fb/{aid}/dismiss", response_class=HTMLResponse)
    def fb_login_dismiss(aid: int):
        return _dismiss(store.account_dir(data, aid) / "login_state.json", _alive(f"login{aid}"))

    @app.post("/services/tdl")
    def start_tdl():
        import threading
        from ..services import ensure_tdl
        threading.Thread(target=ensure_tdl, args=(cfg, config_path), daemon=True).start()
        store.log("Đang bật backend tải Douyin (TikTokDownloader)...")
        return RedirectResponse("/", 303)

    # ---------------- Tài khoản & Page ----------------
    @app.get("/accounts", response_class=HTMLResponse)
    def accounts(request: Request):
        accs = store.q("SELECT * FROM accounts ORDER BY id")
        pages = store.q("SELECT * FROM pages ORDER BY account_id, id")
        mirrors: dict[int, list[str]] = {}     # kênh TikTok -> các Page Facebook đang đăng kèm vào nó
        for p in pages:
            dst = int(store.page_settings(p).get("tiktok_mirror") or 0)
            if p["platform"] != "tiktok" and dst:
                mirrors.setdefault(dst, []).append(p["name"])
        return render(request, "accounts.html", accounts=accs, pages=pages, mirrors=mirrors,
                      logging_in={int(k[5:]) for k, p in procs.items() if k.startswith("login") and p.poll() is None})

    @app.post("/accounts")
    def add_account(name: str = Form(...), platform: str = Form("facebook")):
        platform = "tiktok" if platform == "tiktok" else "facebook"
        aid = store.x("INSERT INTO accounts(name, created_at, platform) VALUES (?,?,?)",
                      (name.strip(), int(time.time()), platform))
        store.account_dir(data, aid)
        return RedirectResponse("/accounts" + ("#tiktok" if platform == "tiktok" else ""), 303)

    @app.post("/accounts/{aid}/login")
    def login(aid: int):
        """Mở cửa sổ Chromium để người dùng tự đăng nhập; xong thì tự quét Page (Facebook) / lấy tên kênh (TikTok)."""
        acc_dir = store.account_dir(data, aid)
        acc = store.one("SELECT * FROM accounts WHERE id=?", (aid,))
        from .. import loginstate
        if acc and acc["platform"] == "tiktok":
            code = ("import sys,json; from pathlib import Path; from dytool import tt_browser as t; "
                    "d=Path(sys.argv[1]); print('TT='+json.dumps(t.login(d)))")
            steps = loginstate.TT_STEPS
        else:
            code = ("import sys,json; from pathlib import Path; from dytool import fb_browser as f; "
                    "d=Path(sys.argv[1]); print('PAGES='+json.dumps(f.login_and_discover(d)))")
            steps = loginstate.FB_STEPS
        (acc_dir / "login_state.json").unlink(missing_ok=True)   # tránh hiện lại kết quả lần trước
        loginstate.write(acc_dir / "login_state.json", steps, "Mở trình duyệt")
        log = open(acc_dir / "login.log", "w")
        procs[f"login{aid}"] = subprocess.Popen([sys.executable, "-c", code, str(acc_dir)], cwd=ROOT,
                                               stdout=log, stderr=subprocess.STDOUT)
        store.x("UPDATE accounts SET status='dang_dang_nhap' WHERE id=?", (aid,))
        store.log(f"Mở trình duyệt đăng nhập cho tài khoản #{aid}")
        return RedirectResponse("/accounts", 303)

    @app.post("/accounts/{aid}/discover")
    def discover(aid: int):
        from .. import fb_browser
        acc_dir = store.account_dir(data, aid)
        try:
            res = fb_browser.discover_pages(acc_dir)
        except Exception as e:
            store.log(f"Tài khoản #{aid}: không quét được Page ({e})", "error")
            store.x("UPDATE accounts SET status='chua_dang_nhap' WHERE id=?", (aid,))
            return RedirectResponse("/accounts", 303)
        _save_discovery(aid, res)
        return RedirectResponse("/accounts", 303)

    def _save_discovery(aid: int, res: dict):
        for p in res["pages"]:
            store.upsert_page(aid, p["name"], p["profile_id"], p["asset_id"])
        store.x("UPDATE accounts SET status='da_dang_nhap', fb_user=? WHERE id=?", (res.get("fb_user"), aid))
        store.log(f"Tài khoản #{aid}: tìm thấy {len(res['pages'])} Page")

    def _save_tiktok(aid: int, who: dict):
        """Tài khoản TikTok có đúng 1 kênh đăng (dòng pages platform='tiktok'), mặc định tắt."""
        acc = store.one("SELECT * FROM accounts WHERE id=?", (aid,))
        user = who.get("username") or ""
        name = f"@{user}" if user else (acc["name"] if acc else f"TikTok #{aid}")
        old = store.one("SELECT id FROM pages WHERE account_id=? AND platform='tiktok'", (aid,))
        if old:     # đăng nhập lại: cập nhật tên, giữ cài đặt / nguồn của kênh
            store.x("UPDATE pages SET name=?, profile_id=?, asset_id=? WHERE id=?",
                    (name, user, user or f"tt{aid}", old["id"]))
        else:
            store.upsert_page(aid, name, user, user or f"tt{aid}", platform="tiktok")
        store.x("UPDATE accounts SET status='da_dang_nhap', fb_user=? WHERE id=?", (user, aid))
        store.log(f"Tài khoản TikTok #{aid}: đã đăng nhập {name}")

    @app.get("/partials/accounts", response_class=HTMLResponse)
    def accounts_poll(request: Request):
        # hoàn tất đăng nhập đang chờ
        for key, p in list(procs.items()):
            if key.startswith("login") and p.poll() is not None:
                aid = int(key[5:])
                out = (store.account_dir(data, aid) / "login.log").read_text(errors="ignore")
                line = next((l for l in out.splitlines() if l.startswith("PAGES=")), None)
                tt = next((l for l in out.splitlines() if l.startswith("TT=")), None)
                if line:
                    _save_discovery(aid, json.loads(line[6:]))
                elif tt:
                    _save_tiktok(aid, json.loads(tt[3:]))
                else:
                    store.x("UPDATE accounts SET status='chua_dang_nhap' WHERE id=?", (aid,))
                    store.log(f"Tài khoản #{aid}: đăng nhập chưa xong", "error")
                procs.pop(key)
                # báo trình duyệt tải lại trang để hiện Page vừa tìm thấy
                return HTMLResponse("", headers={"HX-Refresh": "true"})
        return HTMLResponse("")

    @app.post("/accounts/{aid}/delete")
    def delete_account(aid: int):
        store.x("DELETE FROM accounts WHERE id=?", (aid,))
        return RedirectResponse("/accounts", 303)

    # ---------------- Cài đặt Page ----------------
    @app.get("/pages/{pid}", response_class=HTMLResponse)
    def page_view(request: Request, pid: int):
        page = store.one("SELECT p.*, a.name acc_name FROM pages p JOIN accounts a ON a.id=p.account_id "
                         "WHERE p.id=?", (pid,))
        sources = store.q("SELECT * FROM sources WHERE page_id=? ORDER BY id", (pid,))
        mirror = store.q("SELECT c.*, p.name dst_name FROM crossposts c JOIN pages p ON p.id=c.dst_page_id "
                         "WHERE c.src_page_id=? ORDER BY c.updated_at DESC LIMIT 10", (pid,))
        return render(request, "page.html", page=page, s=store.page_settings(page), sources=sources,
                      stats=store.counts(pid), json=json, tiktoks=store.tiktok_channels(), mirror=mirror)

    @app.post("/crossposts/{src}/{dst}/{vid}/retry")
    def crosspost_retry(src: int, dst: int, vid: str):
        store.set_crosspost(vid, src, dst, status="pending", error=None)
        return HTMLResponse('<span class="pill info">Chờ đăng lại</span>')

    @app.post("/pages/{pid}/settings")
    async def page_save(request: Request, pid: int):
        form = await request.form()
        s = store.page_settings(store.one("SELECT * FROM pages WHERE id=?", (pid,)))
        for k, default in DEFAULT_PAGE_SETTINGS.items():
            if isinstance(default, bool):
                s[k] = form.get(k) == "on"
            elif k in form:
                v = form.get(k)
                s[k] = type(default)(v) if isinstance(default, (int, float)) and v != "" else v
        store.save_page_settings(pid, s)
        store.x("UPDATE pages SET enabled=? WHERE id=?", (1 if form.get("enabled") == "on" else 0, pid))
        store.log(f"Đã lưu cài đặt Page #{pid}", page_id=pid)
        return RedirectResponse(f"/pages/{pid}?saved=1", 303)

    @app.post("/pages/{pid}/sources")
    def add_source(pid: int, kind: str = Form(...), value: str = Form(...), max_results: int = Form(20),
                   publish_time: int = Form(1), sort_type: int = Form(1), max_age_days: float = Form(3)):
        params = ({"max_age_days": max_age_days} if kind == "channel"
                  else {"max_results": max_results, "publish_time": publish_time, "sort_type": sort_type})
        store.x("INSERT INTO sources(page_id, kind, value, params) VALUES (?,?,?,?)",
                (pid, kind, value.strip(), json.dumps(params)))
        return RedirectResponse(f"/pages/{pid}", 303)

    @app.post("/sources/{sid}/update")
    def update_source(sid: int, kind: str = Form(...), value: str = Form(...), max_results: int = Form(20),
                      publish_time: int = Form(1), sort_type: int = Form(1), max_age_days: float = Form(3),
                      enabled: str = Form(None)):
        row = store.one("SELECT page_id FROM sources WHERE id=?", (sid,))
        params = ({"max_age_days": max_age_days} if kind == "channel"
                  else {"max_results": max_results, "publish_time": publish_time, "sort_type": sort_type})
        store.x("UPDATE sources SET kind=?, value=?, params=?, enabled=? WHERE id=?",
                (kind, value.strip(), json.dumps(params), 1 if enabled == "on" else 0, sid))
        store.log(f"Đã sửa nguồn '{value.strip()}'", page_id=row["page_id"])
        return RedirectResponse(f"/pages/{row['page_id']}?saved=1", 303)

    @app.post("/sources/{sid}/toggle")
    def toggle_source(sid: int):
        row = store.one("SELECT page_id FROM sources WHERE id=?", (sid,))
        store.x("UPDATE sources SET enabled=1-enabled WHERE id=?", (sid,))
        return RedirectResponse(f"/pages/{row['page_id']}", 303)

    @app.post("/sources/{sid}/delete")
    def del_source(sid: int):
        row = store.one("SELECT page_id FROM sources WHERE id=?", (sid,))
        store.x("DELETE FROM sources WHERE id=?", (sid,))
        return RedirectResponse(f"/pages/{row['page_id']}", 303)

    @app.post("/pages/{pid}/scan")
    def scan_now(pid: int):
        worker.scan_now(pid)
        store.log(f"Yêu cầu quét ngay Page #{pid}", page_id=pid)
        return RedirectResponse(f"/pages/{pid}", 303)

    # ---------------- Duyệt video ----------------
    @app.get("/review", response_class=HTMLResponse)
    def review(request: Request, page: int | None = None):
        sql = ("SELECT v.*, p.name page_name, p.platform FROM videos v JOIN pages p ON p.id=v.page_id "
               "WHERE v.status='review'" + (" AND v.page_id=?" if page else "") + " ORDER BY v.updated_at DESC")
        vids = store.q(sql, (page,) if page else ())
        sched = store.q("SELECT v.*, p.name page_name, p.asset_id, p.platform FROM videos v JOIN pages p ON p.id=v.page_id "
                        "WHERE v.status IN ('schedule_pending','scheduled')" + (" AND v.page_id=?" if page else "")
                        + " ORDER BY v.scheduled_at", (page,) if page else ())
        return render(request, "review.html", videos=vids, sched=sched, suggest=suggest_slot,
                      pages=store.q("SELECT * FROM pages WHERE enabled=1"), cur_page=page)

    def suggest_slot(page_id: int) -> str:
        """Giờ gợi ý: sau giờ đã lên lịch muộn nhất của Page + khoảng cách tối thiểu, ít nhất 30 phút nữa."""
        import datetime as dt
        page = store.one("SELECT * FROM pages WHERE id=?", (page_id,))
        gap = float(store.page_settings(page)["min_gap_minutes"]) * 60
        last = store.one("SELECT COALESCE(MAX(scheduled_at),0) t FROM videos WHERE page_id=? AND "
                         "status IN ('schedule_pending','scheduled')", (page_id,))["t"]
        t = max(time.time() + 1800, last + gap)
        t = int((t + 299) // 300 * 300)      # làm tròn lên 5 phút
        return dt.datetime.fromtimestamp(t).strftime("%Y-%m-%dT%H:%M")

    @app.post("/videos/{pid}/{vid}/schedule")
    def schedule(pid: int, vid: str, when: str = Form(""), caption: str = Form(None)):
        # when mặc định "": ô giờ để trống -> báo lỗi rõ ràng thay vì 422 (htmx bỏ qua, người dùng không thấy gì)
        import datetime as dt
        if caption is not None:
            store.set_video(vid, pid, caption=caption)
        pg = store.one("SELECT platform FROM pages WHERE id=?", (pid,))
        where = "TikTok" if pg and pg["platform"] == "tiktok" else "Facebook"

        def err(msg: str) -> HTMLResponse:
            # lỗi hiện ngay dưới ô chọn giờ, giữ nguyên các nút Duyệt / Đăng ngay
            return HTMLResponse(msg, headers={"HX-Retarget": f"#v-{pid}-{vid} .sched-msg", "HX-Reswap": "innerHTML"})
        try:
            ts = int(dt.datetime.strptime(when, "%Y-%m-%dT%H:%M").timestamp())
        except ValueError:
            return err("Chưa chọn đủ ngày và giờ — chọn lại trong ô bên trên rồi bấm Lên lịch.")
        if ts < time.time() + 20 * 60:
            earliest = dt.datetime.fromtimestamp(time.time() + 21 * 60)
            return err(f"{where} chỉ nhận lịch sau ít nhất 20 phút nữa — chọn từ {earliest:%H:%M} trở đi.")
        store.set_video(vid, pid, status="schedule_pending", scheduled_at=ts, error=None)
        store.log(f"Lên lịch {vid} lúc {when.replace('T', ' ')}", page_id=pid)
        note = " (TikTok chỉ nhận lịch trong 10 ngày — tool sẽ gửi khi tới gần ngày)" \
            if where == "TikTok" and ts > time.time() + 10 * 86400 - 3600 else ""
        return HTMLResponse(f'<div class="done">Sẽ gửi lịch lên {where}: {dt.datetime.fromtimestamp(ts):%H:%M %d/%m}{note}</div>')

    @app.post("/videos/{pid}/{vid}/{action}")
    def video_action(pid: int, vid: str, action: str, caption: str = Form(None)):
        if caption is not None:
            store.set_video(vid, pid, caption=caption)
        new = {"approve": "approved", "publish_now": "publish_now", "reject": "rejected",
               "retry": "downloaded", "save": None, "unschedule": "review"}.get(action)
        if new:
            store.set_video(vid, pid, status=new, error=None)
        return HTMLResponse(f'<div class="done">{STATUS_VI.get(new, "Đã lưu") if new else "Đã lưu caption"}</div>')

    # ---------------- Tiến trình ----------------
    @app.get("/queue", response_class=HTMLResponse)
    def queue(request: Request, status: str | None = None, page: int = 1):
        per = 100       # chia trang thay vì vẽ 300 dòng một lúc
        page = max(1, page)
        where, args = (" WHERE v.status=?", (status,)) if status else ("", ())
        total = store.one("SELECT COUNT(*) n FROM videos v" + where, args)["n"]
        sql = ("SELECT v.*, p.name page_name FROM videos v JOIN pages p ON p.id=v.page_id" + where
               + " ORDER BY v.updated_at DESC LIMIT ? OFFSET ?")
        return render(request, "queue.html", videos=store.q(sql, (*args, per, (page - 1) * per)),
                      totals=store.counts(), cur=status, page=page, pages=max(1, -(-total // per)), total=total)

    return app


def _ago(ts) -> str:
    if not ts:
        return "—"
    d = int(time.time()) - int(ts)
    if d < 60:
        return "vừa xong"
    if d < 3600:
        return f"{d // 60} phút trước"
    if d < 86400:
        return f"{d // 3600} giờ trước"
    return f"{d // 86400} ngày trước"
