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
    "uploading": "Đang đăng",
}


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _request_guard(lan: bool):
    """Chặn 2 kiểu tấn công vào server cục bộ không có đăng nhập:
    - DNS rebinding: tên miền lạ trỏ về 127.0.0.1 để đọc dữ liệu -> chỉ nhận Host là 127.0.0.1 / localhost
      (chế độ --lan mở cho máy khác cùng mạng thì bỏ qua bước này).
    - CSRF: trang web lạ đang mở trong trình duyệt tự gửi form POST tới 127.0.0.1:8000 (bật Page, tự đăng, xoá
      tài khoản, chạy cập nhật…) -> thao tác ghi có Origin / Referer thì phải trùng chính dytool.
      Không có cả 2 header (dytool.exe gọi /app/show, công cụ dòng lệnh) thì cho qua: trình duyệt luôn gửi Origin
      khi một trang khác gửi form POST."""
    from urllib.parse import urlsplit
    from fastapi.responses import PlainTextResponse

    async def guard(request: Request, call_next):
        host = request.headers.get("host") or ""
        if not lan and urlsplit("//" + host).hostname not in LOCAL_HOSTS:
            return PlainTextResponse("dytool chỉ nhận truy cập từ 127.0.0.1 / localhost", status_code=400)
        if request.method not in SAFE_METHODS:
            src = request.headers.get("origin") or request.headers.get("referer")
            if src is not None and (src == "null" or urlsplit(src).netloc.lower() != host.lower()):
                return PlainTextResponse("Yêu cầu đến từ trang khác — đã chặn", status_code=403)
        return await call_next(request)
    return guard


