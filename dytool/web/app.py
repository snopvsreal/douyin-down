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
    if autostart:
        worker.start()          # tự bật chạy tự động khi mở giao diện
    app.mount("/media", StaticFiles(directory=str(data)), name="media")
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    tpl = Jinja2Templates(directory=str(HERE / "templates"))
    tpl.env.globals.update(STATUS_VI=STATUS_VI, time=time)
    tpl.env.filters["ago"] = lambda ts: _ago(ts)

    def render(request: Request, name: str, **ctx):
        return tpl.TemplateResponse(request, name, {"worker": worker, **ctx})

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

    # ---------------- Tổng quan ----------------
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        pages = store.q("SELECT p.*, a.name acc_name FROM pages p JOIN accounts a ON a.id=p.account_id "
                        "WHERE p.enabled=1 ORDER BY p.id")
        stats = {p["id"]: store.counts(p["id"]) for p in pages}
        return render(request, "home.html", pages=pages, stats=stats, totals=store.counts())

    @app.get("/partials/status", response_class=HTMLResponse)
    def status_partial(request: Request):
        logs = store.q("SELECT * FROM logs ORDER BY id DESC LIMIT 25")
        return render(request, "_status.html", health=health(), logs=logs)

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
            log = open(data / "douyin_login.log", "w")
            procs["douyin"] = subprocess.Popen([sys.executable, "-m", "dytool.cli", "-c", str(ROOT / config_path),
                                                "douyin-login"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            store.log("Mở trình duyệt đăng nhập Douyin — đăng nhập xong cookie sẽ tự lưu vào config.yaml")
        return RedirectResponse("/", 303)

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
        return render(request, "accounts.html", accounts=accs, pages=pages,
                      logging_in={int(k[5:]) for k, p in procs.items() if k.startswith("login") and p.poll() is None})

    @app.post("/accounts")
    def add_account(name: str = Form(...)):
        aid = store.x("INSERT INTO accounts(name, created_at) VALUES (?,?)", (name.strip(), int(time.time())))
        store.account_dir(data, aid)
        return RedirectResponse("/accounts", 303)

    @app.post("/accounts/{aid}/login")
    def login(aid: int):
        """Mở cửa sổ Chromium để người dùng tự đăng nhập; xong thì tự quét Page."""
        acc_dir = store.account_dir(data, aid)
        code = ("import sys,json; from pathlib import Path; from dytool import fb_browser as f; "
                "d=Path(sys.argv[1]); f.login(d); print('PAGES='+json.dumps(f.discover_pages(d)))")
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

    @app.get("/partials/accounts", response_class=HTMLResponse)
    def accounts_poll(request: Request):
        # hoàn tất đăng nhập đang chờ
        for key, p in list(procs.items()):
            if key.startswith("login") and p.poll() is not None:
                aid = int(key[5:])
                out = (store.account_dir(data, aid) / "login.log").read_text(errors="ignore")
                line = next((l for l in out.splitlines() if l.startswith("PAGES=")), None)
                if line:
                    _save_discovery(aid, json.loads(line[6:]))
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
        return render(request, "page.html", page=page, s=store.page_settings(page), sources=sources,
                      stats=store.counts(pid), json=json)

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
        sql = ("SELECT v.*, p.name page_name FROM videos v JOIN pages p ON p.id=v.page_id "
               "WHERE v.status='review'" + (" AND v.page_id=?" if page else "") + " ORDER BY v.updated_at DESC")
        vids = store.q(sql, (page,) if page else ())
        sched = store.q("SELECT v.*, p.name page_name, p.asset_id FROM videos v JOIN pages p ON p.id=v.page_id "
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
    def schedule(pid: int, vid: str, when: str = Form(...), caption: str = Form(None)):
        import datetime as dt
        if caption is not None:
            store.set_video(vid, pid, caption=caption)
        try:
            ts = int(dt.datetime.strptime(when, "%Y-%m-%dT%H:%M").timestamp())
        except ValueError:
            return HTMLResponse('<div class="done" style="color:var(--err)">Giờ không hợp lệ</div>')
        if ts < time.time() + 20 * 60:
            return HTMLResponse('<div class="done" style="color:var(--err)">Facebook chỉ cho lên lịch '
                                'sau ít nhất 20 phút nữa</div>')
        store.set_video(vid, pid, status="schedule_pending", scheduled_at=ts, error=None)
        store.log(f"Lên lịch {vid} lúc {when.replace('T', ' ')}", page_id=pid)
        return HTMLResponse(f'<div class="done">Sẽ gửi lịch lên Facebook: {dt.datetime.fromtimestamp(ts):%H:%M %d/%m}</div>')

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
    def queue(request: Request, status: str | None = None):
        sql = ("SELECT v.*, p.name page_name FROM videos v JOIN pages p ON p.id=v.page_id"
               + (" WHERE v.status=?" if status else "") + " ORDER BY v.updated_at DESC LIMIT 300")
        return render(request, "queue.html", videos=store.q(sql, (status,) if status else ()),
                      totals=store.counts(), cur=status)

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