def create_app(config_path: str = "config.yaml", autostart: bool = False, lan: bool = False) -> FastAPI:
    cfg = config.load(ROOT / config_path)
    data: Path = (ROOT / cfg["_data"]).resolve() if not Path(cfg["_data"]).is_absolute() else cfg["_data"]
    cfg["_data"] = data
    store = Store(data / "app.db")
    worker = Worker(cfg, store)
    procs: dict[str, subprocess.Popen] = {}

    app = FastAPI(title="dytool")
    app.middleware("http")(_request_guard(lan))
    app.state.worker = worker         # app desktop (desktop.py) dùng để tạm dừng / bật từ icon khay
    app.state.show_window = None       # desktop.py gán hàm đưa cửa sổ lên trước (khi mở dytool.exe lần 2)
    if autostart:
        worker.start()          # tự bật chạy tự động khi mở giao diện
        from .. import updates
        updates.start_background_checks()   # hỏi GitHub có bản mới của dytool / VoiceStudio / ShardBrowser (6 giờ / lần)
    # chỉ phục vụ video đã edit (xem trước ở Duyệt video). KHÔNG mount cả data/: trong đó có app.db,
    # cookie Facebook (accounts/<id>/fb_state.json), nhật ký đăng nhập
    (data / "edited").mkdir(parents=True, exist_ok=True)
    app.mount("/media/edited", StaticFiles(directory=str(data / "edited")), name="media")
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    tpl = Jinja2Templates(directory=str(HERE / "templates"))
    # css_v(): đổi theo lần sửa app.css (đọc lại mỗi trang) để trình duyệt không dùng bản CSS cũ trong cache
    from ..store import vn_fmt
    tpl.env.globals.update(STATUS_VI=STATUS_VI, time=time, vnt=vn_fmt,     # vnt(ts, fmt): hiện giờ theo giờ VN
                           css_v=lambda: int((HERE / "static" / "app.css").stat().st_mtime))
    tpl.env.filters["ago"] = lambda ts: _ago(ts)

    def _setup_failed() -> str | None:
        """Lần cài / cập nhật thư viện gần nhất chưa xong (setup_app.ps1 ghi data/setup_failed.txt) -> nội dung lỗi."""
        try:
            return (data / "setup_failed.txt").read_text(encoding="utf-8-sig").strip() or "không rõ lỗi"
        except OSError:
            return None

    def render(request: Request, name: str, **ctx):
        # navc: số video theo trạng thái cho huy hiệu trên menu (Chờ duyệt / Lỗi)
        return tpl.TemplateResponse(request, name, {"worker": worker, "navc": store.counts(),
                                                    "setup_failed": _setup_failed(), **ctx})

    @app.post("/settings/reinstall-libs")
    def reinstall_libs():
        """Mở cửa sổ cài lại thư viện (setup_app.ps1, có hiện tiến độ). Script tự đóng dytool trước khi cài
        (-Launch: cài xong mở lại)."""
        script = ROOT / "scripts" / "setup_app.ps1"
        flags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
        subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                          "-Launch"], cwd=str(ROOT), creationflags=flags)
        store.log("Đang cài lại thư viện — dytool sẽ tự đóng rồi mở lại khi xong")
        return HTMLResponse('<div class="done">Đã mở cửa sổ cài lại thư viện</div>')

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
        # chỉ chữ / số / - / _ : ký tự lạ (" \ xuống dòng) ghi vào config.yaml sẽ làm hỏng file -> app không mở được
        if not re.fullmatch(r"sk-ant-[A-Za-z0-9_-]{10,300}", key):
            return RedirectResponse("/settings?key_error=1#api-key", 303)
        path = ROOT / config_path
        text = path.read_text(encoding="utf-8")
        text, n = re.subn(r'(?m)^(\s*anthropic_api_key:\s*)"[^"\n]*"', lambda m: f'{m.group(1)}"{key}"', text, count=1)
        if not n:
            store.log("Không ghi được API key vào config.yaml (không thấy dòng anthropic_api_key) — "
                      "key chỉ dùng tới khi tắt app", "error")
        else:
            path.write_text(text, encoding="utf-8")
        _sub_cfg()["anthropic_api_key"] = key      # dùng ngay, không cần khởi động lại
        store.log("Đã lưu Anthropic API key")
        return RedirectResponse("/settings?key_saved=1#api-key", 303)

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
        from .. import progress, updates
        from ..cookies import cookie_status
        return render(request, "_services.html", health=health(), vs_installed=bool(voicestudio_exe()),
                      task=progress.snapshot(), n_updates=updates.pending(),
                      douyin=cookie_status(cfg["douyin"].get("cookie", "")), dy_logging_in=_alive("douyin"))

    # ---------------- Cập nhật (dytool / VoiceStudio / ShardBrowser) ----------------
    @app.get("/partials/updates", response_class=HTMLResponse)
    def updates_partial(request: Request, msg: str = ""):
        from .. import updates
        updates.check_soon()
        rows = updates.status()
        return render(request, "_updates.html", rows=rows, msg=msg, checked_at=updates.checked_at(),
                      check_error=updates.check_error(),
                      busy=not updates.checked_at() or
                      any(r["job"] and r["job"]["state"] in ("downloading", "installing") for r in rows))

    @app.post("/updates/check", response_class=HTMLResponse)
    def updates_check(request: Request):
        from .. import updates
        updates.check(force=True)
        return updates_partial(request)

    @app.post("/updates/{name}", response_class=HTMLResponse)
    def updates_start(request: Request, name: str):
        from .. import updates
        why = updates.start(name)
        label = updates.APPS.get(name, {}).get("name", name)
        if why:
            store.log(f"Chưa cập nhật {label}: {why}", "error")
        else:
            store.log(f"Đang cập nhật {label}…")
        return updates_partial(request, msg=f"{label}: {why}" if why else "")

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

    @app.post("/settings/open-folder")
    def open_folder(which: str = Form("data")):
        """Mở thư mục dữ liệu / thư mục cài đặt trong File Explorer (chỉ 2 thư mục cố định này)."""
        import os
        folder = data if which == "data" else ROOT
        if hasattr(os, "startfile"):
            os.startfile(str(folder))
        return RedirectResponse("/settings#thong-tin", 303)

    @app.post("/services/douyin-login")
    def douyin_login(request: Request):
        """Mở hồ sơ ShardBrowser dành cho Douyin để đăng nhập douyin.com, lưu cookie vào config."""
        if not _auto_profile(data, "dytool · Douyin", "Đăng nhập Douyin"):
            return RedirectResponse("/settings#trinh-duyet", 303)
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
        # bấm từ trang Cài đặt -> quay lại mục Douyin (có thanh tiến trình đăng nhập ở đó); nơi khác -> Tổng quan
        back = "/settings#douyin" if "/settings" in (request.headers.get("referer") or "") else "/"
        return RedirectResponse(back, 303)

    def _alive(key: str) -> bool:
        p = procs.get(key)
        return bool(p and p.poll() is None)

    announced: dict[str, tuple] = {}     # bước đăng nhập đã đọc cho trình đọc màn hình, theo file trạng thái

    def _login_view(request: Request, title: str, path: Path, alive: bool, url: str):
        from .. import loginstate
        lg = loginstate.view(path, alive)   # không có lần đăng nhập nào gần đây -> trả rỗng, khung tự ẩn (CSS :empty)
        if not lg:
            # 286 = htmx dừng hỏi lại (trước: mỗi tài khoản + Douyin hỏi mỗi giây mãi mãi). Bấm Đăng nhập -> trang
            # tải lại -> khung bắt đầu theo dõi lại
            return HTMLResponse("", status_code=286)
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
        from .. import browsers
        choices = {a["id"]: browsers.get_choice(store.account_dir(data, a["id"])) for a in accs}
        return render(request, "accounts.html", accounts=accs, pages=pages, mirrors=mirrors, shard=_shard_info(),
                      choices=choices,
                      logging_in={int(k[5:]) for k, p in procs.items() if k.startswith("login") and p.poll() is None})

    def _shard_info() -> dict:
        """Trạng thái ShardBrowser cho trang Tài khoản (ô đổi hồ sơ) và trang Cài đặt (kết nối, hồ sơ Douyin)."""
        from .. import browsers
        api, token = browsers.shardx_settings()
        shard = {"api": api, "has_token": bool(token), "profiles": [], "error": None,
                 "installed": browsers.launcher_exe() is not None, "download": browsers.DOWNLOAD,
                 "douyin": browsers.get_choice(data), "auto": browsers.auto_token()}
        if token:
            try:
                shard["profiles"] = browsers.list_profiles(start_launcher=False)   # không treo trang ~45s
            except browsers.BrowserError as e:
                shard["error"] = str(e)
        return shard

    # ---------------- Cài đặt chung (cập nhật, API key, trình duyệt, Douyin, thông tin) ----------------
    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        import os
        from .. import updates
        from ..cookies import cookie_status
        key = _sub_cfg().get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY") or ""
        # tăng tốc AI: đọc phiên bản torch (bản "+cu…" = có CUDA), không import torch (không khởi động CUDA / tốn VRAM)
        accel = {"gpu": False, "torch": "", "card": ""}
        try:
            from importlib.metadata import version as _v
            accel["torch"] = _v("torch")
            accel["gpu"] = "+cu" in accel["torch"]
            if accel["gpu"]:
                q = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True,
                                   text=True, timeout=5)
                accel["card"] = q.stdout.strip().splitlines()[0] if q.returncode == 0 and q.stdout.strip() else ""
        except Exception:
            pass
        return render(request, "settings.html", shard=_shard_info(),
                      key_tail=key[-4:] if key else "", key_env=bool(not _sub_cfg().get("anthropic_api_key") and key),
                      douyin=cookie_status(cfg["douyin"].get("cookie", "")), dy_logging_in=_alive("douyin"),
                      version=updates.installed("dytool"), root=str(ROOT), data_dir=str(data),
                      config_file=str(ROOT / config_path), accel=accel)

    # ---------------- Trình duyệt (Chromium / ShardBrowser) ----------------
    @app.post("/settings/shardx")
    def save_shardx(api: str = Form(""), token: str = Form("")):
        """Lưu API + token ShardBrowser vào config.yaml (mục browser:), giữ nguyên các dòng khác."""
        import re
        api = (api.strip() or "http://127.0.0.1:40325").rstrip("/")
        token = token.strip()
        # địa chỉ chỉ gồm http(s)://tên-máy[:cổng]; token chỉ ký tự của JWT — chặn ký tự làm hỏng config.yaml
        if not re.fullmatch(r"https?://[A-Za-z0-9.\-]+(:\d{1,5})?", api) or \
                (token and not re.fullmatch(r"[A-Za-z0-9._\-]{10,4000}", token)):
            store.log("Địa chỉ API / token ShardBrowser không hợp lệ — chưa lưu", "error")
            return RedirectResponse("/settings?browser_error=1#trinh-duyet", 303)
        path = ROOT / config_path
        text = path.read_text(encoding="utf-8")
        if re.search(r"(?m)^browser:", text):
            for key, val in (("shardx_api", api), ("shardx_token", token)):
                if not token and key == "shardx_token":
                    continue                      # để trống ô token = giữ token cũ
                pat = rf'(?m)^(\s+{key}:\s*)"[^"\n]*"'
                text = re.sub(pat, lambda m: f'{m.group(1)}"{val}"', text, count=1) if re.search(pat, text) \
                    else re.sub(r"(?m)^browser:.*$", lambda m: f'{m.group(0)}\n  {key}: "{val}"', text, count=1)
        else:
            text = text.rstrip("\n") + f'\n\nbrowser:\n  shardx_api: "{api}"\n  shardx_token: "{token}"\n'
        path.write_text(text, encoding="utf-8")
        store.log("Đã lưu cài đặt ShardBrowser")
        return RedirectResponse("/settings?browser_saved=1#trinh-duyet", 303)

    def _auto_profile(target: Path, name: str, who: str, quiet: bool = False) -> bool:
        """Chưa gắn hồ sơ -> tự tạo hồ sơ ShardBrowser mới và gắn luôn (người dùng không phải liên kết tay)."""
        from .. import browsers
        try:
            c, new = browsers.ensure_profile(target, name)
        except browsers.BrowserError as e:
            if not quiet:
                store.log(f"{who}: không tạo được hồ sơ ShardBrowser — {e}", "error")
            return False
        if new:
            store.log(f"{who}: đã tạo hồ sơ ShardBrowser '{c['profile_name']}' (thư mục {browsers.FOLDER})")
        return True

    def _acc_profile_name(acc) -> str:
        plat = "TikTok" if acc["platform"] == "tiktok" else "Facebook"
        return f"dytool · {plat} · {acc['name']}"

    @app.post("/accounts/{aid}/browser/new")
    def new_account_profile(aid: int):
        """Tạo hồ sơ mới thay cho hồ sơ đang gắn (hồ sơ cũ vẫn còn trong ShardBrowser)."""
        from .. import browsers
        acc = store.one("SELECT * FROM accounts WHERE id=?", (aid,))
        if acc:
            acc_dir = store.account_dir(data, aid)
            old = browsers.get_choice(acc_dir)
            browsers.set_choice(acc_dir, "")
            if not _auto_profile(acc_dir, _acc_profile_name(acc), f"Tài khoản #{aid}") and old:
                browsers.set_choice(acc_dir, old["profile_id"], old.get("profile_name", ""))
        return RedirectResponse("/accounts", 303)

    def _set_profile(target: Path, choice: str, who: str):
        """choice = '<profile_id>|<tên hồ sơ>' hoặc '' (bỏ chọn)."""
        from .. import browsers
        pid, _, name = choice.partition("|")
        browsers.set_choice(target, pid, name)
        store.log(f"{who}: dùng hồ sơ ShardBrowser '{name}' — bấm Đăng nhập để đăng nhập trong hồ sơ đó" if pid
                  else f"{who}: đã bỏ chọn hồ sơ ShardBrowser")

    @app.post("/accounts/{aid}/browser")
    def set_account_browser(aid: int, choice: str = Form("")):
        _set_profile(store.account_dir(data, aid), choice, f"Tài khoản #{aid}")
        return RedirectResponse("/accounts", 303)

    @app.post("/settings/douyin-browser")
    def set_douyin_browser(choice: str = Form("")):
        _set_profile(data, choice, "Đăng nhập Douyin")
        return RedirectResponse("/settings#trinh-duyet", 303)

    @app.post("/settings/douyin-browser/new")
    def new_douyin_profile():
        from .. import browsers
        old = browsers.get_choice(data)
        browsers.set_choice(data, "")
        if not _auto_profile(data, "dytool · Douyin", "Đăng nhập Douyin") and old:
            browsers.set_choice(data, old["profile_id"], old.get("profile_name", ""))
        return RedirectResponse("/settings#trinh-duyet", 303)

    @app.post("/accounts")
    def add_account(name: str = Form(...), platform: str = Form("facebook")):
        platform = "tiktok" if platform == "tiktok" else "facebook"
        aid = store.x("INSERT INTO accounts(name, created_at, platform) VALUES (?,?,?)",
                      (name.strip(), int(time.time()), platform))
        acc = store.one("SELECT * FROM accounts WHERE id=?", (aid,))
        # tạo sẵn hồ sơ nếu ShardBrowser đang chạy; không được thì để tới lúc bấm Đăng nhập
        _auto_profile(store.account_dir(data, aid), _acc_profile_name(acc), f"Tài khoản #{aid}", quiet=True)
        return RedirectResponse("/accounts" + ("#tiktok" if platform == "tiktok" else ""), 303)

    @app.post("/accounts/{aid}/login")
    def login(aid: int):
        """Mở hồ sơ ShardBrowser của tài khoản để người dùng tự đăng nhập; xong thì tự quét Page / lấy tên kênh."""
        acc_dir = store.account_dir(data, aid)
        acc = store.one("SELECT * FROM accounts WHERE id=?", (aid,))
        if not acc:
            return RedirectResponse("/accounts", 303)
        if not _auto_profile(acc_dir, _acc_profile_name(acc), f"Tài khoản #{aid}"):
            return RedirectResponse("/settings#trinh-duyet", 303)
        from .. import loginstate
        if acc and acc["platform"] == "tiktok":
            code = ("import sys,json; from pathlib import Path; from dytool import tt_browser as t; "
                    "d=Path(sys.argv[1]); print('TT='+json.dumps(t.login(d)))")
            steps = loginstate.TT_STEPS
        else:
            code = ("import sys,json; from pathlib import Path; from dytool import fb_browser as f; "
                    "d=Path(sys.argv[1]); print('PAGES='+json.dumps(f.login_and_discover(d)))")
            steps = loginstate.FB_STEPS
        key = f"login{aid}"
        if procs.get(key) and procs[key].poll() is None:
            store.log(f"Tài khoản #{aid}: cửa sổ đăng nhập đang mở rồi — đăng nhập trong cửa sổ đó")
            return RedirectResponse("/accounts", 303)
        (acc_dir / "login_state.json").unlink(missing_ok=True)   # tránh hiện lại kết quả lần trước
        loginstate.write(acc_dir / "login_state.json", steps, "Mở trình duyệt")
        log = open(acc_dir / "login.log", "w")
        p = procs[key] = subprocess.Popen([sys.executable, "-c", code, str(acc_dir)], cwd=ROOT,
                                          stdout=log, stderr=subprocess.STDOUT)
        store.x("UPDATE accounts SET status='dang_dang_nhap' WHERE id=?", (aid,))
        store.log(f"Mở trình duyệt đăng nhập cho tài khoản #{aid}")

        def watch():    # lưu kết quả ngay khi đăng nhập xong, kể cả khi người dùng đã rời trang Tài khoản
            p.wait()
            log.close()
            _finish_login(key)
        import threading
        threading.Thread(target=watch, daemon=True).start()
        return RedirectResponse("/accounts", 303)

    import threading as _threading
    login_lock = _threading.Lock()

    def _finish_login(key: str) -> bool:
        """Đọc kết quả tiến trình đăng nhập đã xong (login.log) -> lưu Page / kênh. Gọi từ luồng theo dõi hoặc lần
        làm mới trang; chạy đúng 1 lần cho mỗi lần đăng nhập. True = vừa xử lý xong 1 lần đăng nhập."""
        with login_lock:
            p = procs.get(key)
            if not p or p.poll() is None:
                return False
            procs.pop(key)
        aid = int(key[5:])
        if not store.one("SELECT 1 FROM accounts WHERE id=?", (aid,)):
            return True         # tài khoản đã bị xoá trong lúc đăng nhập
        try:
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
        except Exception as e:
            store.log(f"Tài khoản #{aid}: không lưu được kết quả đăng nhập: {e}", "error")
        return True

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
        # hoàn tất đăng nhập (thường luồng watch() đã làm); hết cửa sổ đăng nhập nào còn mở -> tải lại trang để hiện
        # Page / kênh vừa tìm thấy (khung poll này chỉ có trên trang khi lúc vẽ còn đang đăng nhập)
        for key in [k for k in procs if k.startswith("login")]:
            _finish_login(key)
        if not any(k.startswith("login") and p.poll() is None for k, p in procs.items()):
            return HTMLResponse("", headers={"HX-Refresh": "true"})
        return HTMLResponse("")

    @app.post("/accounts/{aid}/delete")
    def delete_account(aid: int):
        acc = store.one("SELECT * FROM accounts WHERE id=?", (aid,))
        p = procs.pop(f"login{aid}", None)       # đang mở cửa sổ đăng nhập -> đóng tiến trình đó
        if p and p.poll() is None:
            p.terminate()
        store.x("DELETE FROM accounts WHERE id=?", (aid,))
        if acc:     # ghi lại để biết đã xoá gì (thư mục data/accounts/<id> + hồ sơ ShardBrowser vẫn giữ)
            plat = "TikTok" if acc["platform"] == "tiktok" else "Facebook"
            store.log(f"Đã xoá tài khoản {plat} #{aid} '{acc['name']}' khỏi tool "
                      f"(dữ liệu đăng nhập vẫn giữ ở data/accounts/{aid})")
        return RedirectResponse("/accounts", 303)

    # ---------------- Cài đặt Page ----------------
    @app.get("/pages/{pid}", response_class=HTMLResponse)
    def page_view(request: Request, pid: int):
        page = store.one("SELECT p.*, a.name acc_name FROM pages p JOIN accounts a ON a.id=p.account_id "
                         "WHERE p.id=?", (pid,))
        if not page:        # kênh đã bị xoá / tab cũ -> về danh sách, không lỗi 500
            return RedirectResponse("/accounts", 303)
        sources = store.q("SELECT * FROM sources WHERE page_id=? ORDER BY id", (pid,))
        mirror = store.q("SELECT c.*, p.name dst_name FROM crossposts c JOIN pages p ON p.id=c.dst_page_id "
                         "WHERE c.src_page_id=? ORDER BY c.updated_at DESC LIMIT 10", (pid,))
        return render(request, "page.html", page=page, s=store.page_settings(page), sources=sources,
                      stats=store.counts(pid), json=json, tiktoks=store.tiktok_channels(), mirror=mirror)

    @app.post("/crossposts/{src}/{dst}/{vid}/retry")
    def crosspost_retry(src: int, dst: int, vid: str):
        cp = store.one("SELECT * FROM crossposts WHERE video_id=? AND src_page_id=? AND dst_page_id=?", (vid, src, dst))
        if not cp or cp["status"] != "failed":
            return HTMLResponse('<span class="pill">Không còn ở trạng thái lỗi — tải lại trang</span>')
        # giờ hẹn đã qua -> đăng ngay khi tới lượt (giữ giờ cũ thì worker lại coi là quá giờ)
        when = cp["scheduled_at"] if cp["scheduled_at"] and cp["scheduled_at"] > time.time() + 25 * 60 else None
        store.set_crosspost(vid, src, dst, status="pending", error=None, scheduled_at=when)
        return HTMLResponse('<span class="pill info">Chờ đăng lại</span>')

    @app.post("/pages/{pid}/settings")
    async def page_save(request: Request, pid: int):
        from ..store import parse_number
        form = await request.form()
        page = store.one("SELECT * FROM pages WHERE id=?", (pid,))
        if not page:
            return RedirectResponse("/accounts", 303)
        s = store.page_settings(page)
        bad = []
        for k, default in DEFAULT_PAGE_SETTINGS.items():
            if isinstance(default, bool):
                s[k] = form.get(k) == "on"
            elif k in form:
                v = form.get(k)
                if isinstance(default, (int, float)):
                    if str(v).strip() == "":
                        continue                      # ô số để trống -> giữ giá trị cũ
                    try:
                        s[k] = parse_number(v, default)
                    except ValueError:
                        bad.append(k)
                else:
                    s[k] = v
        if bad:
            # không lưu gì cả, báo đúng ô sai (trước đây: lỗi 500, người dùng không thấy gì và mất thay đổi)
            from urllib.parse import quote
            return RedirectResponse(f"/pages/{pid}?bad={quote(','.join(bad))}", 303)
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
        if not row:
            return RedirectResponse("/accounts", 303)
        params = ({"max_age_days": max_age_days} if kind == "channel"
                  else {"max_results": max_results, "publish_time": publish_time, "sort_type": sort_type})
        store.x("UPDATE sources SET kind=?, value=?, params=?, enabled=? WHERE id=?",
                (kind, value.strip(), json.dumps(params), 1 if enabled == "on" else 0, sid))
        store.log(f"Đã sửa nguồn '{value.strip()}'", page_id=row["page_id"])
        return RedirectResponse(f"/pages/{row['page_id']}?saved=1", 303)

    @app.post("/sources/{sid}/toggle")
    def toggle_source(sid: int):
        row = store.one("SELECT page_id FROM sources WHERE id=?", (sid,))
        if not row:
            return RedirectResponse("/accounts", 303)
        store.x("UPDATE sources SET enabled=1-enabled WHERE id=?", (sid,))
        return RedirectResponse(f"/pages/{row['page_id']}", 303)

    @app.post("/sources/{sid}/delete")
    def del_source(sid: int):
        row = store.one("SELECT page_id FROM sources WHERE id=?", (sid,))
        if not row:         # bấm Xoá 2 lần / tab cũ
            return RedirectResponse("/accounts", 303)
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
        from ..store import vn_time
        return vn_time(t).strftime("%Y-%m-%dT%H:%M")     # ô chọn giờ hiểu là giờ VN

    @app.post("/videos/{pid}/{vid}/schedule")
    def schedule(pid: int, vid: str, when: str = Form(""), caption: str = Form(None)):
        # when mặc định "": ô giờ để trống -> báo lỗi rõ ràng thay vì 422 (htmx bỏ qua, người dùng không thấy gì)
        import datetime as dt
        from ..store import vn_fmt, vn_ts
        pg = store.one("SELECT platform FROM pages WHERE id=?", (pid,))
        where = "TikTok" if pg and pg["platform"] == "tiktok" else "Facebook"

        def err(msg: str) -> HTMLResponse:
            # lỗi hiện ngay dưới ô chọn giờ, giữ nguyên các nút Duyệt / Đăng ngay
            return HTMLResponse(msg, headers={"HX-Retarget": f"#v-{pid}-{vid} .sched-msg", "HX-Reswap": "innerHTML"})
        try:
            ts = vn_ts(dt.datetime.strptime(when, "%Y-%m-%dT%H:%M"))     # giờ người dùng chọn = giờ VN
        except ValueError:
            return err("Chưa chọn đủ ngày và giờ — chọn lại trong ô bên trên rồi bấm Lên lịch.")
        if ts < time.time() + 20 * 60:
            return err(f"{where} chỉ nhận lịch sau ít nhất 20 phút nữa — chọn từ "
                       f"{vn_fmt(time.time() + 21 * 60, '%H:%M')} trở đi.")
        fields = {"caption": caption} if caption is not None else {}
        if not store.set_video_if(vid, pid, ("review", "approved", "schedule_pending"), status="schedule_pending",
                                  scheduled_at=ts, error=None, **fields):
            return err("Video không còn ở mục chờ duyệt (đã đăng / đang đăng / đã bỏ) — tải lại trang.")
        store.log(f"Lên lịch {vid} lúc {when.replace('T', ' ')}", page_id=pid)
        note = " (TikTok chỉ nhận lịch trong 10 ngày — tool sẽ gửi khi tới gần ngày)" \
            if where == "TikTok" and ts > time.time() + 10 * 86400 - 3600 else ""
        # đổi giờ / lên lịch lại: lượt đăng kèm TikTok đang chờ theo giờ cũ -> bỏ, worker xếp lại theo giờ mới khi
        # lịch Facebook được gửi
        store.x("DELETE FROM crossposts WHERE video_id=? AND src_page_id=? AND status='pending'", (vid, pid))
        return HTMLResponse(f'<div class="done">Sẽ gửi lịch lên {where}: {vn_fmt(ts)}{note}</div>')

    @app.post("/videos/{pid}/{vid}/{action}")
    def video_action(pid: int, vid: str, action: str, caption: str = Form(None)):
        # mỗi thao tác chỉ hợp lệ từ vài trạng thái; đổi có điều kiện (nguyên tử) để trang mở lâu / bấm muộn không
        # ghi đè việc worker vừa làm (vd đã gửi lịch lên FB rồi mới bấm Huỷ lịch -> bài vẫn nằm trên FB)
        rules = {
            "approve": ("approved", ("review",)),
            "publish_now": ("publish_now", ("review", "approved")),
            "reject": ("rejected", ("new", "downloaded", "review", "approved", "publish_now", "schedule_pending",
                                    "failed")),
            "retry": ("downloaded", ("failed",)),
            "unschedule": ("review", ("schedule_pending",)),
            "save": (None, ("new", "downloaded", "review", "approved", "publish_now", "schedule_pending", "failed")),
        }
        if action not in rules:
            return HTMLResponse('<div class="done" style="color:var(--err)">Thao tác không hợp lệ</div>', status_code=400)
        new, allowed = rules[action]
        if action == "retry" and not (data / "raw" / f"{vid}.mp4").exists():
            new = "new"         # lỗi ở bước tải (chưa có file gốc) -> tải lại, không đưa thẳng sang edit
        fields = {"caption": caption} if caption is not None else {}
        if new:
            fields.update(status=new, error=None)
            if action == "unschedule":
                fields["scheduled_at"] = None
        ok = store.set_video_if(vid, pid, allowed, **fields) if fields else True
        if not ok:
            cur = store.one("SELECT status FROM videos WHERE id=? AND page_id=?", (vid, pid))
            now = STATUS_VI.get(cur["status"], cur["status"]) if cur else "không còn"
            hint = (" — lịch đã gửi lên nền tảng, hãy gỡ bài hẹn giờ ngay trên Facebook / TikTok"
                    if cur and cur["status"] == "scheduled" else " — tải lại trang để xem trạng thái mới")
            return HTMLResponse(f'<div class="done" style="color:var(--err)">Không làm được: video đang ở trạng thái '
                                f'"{now}"{hint}</div>')
        if action in ("unschedule", "reject"):
            # huỷ lịch / bỏ video -> lượt đăng kèm TikTok chưa đăng của nó cũng bỏ (trước: TikTok vẫn đăng theo giờ cũ)
            store.x("DELETE FROM crossposts WHERE video_id=? AND src_page_id=? AND status='pending'", (vid, pid))
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
