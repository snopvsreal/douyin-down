"""Giao diện web quản lý nhiều tài khoản Facebook / nhiều Page.  Chạy: uv run dytool ui  -> http://127.0.0.1:8000"""

import json
import subprocess
import sys
import threading
import re
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import config
from ..store import DEFAULT_PAGE_SETTINGS, Store
from ..worker import Worker

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

STATUS_VI = {
    "new": "Mới tìm thấy", "downloaded": "Đã tải", "review": "Chờ duyệt", "approved": "Chờ đăng",
    "publish_now": "Đăng ngay", "schedule_pending": "Chờ gửi lịch", "scheduled": "Đã lên lịch", "uploaded": "Đã đăng", "rejected": "Đã bỏ", "failed": "Lỗi", "skipped": "Bỏ qua (lọc)",
    "uploading": "Đang đăng",
}


def post_url(url: str | None) -> str:
    """Link bài thật trên nền tảng (reel / bài viết Facebook, video TikTok); link khác (trang soạn bài Business Suite
    ghi lúc đăng ngay ở bản cũ) -> '' để giao diện không hiện "xem bài" trỏ sai chỗ."""
    u = url or ""
    return u if re.match(r"https://(www\.)?(facebook\.com/(reel/|[^/]+/posts/)|tiktok\.com/@)", u) else ""


def status_vi(status: str, platform: str | None = None) -> str:
    """Nhãn trạng thái của 1 video — ghi đúng nền tảng kênh (TikTok / Facebook) cho trạng thái lịch.
    STATUS_VI để trung tính vì dùng cả cho bộ lọc chung nhiều kênh."""
    label = STATUS_VI.get(status, status)
    if platform and status in ("schedule_pending", "scheduled"):
        where = "TikTok" if platform == "tiktok" else "Facebook"
        label = {"schedule_pending": f"Chờ gửi lịch lên {where}", "scheduled": f"Đã lên lịch trên {where}"}[status]
    return label


# hiện ở Hộp duyệt: chờ duyệt + đã duyệt nhưng CHƯA gửi lên nền tảng (đổi giờ / giữ lại / bỏ được)
REVIEWABLE = "('review', 'approved', 'publish_now', 'schedule_pending')"
QUEUED = ("approved", "publish_now", "schedule_pending")

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def urlparse_host(url: str) -> str:
    from urllib.parse import urlsplit
    return urlsplit(url).hostname or ""
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
    worker.start_sync()        # bài hẹn giờ qua giờ -> Đã đăng, rồi lấy link bài (chạy cả khi tắt Chạy tự động)
    from .. import avatars as _avatars
    _avatars.refresh_fb_background(store, data)        # ảnh Page Facebook chưa có (ảnh công khai, chạy nền)
    procs: dict[str, subprocess.Popen] = {}

    app = FastAPI(title="dytool")
    app.middleware("http")(_request_guard(lan))
    app.state.worker = worker         # app desktop (desktop.py) dùng để tạm dừng / bật từ icon khay
    app.state.show_window = None       # desktop.py gán hàm đưa cửa sổ lên trước (khi mở dytool.exe lần 2)
    if autostart:
        worker.start()          # tự bật chạy tự động khi mở giao diện
        from .. import updates
        updates.start_background_checks()   # hỏi GitHub có bản mới của dytool / VoiceStudio (6 giờ / lần)
    from .. import browsers
    # tải sẵn / cập nhật nhân trình duyệt ShardX (lần đầu ~200 MB). Ngoài khối autostart: app desktop tạo giao diện với
    # autostart=False (tự bật worker riêng)
    browsers.install_engine_background()
    # chỉ phục vụ video đã edit (xem trước ở Duyệt video). KHÔNG mount cả data/: trong đó có app.db,
    # cookie Facebook (accounts/<id>/fb_state.json), nhật ký đăng nhập
    (data / "edited").mkdir(parents=True, exist_ok=True)
    app.mount("/media/edited", StaticFiles(directory=str(data / "edited")), name="media")
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    tpl = Jinja2Templates(directory=str(HERE / "templates"))
    # css_v(): đổi theo lần sửa app.css (đọc lại mỗi trang) để trình duyệt không dùng bản CSS cũ trong cache
    from ..store import vn_fmt
    from .. import updates as _updates
    tpl.env.globals.update(STATUS_VI=STATUS_VI, status_vi=status_vi, post_url=post_url, time=time, vnt=vn_fmt,     # vnt(ts, fmt): hiện giờ theo giờ VN
                           css_v=lambda: int((HERE / "static" / "app.css").stat().st_mtime),
                           app_ver=lambda: _updates.installed("dytool"),   # cuối menu; đọc có nhớ 60 s
                           vs_cached_voices=lambda: __import__("dytool.vs_api", fromlist=["x"]).cached_profiles(),
                           edit_state=worker.edit_state,      # nút Edit ngay (_edit_btn.html)
                           avatar_url=lambda pid: _avatars.url(data, pid))   # ảnh kênh (_macros.chan_av)
    tpl.env.filters["ago"] = lambda ts: _ago(ts)

    def _setup_failed() -> str | None:
        """Lần cài / cập nhật thư viện gần nhất chưa xong (setup_app.ps1 ghi data/setup_failed.txt) -> nội dung lỗi."""
        try:
            return (data / "setup_failed.txt").read_text(encoding="utf-8-sig").strip() or "không rõ lỗi"
        except OSError:
            return None

    def render(request: Request, name: str, **ctx):
        # navc: số video theo trạng thái cho huy hiệu trên menu (Chờ duyệt / Lỗi)
        from . import plan
        from .. import progress
        # run_task: thẻ Chạy tự động vẽ sẵn nội dung ngay trong trang (trước: khung rỗng rồi mới hỏi /partials/runcard
        # -> mỗi lần chuyển tab chữ biến mất rồi hiện lại, nháy)
        return tpl.TemplateResponse(request, name, {"worker": worker, "navc": store.counts(),
                                                    "run_task": progress.snapshot(),
                                                    "nav_pages": plan.nav_pages(store),
                                                    "data_rev": f"{store.rev()}-{int(worker.running)}",
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

    health_cache: dict = {"at": 0.0, "v": None}

    def health() -> dict:
        """Backend tải + VoiceStudio có đang chạy. 2 kiểm tra chạy song song, chờ kết nối 0.3 s, nhớ 3 s
        (thanh trạng thái hỏi mỗi 5 s trên mọi trang — trước mất ~4 s / lần khi 2 dịch vụ đang tắt)."""
        # Có kết quả cũ thì trả ngay, hỏi lại ở luồng nền: lúc máy bận (đang đăng bài, ShardX tải video lên) mỗi lần hỏi
        # chờ tới 3 s -> các yêu cầu tự làm mới dồn lại, bấm gì cũng phải chờ = app như bị đơ.
        if health_cache.get("v") is not None:
            if time.time() - health_cache["at"] >= 3 and not health_cache.get("busy"):
                health_cache["busy"] = True

                def bg():
                    try:
                        _health_check()
                    finally:
                        health_cache["busy"] = False
                threading.Thread(target=bg, daemon=True, name="dytool-health").start()
            return health_cache["v"]
        return _health_check()

    def _health_check() -> dict:
        from concurrent.futures import ThreadPoolExecutor
        from ..services import LOCAL_TIMEOUT

        def ok(url):
            try:
                return httpx.get(url, timeout=LOCAL_TIMEOUT).status_code < 500
            except httpx.HTTPError:
                return False
        from .. import services
        urls = {"tdl": cfg["douyin"]["api"].rstrip("/") + "/docs", "voicestudio": services.vs_api(cfg) + "/health"}
        with ThreadPoolExecutor(2) as ex:
            v = dict(zip(urls, ex.map(ok, urls.values())))
        # 'ok' / 'starting' (máy chủ giọng nói đang bật) / 'off'; 'missing' = chưa có model (đang / chưa tải)
        v["vs"] = ("ok" if v["voicestudio"] else "starting" if services.vs_ours_running()
                   else "off" if services.vs_model_ready() else "missing")
        health_cache.update(at=time.time(), v=v)
        return v

    def _sub_cfg() -> dict:
        return cfg["edit"].setdefault("subtitles", {})

    # ---------------- Màn hình khởi động (app desktop) ----------------
    # Mở app: BẬT SẴN backend tải Douyin + máy chủ giọng nói (người dùng yêu cầu 2026-11-25: "khởi động backend trước
    # khi vào app") rồi mới vào màn hình chính — chạy song song, chờ tới khi dùng được. Nút "Vào luôn" không phải chờ.
    # KHÔNG tự bật chạy tự động. Máy chủ giọng nói vẫn tự tắt khi rảnh theo cài đặt ở trang Giọng nói (cả khi chạy
    # tự động đang tắt — _idle_watch).
    STARTUP_STEPS = [("ui", "Giao diện"), ("tdl", "Bộ tải video Douyin"), ("vs", "Giọng nói (lồng tiếng)"),
                     ("browser", "Trình duyệt (nhân ShardX)"), ("douyin", "Đăng nhập Douyin"), ("ai", "AI dịch / viết lời")]
    startup: dict = {"begun": False, "done": False, "at": 0.0,
                     "steps": {k: {"name": n, "state": "wait", "detail": ""} for k, n in STARTUP_STEPS}}

    def _st(key: str, state: str | None = None, detail: str | None = None) -> None:
        step = startup["steps"][key]
        if state:
            step["state"] = state
        if detail is not None:
            step["detail"] = detail

    def _step_tdl():
        from .. import services
        _st("tdl", "run", "đang bật (cổng 5555)…")
        if services.tdl_alive(cfg):
            _st("tdl", "ok", "đang chạy")
            return
        if services.ensure_tdl(cfg, wait=120):
            _st("tdl", "ok", "đã bật")
        else:
            _st("tdl", "err", "bật chưa được — xem data/tdl_server.log (tool tự thử lại khi cần tải)")

    def _step_vs():
        from .. import services
        api = services.vs_api(cfg)
        if urlparse_host(api) not in LOCAL_HOSTS:
            _st("vs", "ok", f"đặt ở máy khác ({api})")
            return
        if not services.vs_model_ready():
            from .. import voice_model
            voice_model.ensure_background()      # lấy từ VoiceStudio cũ (nếu có) hoặc tải 3,3 GB ở nền, không chờ
            _st("vs", "warn", "chưa có model — đang tải ở nền (3,3 GB), xem tiến độ ở trang Giọng nói")
            return
        _st("vs", "run", "đang bật máy chủ giọng nói…")
        r = services.start_voicestudio(cfg)
        if r == "crashed":
            _st("vs", "err", "bật lên rồi tắt ngay — xem Nhật ký ở trang Giọng nói")
            return
        deadline = time.time() + 180
        while time.time() < deadline:
            try:
                m = httpx.get(api + "/model/status", timeout=httpx.Timeout(3.0, connect=0.3)).json()
            except (httpx.HTTPError, ValueError):
                m = None
            if m and m.get("loaded"):
                _st("vs", "ok", f"sẵn sàng ({m.get('device') == 'cuda' and 'card đồ hoạ' or 'CPU'})")
                return
            if m and m.get("error"):
                _st("vs", "err", m["error"][:200])
                return
            _st("vs", "run", (m or {}).get("detail") or "đang khởi động…")
            if not services.vs_ours_running() and services.vs_state(cfg) == "off":
                _st("vs", "err", "máy chủ giọng nói đã tắt — xem Nhật ký ở trang Giọng nói")
                return
            time.sleep(1.5)
        _st("vs", "warn", "nạp model lâu hơn thường lệ — vẫn đang nạp ở nền")

    def _step_browser():
        from .. import browsers
        t = browsers.install_engine_background()
        if browsers.engine_installed():
            _st("browser", "ok", "")         # kiểm tra bản mới tiếp ở nền
            return
        _st("browser", "run", "tải nhân lần đầu (~440 MB)…")
        t.join(900)
        _st("browser", "ok" if browsers.engine_installed() else "err",
            "" if browsers.engine_installed() else "chưa tải được — xem Cài đặt › Trình duyệt")

    def _step_douyin():
        from ..cookies import cookie_status
        _st("douyin", "run", "")
        dy = cookie_status(cfg["douyin"].get("cookie", ""))
        if not dy["logged_in"]:
            _st("douyin", "warn", "hết hạn — đăng nhập lại" if dy.get("expired") else "chưa đăng nhập")
        elif dy["days_left"] is not None and dy["days_left"] < 7:
            _st("douyin", "warn", f"còn {dy['days_left']} ngày — nên gia hạn")
        else:
            _st("douyin", "ok", f"còn {dy['days_left']} ngày" if dy["days_left"] is not None else "")

    def _step_ai():
        from .. import ai
        _st("ai", "run", "")
        p = ai.active()
        if p and ai.ready():
            _st("ai", "ok", ai.PROVIDERS[p]["name"])
        else:
            _st("ai", "warn", "chưa cài / chưa đăng nhập — dùng dịch máy (Cài đặt › AI)")

    def _idle_watch():
        """Máy chủ giọng nói bật lúc mở app: rảnh lâu thì tắt (theo trang Giọng nói) cả khi chạy tự động đang tắt —
        vòng lặp worker chỉ làm việc này lúc chạy tự động bật."""
        from .. import services
        while True:
            time.sleep(60)
            w = app.state.worker
            if w is not None and w.running:
                continue
            try:
                if services.vs_idle_stop(cfg):
                    store.log("Rảnh lâu -> tắt máy chủ giọng nói (tự bật lại khi có video cần lồng tiếng)")
            except Exception as e:
                store.log(f"Tắt máy chủ giọng nói lỗi: {e}", "error")

    def begin_startup():
        if startup["begun"]:
            return
        startup.update(begun=True, at=time.time())
        _st("ui", "ok")

        def run():
            from concurrent.futures import ThreadPoolExecutor
            jobs = {"tdl": _step_tdl, "vs": _step_vs, "browser": _step_browser, "douyin": _step_douyin, "ai": _step_ai}

            def safe(key):
                try:
                    jobs[key]()
                except Exception as e:      # 1 dịch vụ lỗi không được chặn cả app
                    _st(key, "err", str(e)[:200])
            with ThreadPoolExecutor(len(jobs)) as pool:
                list(pool.map(safe, jobs))
            from .. import updates
            updates.start_background_checks()
            startup["done"] = True
        threading.Thread(target=run, daemon=True, name="dytool-startup").start()
        threading.Thread(target=_idle_watch, daemon=True, name="vs-idle-watch").start()

    app.state.begin_startup = begin_startup

    @app.get("/startup", response_class=HTMLResponse)
    def startup_page(request: Request):
        if not startup["begun"] or startup["done"]:
            return RedirectResponse("/", 303)
        return tpl.TemplateResponse(request, "startup.html", {"steps": startup["steps"]})

    @app.get("/startup/status")
    def startup_status():
        return JSONResponse({"done": startup["done"], "elapsed": int(time.time() - startup["at"]),
                             "steps": [{"key": k, **v} for k, v in startup["steps"].items()]})

    # ---------------- Trang VoiceStudio: điều khiển + toàn bộ tính năng VoiceStudio bằng giao diện dytool ----------------
    # (không nhúng giao diện của VoiceStudio — mọi trang gọi API backend qua vs_api.py)
    VS_TABS = ("speak", "batch", "dubfile", "stt", "voices", "history", "pron", "log")
    # Whisper nhận được (mã của Whisper); "" = tự nhận
    STT_LANGS = (("", "Tự nhận ngôn ngữ"), ("vi", "Tiếng Việt"), ("zh", "Tiếng Trung"), ("en", "Tiếng Anh"),
                 ("yue", "Tiếng Quảng Đông"), ("ja", "Tiếng Nhật"), ("ko", "Tiếng Hàn"), ("th", "Tiếng Thái"),
                 ("id", "Tiếng Indonesia"), ("ms", "Tiếng Mã Lai"), ("km", "Tiếng Khmer"), ("lo", "Tiếng Lào"),
                 ("hi", "Tiếng Hindi"), ("ar", "Tiếng Ả Rập"), ("ru", "Tiếng Nga"), ("fr", "Tiếng Pháp"),
                 ("de", "Tiếng Đức"), ("es", "Tiếng Tây Ban Nha"), ("pt", "Tiếng Bồ Đào Nha"), ("it", "Tiếng Ý"))

    def _voice_usage() -> dict[str, list[str]]:
        """tên giọng (chữ thường) -> kênh đang dùng nó để lồng tiếng."""
        from ..dub import page_voices
        use: dict[str, list[str]] = {}
        for pg in store.q("SELECT * FROM pages ORDER BY id"):
            s = store.page_settings(pg)
            if not s.get("dub_enabled"):
                continue
            for g, v in page_voices(s).items():
                use.setdefault(v["name"].lower(), []).append(f"{pg['name']} · {'nam' if g == 'male' else 'nữ'}")
        return use

    def _channel_voices() -> list[dict]:
        from ..dub import page_voices
        out = []
        for pg in store.q("SELECT * FROM pages ORDER BY id"):
            s = store.page_settings(pg)
            out.append({"id": pg["id"], "name": pg["name"], "dub": bool(s.get("dub_enabled")), "voices": page_voices(s)})
        return out

    @app.get("/voicestudio", response_class=HTMLResponse)
    def vs_page(request: Request, tab: str = "", v: str = "", star: int = 0, reuse: str = ""):
        from .. import services, vs_api
        tab = tab if tab in VS_TABS else ""
        ctx: dict = {"info": services.vs_info(cfg), "tab": tab,
                     "jobs": _vs_jobs, "va": vs_api, "err": "", "v": v}
        ctx["tasks"] = _vs_tasks
        if tab in ("dubfile", "batch", "stt"):
            from .. import progress
            ctx["prog"] = progress.snapshot()
            ctx["channels"] = _channel_voices()
            ctx["stt_langs"] = STT_LANGS
        if tab in ("speak", "voices", "history", "batch") and ctx["info"]["state"] == "ok":
            try:
                ctx["profiles"] = vs_api.profiles(cfg)
                if tab in ("speak", "history"):
                    ctx["history"] = vs_api.history(cfg)
                    if reuse:            # "Dùng lại văn bản" từ Lịch sử -> điền sẵn ô văn bản + giọng
                        ctx["reuse"] = next((h for h in ctx["history"] if h.get("id") == reuse), None)
                        rz = ctx["reuse"] or {}
                        if rz.get("instruct") and not rz.get("profile_id"):
                            try:            # giọng thiết kế: mô tả cũ -> điền lại các ô thiết kế
                                ctx["reuse_vd"] = vs_api.describe(cfg, rz["instruct"])
                            except vs_api.VSError:
                                pass
            except vs_api.VSError as e:
                ctx["err"] = str(e)
        if tab == "pron" and ctx["info"]["state"] == "ok":
            try:
                ctx["pron"] = vs_api.pron_list(cfg)
            except vs_api.VSError as e:
                ctx["err"] = str(e)
        ctx["star"] = bool(star)
        ctx.setdefault("profiles", [])
        ctx.setdefault("history", [])
        ctx["usage"] = _voice_usage()
        if not tab:
            ctx["idle_choices"] = services.IDLE_CHOICES
            ctx["channels"] = _channel_voices()
        return render(request, "voicestudio.html", **ctx)

    @app.get("/partials/vs-ready")
    def vs_ready_partial():
        """Trang VoiceStudio đang chờ backend: bật xong -> tải lại trang để hiện mục vừa mở."""
        from .. import services
        if services.vs_info(cfg).get("state") == "ok":
            return HTMLResponse("", headers={"HX-Refresh": "true"})
        return HTMLResponse("")

    @app.get("/partials/vs-status", response_class=HTMLResponse)
    def vs_status_partial(request: Request):
        from .. import services
        return tpl.TemplateResponse(request, "_vs_status.html", {"info": services.vs_info(cfg)})

    @app.get("/partials/vs-log", response_class=HTMLResponse)
    def vs_log_partial():
        from html import escape
        from .. import services
        text = services.vs_log_tail()
        return HTMLResponse(f"<pre class='vs-log'>{escape(text) or 'Chưa có nhật ký.'}</pre>")

    def _vs_done(msg: str = "") -> HTMLResponse:
        from .. import services
        services._vs_info["at"] = 0                 # lần hỏi kế lấy trạng thái mới ngay
        health_cache["at"] = 0
        if msg:
            store.log(msg)
        return HTMLResponse("", headers={"HX-Trigger": "services-changed, vs-changed"})

    def _editing() -> bool:
        from .. import progress
        t = progress.STATE.get("task")
        return bool(t and t.get("kind") == "edit")

    @app.post("/voicestudio/start")
    def vs_start():
        return start_voicestudio()

    @app.post("/voicestudio/stop")
    def vs_stop():
        from .. import services
        if _editing():
            return PlainTextResponse("Đang edit / lồng tiếng 1 video — đợi xong (hoặc bấm Dừng) rồi tắt giọng nói.", 400)
        if services.stop_voicestudio():
            return _vs_done("Đã tắt máy chủ giọng nói")
        if services.vs_state(cfg) != "off":
            return PlainTextResponse("Máy chủ giọng nói này không do dytool bật (đặt ở máy khác) — tắt trên máy đó.", 400)
        return _vs_done()

    @app.post("/voicestudio/restart")
    def vs_restart():
        from .. import services
        if _editing():
            return PlainTextResponse("Đang edit / lồng tiếng 1 video — đợi xong rồi khởi động lại.", 400)
        if not services.stop_voicestudio() and services.vs_state(cfg) != "off":
            return PlainTextResponse("Máy chủ giọng nói này không do dytool bật (đặt ở máy khác) — khởi động lại trên máy đó.", 400)
        services.start_voicestudio(cfg)
        return _vs_done("Đang khởi động lại máy chủ giọng nói")

    @app.post("/voicestudio/idle")
    def vs_idle(minutes: int = Form(...)):
        from .. import services
        try:
            services.vs_set_idle_minutes(minutes)
        except ValueError:
            return PlainTextResponse("Giá trị không hợp lệ", 400)
        services.vs_touch()                         # tính lại giờ rảnh từ lúc đổi
        return _vs_done()

    @app.post("/voicestudio/offload")
    def vs_offload(on: str = Form("")):
        from .. import services, vs_api
        try:
            vs_api.set_offload(cfg, on == "on")
        except vs_api.VSError as e:
            return PlainTextResponse(str(e), 400)
        services._vs_info["at"] = 0
        return HTMLResponse("")

    @app.post("/voicestudio/flush")
    def vs_flush():
        from .. import services, vs_api
        if _editing():
            return PlainTextResponse("Đang lồng tiếng 1 video — đợi xong rồi giải phóng bộ nhớ.", 400)
        try:
            vs_api.flush_memory(cfg)
        except vs_api.VSError as e:
            return PlainTextResponse(str(e), 400)
        return _vs_done("Đã giải phóng bộ nhớ card đồ hoạ (model giọng nói nạp lại khi cần đọc)")

    # ----- việc chạy nền (đọc thử / tạo giọng nói / nghe thử giọng thiết kế): ô kết quả tự hỏi lại -----
    _vs_jobs: dict = {}         # khoá -> {"state": "run"|"ok"|"err", "msg", "at", "label", "file"}
    JOB_RE = __import__("re").compile(r"^[a-z0-9-]{1,40}$")

    def _job_file(key: str) -> Path:
        return data / "work" / f"vs_job_{key}.wav"

    def _start_job(request: Request, key: str, label: str, fn) -> HTMLResponse:
        """fn() -> bytes âm thanh. Chạy ở luồng nền (đọc mất 10 giây – vài phút)."""
        from .. import services
        cur = _vs_jobs.get(key)
        if not (cur and cur["state"] == "run"):
            _vs_jobs[key] = {"state": "run", "msg": "", "at": time.time(), "label": label}

            def run():
                try:
                    res = fn()
                    audio, meta = res if isinstance(res, tuple) else (res, {})
                    out = _job_file(key)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(audio)
                    services.vs_touch()
                    _vs_jobs[key] = {"state": "ok", "msg": "", "at": time.time(), "label": label, "meta": meta}
                except Exception as e:      # lỗi VoiceStudio / mạng: hiện trong ô kết quả
                    _vs_jobs[key] = {"state": "err", "msg": str(e)[:400], "at": time.time(), "label": label}
            threading.Thread(target=run, daemon=True, name=f"vs-job-{key}").start()
        return vs_job_partial(request, key)

    @app.get("/partials/vs-job", response_class=HTMLResponse)
    def vs_job_partial(request: Request, key: str):
        if not JOB_RE.match(key):
            return PlainTextResponse("", 400)
        t = _vs_jobs.get(key)
        return tpl.TemplateResponse(request, "_vs_job.html", {"key": key, "t": t,
                                                              "fresh": bool(t and time.time() - t["at"] < 6)})

    @app.get("/voicestudio/job.wav")
    def vs_job_wav(key: str, dl: int = 0):
        from fastapi.responses import FileResponse
        if not JOB_RE.match(key) or not _job_file(key).exists():
            return PlainTextResponse("", 404)
        name = f"dytool-{key}-{time.strftime('%Y%m%d-%H%M%S')}.wav" if dl else None
        return FileResponse(_job_file(key), media_type="audio/wav", filename=name, headers={"Cache-Control": "no-store"})

    # ----- việc dài có nhiều file kết quả (đọc hàng loạt / lồng tiếng 1 file video / giọng -> chữ) -----
    # khoá -> {"state": run|ok|err|stopped, "label", "at", "msg", "done", "total", "detail", "files": {tên: nhãn},
    #          "text", "stop"}
    _vs_tasks: dict = {}
    TASK_KEYS = ("batch", "dubfile", "stt")
    TASK_FILE_RE = __import__("re").compile(r"^[A-Za-z0-9_.-]{1,60}$")

    def _task_dir(key: str) -> Path:
        return data / "work" / f"vs_task_{key}"

    def _start_task(request: Request, key: str, label: str, fn) -> HTMLResponse:
        """fn(t) tự ghi file vào _task_dir(key) và cập nhật t (done / total / files / text). Chạy ở luồng nền."""
        t = {"state": "run", "label": label, "at": time.time(), "msg": "", "done": 0, "total": 0, "detail": "",
             "files": {}, "text": "", "stop": False}
        _vs_tasks[key] = t

        def run():
            from .. import progress
            try:
                fn(t)
                t["state"] = "stopped" if t["stop"] else "ok"
            except progress.Cancelled:
                t["state"] = "stopped"
            except Exception as e:
                t.update(state="stopped" if t["stop"] else "err", msg=str(e)[:500])
            t["end"] = time.time()
        threading.Thread(target=run, daemon=True, name=f"vs-task-{key}").start()
        return vs_task_partial(request, key)

    def _task_busy(key: str) -> bool:
        return (_vs_tasks.get(key) or {}).get("state") == "run"

    def _fresh_task_dir(key: str) -> Path:
        import shutil
        d = _task_dir(key)
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def _save_upload(f, dest: Path, max_mb: int) -> int:
        """Chép file tải lên ra đĩa từng khúc (video có thể vài trăm MB). Quá max_mb -> ValueError."""
        n = 0
        with open(dest, "wb") as out:
            while True:
                chunk = await f.read(1 << 20)
                if not chunk:
                    break
                n += len(chunk)
                if n > max_mb * 1024 * 1024:
                    raise ValueError(f"File quá lớn (tối đa {max_mb} MB)")
                out.write(chunk)
        if not n:
            raise ValueError("File trống")
        return n

    @app.get("/partials/vs-task", response_class=HTMLResponse)
    def vs_task_partial(request: Request, key: str):
        from .. import progress
        if key not in TASK_KEYS:
            return PlainTextResponse("", 400)
        return tpl.TemplateResponse(request, "_vs_task.html", {"key": key, "t": _vs_tasks.get(key),
                                                               "prog": progress.snapshot()})

    @app.post("/voicestudio/task/{key}/stop", response_class=HTMLResponse)
    def vs_task_stop(request: Request, key: str):
        from .. import progress
        t = _vs_tasks.get(key)
        if key not in TASK_KEYS or not t or t["state"] != "run":
            return PlainTextResponse("Không có việc nào đang chạy", 400)
        t["stop"] = True
        if key == "dubfile":
            snap = progress.snapshot()
            if snap and snap.get("title") == "Lồng tiếng file":
                progress.cancel()
        return vs_task_partial(request, key)

    @app.get("/voicestudio/task/{key}/file/{name}")
    def vs_task_file(key: str, name: str, dl: int = 0):
        from fastapi.responses import FileResponse
        t = _vs_tasks.get(key)
        if key not in TASK_KEYS or not TASK_FILE_RE.match(name) or not t or name not in t["files"]:
            return PlainTextResponse("", 404)
        f = _task_dir(key) / name
        if not f.is_file():
            return PlainTextResponse("", 404)
        return FileResponse(f, filename=f"dytool-{key}-{name}" if dl else None, headers={"Cache-Control": "no-store"})

    @app.post("/voicestudio/batch", response_class=HTMLResponse)
    async def vs_batch(request: Request):
        """Đọc hàng loạt: mỗi dòng 1 file wav (001.wav…), kèm 1 file ghép tất cả + zip. Không ghi Lịch sử."""
        import random
        from .. import vs_api
        if (bad := _vs_ready()):
            return bad
        if _task_busy("batch"):
            return PlainTextResponse("Đang đọc hàng loạt — đợi xong hoặc bấm Dừng", 400)
        form = await request.form()
        g = lambda k, d="": str(form.get(k, d) or d)
        lines = [" ".join(x.split()) for x in g("text").splitlines()]
        lines = [x for x in lines if x]
        if not lines:
            return PlainTextResponse("Gõ văn bản — mỗi dòng 1 bản đọc", 400)
        if len(lines) > 300:
            return PlainTextResponse(f"Tối đa 300 dòng mỗi lần (đang có {len(lines)})", 400)
        if any(len(x) > 3000 for x in lines):
            return PlainTextResponse("Có dòng dài quá 3.000 ký tự — dùng Tạo giọng nói cho văn bản dài", 400)
        prof, instruct = g("voice"), ""
        if g("src", "saved") == "design":
            prof, instruct = "", vs_api.instruct_of(extra=g("vd_extra"), **_vd(form))
        elif not prof:
            return PlainTextResponse("Chọn 1 giọng đã lưu (hoặc chuyển sang Thiết kế)", 400)
        language = g("language", "vi") if g("language", "vi") in vs_api.LANG_NAMES else "vi"
        seed = g("seed").strip()
        try:
            speed = float(g("speed", "1"))
            gap = max(0.0, min(5.0, float(g("gap", "0.5") or 0)))
        except ValueError:
            speed, gap = 1.0, 0.5
        sd = int(seed) if seed.lstrip("-").isdigit() else None
        if instruct and sd is None:          # giọng thiết kế: cố định seed để mọi dòng cùng 1 giọng
            sd = random.randint(0, 2 ** 31 - 1)
        kw = dict(profile_id=prof, instruct=instruct, language=language, speed=speed, seed=sd,
                  effect=g("effect", "broadcast"), pronounce=g("pronounce") == "on", record=False)

        def fn(t):
            import io
            import zipfile
            import numpy as np
            import soundfile as sf
            from .. import services
            d = _fresh_task_dir("batch")
            t["total"] = len(lines)
            parts, sr = [], 24000
            for i, line in enumerate(lines, 1):
                if t["stop"]:
                    break
                t["detail"] = line[:100]
                wav, _meta = vs_api.generate(cfg, line, **kw)
                name = f"{i:03d}.wav"
                (d / name).write_bytes(wav)
                a, sr = sf.read(io.BytesIO(wav), dtype="float32")
                parts.append(a if a.ndim == 1 else a.mean(axis=1))
                t["done"] = i
                t["files"][name] = line[:120]
                services.vs_touch()
            if not parts:
                return
            seq = []
            for a in parts:
                seq += [a, np.zeros(int(sr * gap), dtype=np.float32)]
            sf.write(d / "tat-ca.wav", np.concatenate(seq[:-1]), sr, subtype="PCM_16")
            (d / "noi-dung.txt").write_text("\n".join(f"{i:03d}.wav\t{x}" for i, x in
                                                      enumerate(lines[:len(parts)], 1)), "utf-8")
            with zipfile.ZipFile(d / "tat-ca.zip", "w", zipfile.ZIP_DEFLATED) as z:
                for name in [*t["files"], "noi-dung.txt"]:
                    z.write(d / name, name)
            t["files"] = {"tat-ca.wav": "Ghép tất cả (.wav)", "tat-ca.zip": "Từng file (.zip)", **t["files"]}
        return _start_task(request, "batch", f"{len(lines)} dòng", fn)

    @app.post("/voicestudio/dubfile", response_class=HTMLResponse)
    async def vs_dubfile(request: Request):
        """Edit + lồng tiếng 1 file video bất kỳ theo Cài đặt kênh đã chọn (như `dytool edit-file`). Không đăng, không
        thêm vào hàng video của kênh."""
        if _task_busy("dubfile"):
            return PlainTextResponse("Đang lồng tiếng 1 file — đợi xong hoặc bấm Dừng", 400)
        form = await request.form()
        f = form.get("file")
        try:
            pid = int(str(form.get("pid", "")))
        except ValueError:
            return PlainTextResponse("Chọn kênh để lấy cài đặt edit / lồng tiếng", 400)
        if not store.one("SELECT id FROM pages WHERE id=?", (pid,)):
            return PlainTextResponse("Không có kênh này", 404)
        if not getattr(f, "filename", None):
            return PlainTextResponse("Chọn file video (.mp4, .mov…)", 400)
        d = _fresh_task_dir("dubfile")
        ext = Path(f.filename).suffix.lower()
        src = d / ("goc" + (ext if ext in (".mp4", ".mov", ".mkv", ".webm", ".avi", ".flv", ".m4v") else ".mp4"))
        try:
            await _save_upload(f, src, 2048)
        except ValueError as e:
            return PlainTextResponse(str(e), 400)
        label = Path(f.filename).name[:80]

        def fn(t):
            t["total"] = 1
            out = d / "long-tieng.mp4"
            try:
                worker.edit_file(pid, src, out, label=label, should_stop=lambda: t["stop"])
            except Exception:
                out.unlink(missing_ok=True)
                raise
            finally:
                src.unlink(missing_ok=True)
            t["done"] = 1
            t["files"] = {"long-tieng.mp4": "Video đã edit"}
        return _start_task(request, "dubfile", label, fn)

    @app.post("/voicestudio/stt", response_class=HTMLResponse)
    async def vs_stt(request: Request):
        """Giọng nói -> chữ (faster-whisper ngay trong dytool, không cần máy chủ giọng nói): .txt + phụ đề .srt."""
        if _task_busy("stt"):
            return PlainTextResponse("Đang chuyển 1 file — đợi xong hoặc bấm Dừng", 400)
        form = await request.form()
        f = form.get("file")
        if not getattr(f, "filename", None):
            return PlainTextResponse("Chọn file âm thanh / video", 400)
        lang = str(form.get("language", "") or "")
        lang = lang if lang in dict(STT_LANGS) else ""
        model = str(form.get("model", "small"))
        model = model if model in ("small", "medium") else "small"
        d = _fresh_task_dir("stt")
        src = d / ("goc" + (Path(f.filename).suffix.lower()[:6] or ".bin"))
        try:
            await _save_upload(f, src, 2048)
        except ValueError as e:
            return PlainTextResponse(str(e), 400)

        def fn(t):
            from .. import subtitles

            def srt_ts(x: float) -> str:
                ms = int(round(x * 1000))
                h, ms = divmod(ms, 3600000)
                m, ms = divmod(ms, 60000)
                sec, ms = divmod(ms, 1000)
                return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"
            t["detail"] = "Đang đọc file…"
            try:
                pcm = subtitles.load_pcm(src)
            except Exception:
                raise RuntimeError("Không đọc được âm thanh trong file này — thử file khác (.mp3, .wav, .mp4)")
            finally:
                src.unlink(missing_ok=True)
            total = len(pcm) / 16000
            if total < 0.5:
                raise RuntimeError("File không có tiếng (hoặc quá ngắn)")
            t["total"] = max(1, int(total))
            t["detail"] = f"Đang nạp model nhận giọng ({model}; lần đầu phải tải về)…"
            segs_it, info = subtitles._whisper(model).transcribe(pcm, language=lang or None, vad_filter=True)
            t["lang"] = info.language
            segs = []
            for sg in segs_it:
                if t["stop"]:
                    break
                if sg.text.strip():
                    segs.append((sg.start, sg.end, sg.text.strip()))
                t["done"] = min(t["total"], int(sg.end))
                t["detail"] = sg.text.strip()[:100]
            text = "\n".join(x[2] for x in segs)
            (d / "loi.txt").write_text(text, "utf-8")
            (d / "phu-de.srt").write_text("\n".join(f"{i}\n{srt_ts(a)} --> {srt_ts(b)}\n{x}\n"
                                                    for i, (a, b, x) in enumerate(segs, 1)), "utf-8")
            t["text"] = text
            t["done"] = t["total"]
            t["files"] = {"loi.txt": "Lời (.txt)", "phu-de.srt": "Phụ đề (.srt)"}
        return _start_task(request, "stt", Path(f.filename).name[:80], fn)

    def _vs_ready() -> PlainTextResponse | None:
        from .. import services
        if services.vs_state(cfg) != "ok":
            return PlainTextResponse("Giọng nói chưa sẵn sàng — bấm Bật ở trang Giọng nói rồi đợi chấm xanh.", 400)
        return None

    @app.post("/voicestudio/test", response_class=HTMLResponse)
    def vs_test(request: Request, pid: int = Form(...), gender: str = Form(...)):
        """Đọc thử 1 câu bằng giọng nam / nữ của 1 kênh (đúng cách worker lồng tiếng, giọng chưa có thì tạo)."""
        from ..dub import VoiceStudio, page_voices
        if gender not in ("male", "female"):
            return PlainTextResponse("Giọng không hợp lệ", 400)
        if (bad := _vs_ready()):
            return bad
        pg = store.one("SELECT * FROM pages WHERE id=?", (pid,))
        if not pg:
            return PlainTextResponse("Không có kênh này", 404)
        spec = page_voices(store.page_settings(pg))[gender]

        def fn():
            import tempfile
            vs = VoiceStudio(cfg["edit"].get("dub", {}))
            out = _job_file(f"ch{pid}-{gender}").with_suffix(".tmp.wav")
            with tempfile.TemporaryDirectory() as tmp:
                name = vs.ensure_voice(Path(tmp), spec)
                vs.speak("Xin chào, đây là giọng lồng tiếng dytool đang dùng cho video của kênh này.", out,
                         duration=5.0, voice=name)
            b = out.read_bytes()
            out.unlink(missing_ok=True)
            return b
        return _start_job(request, f"ch{pid}-{gender}", "Đọc thử", fn)

    # ----- Tạo giọng nói -----
    def _vd(form) -> dict:
        """Các ô thiết kế giọng của form -> tham số instruct_of."""
        return {k: str(form.get(f"vd_{k}", "") or "") for k in ("gender", "age", "pitch", "style", "accent", "dialect")}

    @app.post("/voicestudio/speak", response_class=HTMLResponse)
    async def vs_speak(request: Request):
        """Tạo giọng nói (như trang Generate của VoiceStudio): giọng đã lưu / file ghi âm dùng 1 lần / thiết kế theo
        mô tả, ngôn ngữ, tốc độ, hiệu ứng, seed, độ dài, tham số giọng nâng cao. Chạy nền, ô kết quả tự hỏi lại."""
        from .. import vs_api
        if (bad := _vs_ready()):
            return bad
        form = await request.form()
        g = lambda k, d="": str(form.get(k, d) or d)
        text = g("text")
        if not text.strip():
            return PlainTextResponse("Gõ văn bản cần đọc", 400)
        language = g("language", "vi") if g("language", "vi") in vs_api.LANG_NAMES else "vi"
        src = g("src", "saved")
        seed = g("seed").strip()
        sd = int(seed) if seed.lstrip("-").isdigit() else None
        prof, instruct, ref, ref_name = "", "", None, ""
        if src == "saved":
            prof = g("voice")
            if not prof:
                return PlainTextResponse("Chọn 1 giọng đã lưu (hoặc chuyển sang File ghi âm / Thiết kế)", 400)
        elif src == "file":
            f = form.get("ref_file")
            if not getattr(f, "filename", None):
                return PlainTextResponse("Chọn file ghi âm giọng mẫu (5–15 giây, 1 người nói)", 400)
            ref, ref_name = await f.read(), f.filename
            instruct = g("style_extra").strip()[:120]
        else:
            instruct = vs_api.instruct_of(extra=g("vd_extra"), **_vd(form))
        adv = {k: g(f"adv_{k}") for k, *_ in vs_api.ADVANCED}
        try:
            speed = float(g("speed", "1"))
        except ValueError:
            speed = 1.0
        kw = dict(profile_id=prof, instruct=instruct, language=language, speed=speed, seed=sd,
                  effect=g("effect", "broadcast"), duration=g("duration"), ref_audio=ref, ref_name=ref_name,
                  ref_text=g("ref_text"), advanced=adv, denoise=g("denoise") == "on",
                  postprocess=g("postprocess") == "on", pronounce=g("pronounce") == "on")
        return _start_job(request, "speak", "Bản đọc", lambda: vs_api.generate(cfg, text, **kw))

    @app.post("/voicestudio/describe", response_class=HTMLResponse)
    def vs_describe(request: Request, describe: str = Form(""), prefix: str = Form("sp")):
        """Mô tả tự do -> điền sẵn các ô thiết kế giọng (như ô "Describe your voice" của VoiceStudio)."""
        from .. import vs_api
        if prefix not in ("sp", "ds", "bt"):
            prefix = "sp"
        try:
            d = vs_api.describe(cfg, describe)
        except vs_api.VSError as e:
            return PlainTextResponse(str(e), 400)
        return tpl.TemplateResponse(request, "_vs_design.html", {"va": vs_api, "vd": d, "prefix": prefix,
                                                                 "describe": describe})

    @app.post("/voicestudio/preset", response_class=HTMLResponse)
    def vs_preset(request: Request, i: int = Form(...), prefix: str = Form("sp")):
        from .. import vs_api
        if not 0 <= i < len(vs_api.DESIGN_PRESETS) or prefix not in ("sp", "ds", "bt"):
            return PlainTextResponse("Mẫu không hợp lệ", 400)
        name, attrs, tags = vs_api.DESIGN_PRESETS[i]
        return tpl.TemplateResponse(request, "_vs_design.html", {"va": vs_api, "vd": attrs, "prefix": prefix,
                                                                 "describe": "", "tags": tags})

    @app.post("/voicestudio/history/{hid}/save-voice")
    def vs_take_save(request: Request, hid: str, name: str = Form(""), save_name: str = Form("")):
        from .. import vs_api
        name = name or save_name
        try:
            vs_api.save_take(cfg, hid, name)
        except vs_api.VSError as e:
            return PlainTextResponse(str(e), 400)
        store.log(f"Giọng nói: đã lưu bản đọc thành giọng '{name.strip()}'")
        if request.headers.get("HX-Request") == "true":
            return HTMLResponse("", headers={"HX-Redirect": "/voicestudio?tab=voices"})
        return RedirectResponse("/voicestudio?tab=voices", 303)

    # ----- Từ điển phát âm -----
    @app.post("/voicestudio/pron/add")
    def vs_pron_add(term: str = Form(""), replacement: str = Form(""), language: str = Form("*")):
        from .. import vs_api
        if language != "*" and language not in vs_api.LANG_NAMES:
            language = "*"
        try:
            vs_api.pron_add(cfg, term, replacement, language)
        except vs_api.VSError as e:
            return PlainTextResponse(str(e), 400)
        return RedirectResponse("/voicestudio?tab=pron", 303)

    @app.post("/voicestudio/pron/{eid}/toggle")
    def vs_pron_toggle(eid: str, on: str = Form("")):
        from .. import vs_api
        try:
            vs_api.pron_toggle(cfg, eid, on == "1")
        except vs_api.VSError as e:
            return PlainTextResponse(str(e), 400)
        return RedirectResponse("/voicestudio?tab=pron", 303)

    @app.post("/voicestudio/pron/{eid}/delete")
    def vs_pron_delete(eid: str):
        from .. import vs_api
        try:
            vs_api.pron_delete(cfg, eid)
        except vs_api.VSError as e:
            return PlainTextResponse(str(e), 400)
        return RedirectResponse("/voicestudio?tab=pron", 303)

    @app.post("/voicestudio/pron/test", response_class=HTMLResponse)
    def vs_pron_test(text: str = Form(""), language: str = Form("vi")):
        from html import escape
        from .. import vs_api
        try:
            out = vs_api.pron_test(cfg, text, language)
        except vs_api.VSError as e:
            return PlainTextResponse(str(e), 400)
        return HTMLResponse(f"<p class='small' style='margin:8px 0 0'>Sẽ đọc thành: <b>{escape(out)}</b></p>")

    # ----- Giọng đã lưu: nhân bản / thiết kế / đổi tên / xoá -----
    def _vs_back(msg: str = "", tab: str = "voices"):
        if msg:
            store.log(msg)
        return RedirectResponse(f"/voicestudio?tab={tab}", 303)

    @app.post("/voicestudio/voices/clone")
    async def vs_clone(request: Request):
        from starlette.concurrency import run_in_threadpool
        from .. import vs_api
        if (bad := _vs_ready()):
            return bad
        form = await request.form()
        f = form.get("file")
        if not getattr(f, "filename", None):
            return PlainTextResponse("Chọn file ghi âm giọng mẫu", 400)
        if form.get("consent") != "on":
            return PlainTextResponse("Xác nhận bạn có quyền dùng giọng này (giọng của bạn hoặc người đã đồng ý)", 400)
        audio = await f.read()
        lang = str(form.get("language") or "vi")
        try:
            await run_in_threadpool(vs_api.clone, cfg, str(form.get("name", "")), audio, f.filename,
                                    str(form.get("ref_text", "")), lang if lang in vs_api.LANG_NAMES else "vi")
        except vs_api.VSError as e:
            return PlainTextResponse(str(e), 400)
        return _vs_back(f"Giọng nói: đã nhân bản giọng '{form.get('name')}'")

    @app.post("/voicestudio/voices/design-preview", response_class=HTMLResponse)
    async def vs_design_preview(request: Request):
        from .. import vs_api
        if (bad := _vs_ready()):
            return bad
        form = await request.form()
        instruct = vs_api.instruct_of(extra=str(form.get("vd_extra", "")), **_vd(form))
        seed = vs_api._num(form.get("seed"), -2**31, 2**31, as_int=True) or 7
        return _start_job(request, "design", f"Nghe thử · seed {seed}",
                          lambda: vs_api.design_preview(cfg, instruct, seed))

    @app.post("/voicestudio/voices/design-save")
    async def vs_design_save(request: Request):
        from starlette.concurrency import run_in_threadpool
        from .. import vs_api
        if (bad := _vs_ready()):
            return bad
        form = await request.form()
        name = str(form.get("name", ""))
        seed = vs_api._num(form.get("seed"), -2**31, 2**31, as_int=True) or 7
        try:
            await run_in_threadpool(vs_api.design_save, cfg, name,
                                    vs_api.instruct_of(extra=str(form.get("vd_extra", "")), **_vd(form)), seed)
        except vs_api.VSError as e:
            return PlainTextResponse(str(e), 400)
        return _vs_back(f"Giọng nói: đã lưu giọng thiết kế '{name.strip()}'")

    @app.post("/voicestudio/voices/{vid}/rename")
    def vs_voice_rename(vid: str, name: str = Form("")):
        from .. import vs_api
        prof = next((p for p in _safe_profiles() if p.get("id") == vid), None)
        if prof and _voice_usage().get((prof.get("name") or "").lower()):
            return PlainTextResponse("Giọng này đang dùng cho kênh — đổi tên sẽ làm kênh mất giọng. Chọn giọng khác cho "
                                     "kênh trước.", 400)
        try:
            vs_api.rename_profile(cfg, vid, name)
        except vs_api.VSError as e:
            return PlainTextResponse(str(e), 400)
        return _vs_back()

    @app.post("/voicestudio/voices/{vid}/delete")
    def vs_voice_delete(vid: str):
        from .. import vs_api
        prof = next((p for p in _safe_profiles() if p.get("id") == vid), None)
        if not prof:
            return PlainTextResponse("Không còn giọng này", 404)
        used = _voice_usage().get((prof.get("name") or "").lower())
        if used:
            return PlainTextResponse(f"Giọng đang dùng cho {', '.join(used)} — chọn giọng khác cho kênh trước rồi xoá", 400)
        try:
            vs_api.delete_profile(cfg, vid)
        except vs_api.VSError as e:
            return PlainTextResponse(str(e), 400)
        return _vs_back(f"Giọng nói: đã xoá giọng '{prof.get('name')}'")

    def _safe_profiles() -> list[dict]:
        from .. import vs_api
        try:
            return vs_api.profiles(cfg)
        except vs_api.VSError:
            return []

    @app.get("/voicestudio/voices/{vid}/audio")
    def vs_voice_audio(vid: str):
        from fastapi.responses import Response
        from .. import vs_api
        try:
            return Response(vs_api.profile_audio(cfg, vid), media_type="audio/wav",
                            headers={"Cache-Control": "private, max-age=300"})
        except vs_api.VSError as e:
            return PlainTextResponse(str(e), 404)

    # ----- Lịch sử -----
    @app.get("/voicestudio/history/{hid}/audio")
    def vs_history_audio(hid: str, dl: int = 0):
        from fastapi.responses import Response
        from .. import vs_api
        try:
            body, mime = vs_api.history_audio(cfg, hid)
        except vs_api.VSError as e:
            return PlainTextResponse(str(e), 404)
        ext = "wav" if mime == "audio/wav" else "ogg"
        h = {"Cache-Control": "private, max-age=3600"}
        if dl:
            h["Content-Disposition"] = f'attachment; filename="voicestudio-{hid}.{ext}"'
        return Response(body, media_type=mime, headers=h)

    @app.post("/voicestudio/history/{hid}/star")
    def vs_history_star(hid: str, on: str = Form("")):
        from .. import vs_api
        try:
            vs_api.star(cfg, hid, on == "1")
        except vs_api.VSError as e:
            return PlainTextResponse(str(e), 400)
        return RedirectResponse("/voicestudio?tab=history", 303)

    @app.post("/voicestudio/history/{hid}/delete")
    def vs_history_delete(hid: str):
        from .. import vs_api
        try:
            vs_api.delete_history(cfg, hid)
        except vs_api.VSError as e:
            return PlainTextResponse(str(e), 400)
        return RedirectResponse("/voicestudio?tab=history", 303)

    @app.post("/voicestudio/history/clear")
    def vs_history_clear():
        from .. import vs_api
        try:
            vs_api.clear_history(cfg)
        except vs_api.VSError as e:
            return PlainTextResponse(str(e), 400)
        return _vs_back("Giọng nói: đã xoá lịch sử tạo giọng nói", "history")

    # ---------------- App desktop ----------------
    @app.post("/app/show")
    def app_show():
        """dytool.exe mở lần 2 gọi vào đây để đưa cửa sổ đang chạy lên trước thay vì mở thêm cửa sổ."""
        if app.state.show_window:
            app.state.show_window()
            return HTMLResponse("ok")
        return HTMLResponse("no window", status_code=404)

    @app.post("/services/voicestudio")
    def start_voicestudio():
        """Bật máy chủ giọng nói chạy ngầm (không cửa sổ). Chưa có model -> bắt đầu tải ở nền."""
        from .. import services
        r = services.start_voicestudio(cfg)
        health_cache["at"] = 0
        if r == "starting":
            store.log("Đang bật máy chủ giọng nói — sẵn sàng sau khoảng 15–30 giây")
        elif r == "missing":
            store.log("Chưa có model giọng nói — đang tải ở nền (3,3 GB), xong thì bấm Bật lại")
        elif r == "remote":
            store.log(f"Máy chủ giọng nói đặt ở máy khác ({services.vs_api(cfg)}) — bật trên máy đó", "error")
        elif r == "crashed":
            store.log("Máy chủ giọng nói vừa bật đã tắt — xem Nhật ký ở trang Giọng nói (thử lại sau 10 phút)", "error")
        return HTMLResponse("", headers={"HX-Trigger": "services-changed"})

    @app.post("/services/voice-model")
    def download_voice_model():
        """Tải model giọng nói (OmniVoice, 3,3 GB) ở nền — dùng lại bản của app VoiceStudio nếu máy có."""
        from .. import voice_model
        voice_model.ensure_background()
        health_cache["at"] = 0
        store.log("Đang tải model giọng nói ở nền (3,3 GB)")
        return HTMLResponse("", headers={"HX-Trigger": "services-changed"})

    # ---------------- AI qua CLI (Claude / Codex / Gemini) — không dùng API key ----------------
    @app.get("/partials/ai", response_class=HTMLResponse)
    def ai_partial(request: Request, msg: str = "", msg_err: bool = False):
        from .. import ai
        return render(request, "_ai.html", items=ai.status(), msg=msg, msg_err=msg_err)

    @app.post("/ai/{provider}/{action}", response_class=HTMLResponse)
    def ai_action(request: Request, provider: str, action: str):
        from .. import ai
        if provider not in ai.PROVIDERS or action not in ("install", "login", "use", "test"):
            return PlainTextResponse("Thao tác không hợp lệ", 400)
        name = ai.PROVIDERS[provider]["name"]
        try:
            if action == "install":
                ai.install(provider)
                store.log(f"Đang cài {name}…")
                return ai_partial(request, msg=f"Đang cài {name} (vài phút, tải ~50–150 MB)…")
            if action == "login":
                ai.login(provider)
                return ai_partial(request, msg=ai.PROVIDERS[provider]["login_hint"])
            if action == "use":
                ai.choose(provider)
                store.log(f"Dùng {name} để dịch / lồng tiếng")
                return ai_partial(request, msg=f"Đã chọn {name}.")
            t = time.time()
            out = ai.ask("Trả lời đúng 1 chữ: OK", provider, timeout=180)
            return ai_partial(request, msg=f"{name} trả lời “{out[:60]}” sau {time.time() - t:.0f} giây — dùng được.")
        except ai.AIError as e:
            return ai_partial(request, msg=str(e), msg_err=True)

    # ---------------- Hôm nay (trang đầu) ----------------
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, msg: str = ""):
        import os
        from . import plan
        from .. import browsers
        from ..cookies import cookie_status
        totals = store.counts()
        from .. import ai
        need_key = not ai.ready()
        # "Cần bạn xử lý": gom mọi việc người dùng phải làm vào 1 chỗ
        todo = []
        if worker.captcha:      # Douyin đòi xác minh: đứng đầu — tìm kiếm của mọi kênh đang ngừng
            todo.append({"kind": "dy", "n": None, "captcha": True, "title": "Douyin cần bạn xác minh (captcha)",
                         "sub": f"Từ {vn_fmt(worker.captcha['since'], '%H:%M')} — tìm video của mọi kênh đang tạm ngừng. "
                                "Giải captcha trong cửa sổ douyin.com, xong thì dytool tự quét tiếp."})
        missing = store.q("SELECT v.id, v.page_id, v.error, p.platform FROM videos v JOIN pages p ON p.id=v.page_id "
                          "WHERE v.status='uploaded' AND v.error LIKE 'Không thấy bài trên %' "
                          "AND v.uploaded_at>? ORDER BY v.uploaded_at DESC", (time.time() - 14 * 86400,))
        if missing:
            m0 = missing[0]
            todo.append({"kind": "err", "n": len(missing), "title": "Bài không thấy trên nền tảng sau giờ đăng",
                         "sub": m0["error"].split(" — ")[0].split(": ", 1)[-1][:200], "btn": "Xem",
                         "href": f"/?v={m0['page_id']}:{m0['id']}"})
        # mục "Chờ duyệt" của trang này (thay trang Hộp duyệt cũ — không lặp lại thành 1 thẻ "Cần bạn xử lý"):
        # video chờ duyệt + nhóm riêng video đã duyệt nhưng chưa gửi lên nền tảng (đổi giờ / giữ lại / bỏ được)
        rq_all = review_list()
        rq = review_list(only_review=True)      # cùng danh sách với duyệt lần lượt ("2 / 5")
        rq_auto = [r for r in rq_all if r["status"] != "review"]
        if totals.get("failed"):
            todo.append({"kind": "err", "n": totals["failed"], "title": "Video lỗi", "sub": "Xem lý do rồi bấm Thử lại",
                         "href": "/queue?status=failed", "btn": "Xem video lỗi"})
        relog = [a for a in store.q("SELECT * FROM accounts ORDER BY id")
                 if a["status"] != "da_dang_nhap" or browsers.had_old_profile(store.account_dir(data, a["id"]))]
        if relog:
            todo.append({"kind": "acc", "n": len(relog), "title": "Tài khoản cần đăng nhập",
                         "sub": ", ".join(a["name"] for a in relog[:3]), "href": "/accounts", "btn": "Đăng nhập"})
        dy = cookie_status(cfg["douyin"].get("cookie", ""))
        if not dy["logged_in"] or (dy["days_left"] is not None and dy["days_left"] < 7):
            todo.append({"kind": "dy", "n": None, "title": "Đăng nhập Douyin" if not dy["logged_in"] else "Douyin sắp hết hạn",
                         "sub": "Cần để tìm và tải video" if not dy["logged_in"] else f"Còn {dy['days_left']} ngày",
                         "href": "/settings#douyin", "btn": "Đăng nhập Douyin"})
        dub_wait = store.one("SELECT COUNT(*) n FROM videos v JOIN pages p ON p.id=v.page_id WHERE v.status='downloaded' "
                             "AND p.enabled=1 AND p.settings LIKE '%\"dub_enabled\": true%'")["n"]
        vs = health().get("vs")
        if dub_wait and vs == "missing":
            from .. import voice_model
            dl = voice_model.state()
            busy = dl.get("phase") in ("downloading", "verifying")
            todo.append({"kind": "vs", "n": dub_wait, "title": "Chờ model giọng nói",
                         "sub": (f"Đang tải model lồng tiếng: {dl.get('done', 0) / 1e9:.1f} / {dl.get('total', 0) / 1e9:.1f} GB"
                                 if busy else f"Tải lỗi: {dl.get('error')}" if dl.get("phase") == "error"
                                 else "Video cần lồng tiếng — tải model giọng nói (3,3 GB, 1 lần)"),
                         **({} if busy else {"action": "/services/voice-model", "btn": "Tải model"})})
        elif dub_wait and vs == "off" and not (app.state.worker and app.state.worker.running):
            todo.append({"kind": "vs", "n": dub_wait, "title": "Chờ lồng tiếng", "sub": "Video cần lồng tiếng — máy chủ giọng nói đang tắt",
                         "action": "/services/voicestudio", "btn": "Bật giọng nói"})
        if need_key:
            todo.append({"kind": "key", "n": None, "title": "Chưa cài AI", "sub": "Cài và đăng nhập Claude / Codex / Gemini CLI để dịch, lồng tiếng",
                         "href": "/settings#ai", "btn": "Cài AI"})
        logs = store.q("SELECT * FROM logs ORDER BY id DESC LIMIT 6")
        return render(request, "home.html", totals=totals, todo=todo, today=plan.today_items(store),
                      chans=plan.channel_today(store, store.page_settings), logs=logs, rq=rq, rq_auto=rq_auto, msg=msg,
                      colors=plan.colors(store), today_label=vn_fmt(time.time(), "%d/%m"))

    @app.get("/partials/rev")
    def data_rev():
        """Phiên bản dữ liệu cho cập nhật trực tiếp (base.html hỏi 3 s / lần; đổi thì vẽ lại trang đang mở)."""
        return PlainTextResponse(f"{store.rev()}-{int(worker.running)}", headers={"Cache-Control": "no-store"})

    @app.get("/partials/runcard", response_class=HTMLResponse)
    def runcard(request: Request):
        from .. import progress
        return render(request, "_runcard.html", task=progress.snapshot())

    @app.post("/worker/cancel", response_class=HTMLResponse)
    def worker_cancel(request: Request, where: str = ""):
        """Nút Dừng: dừng việc đang làm + tạm dừng chạy tự động (không thì vòng sau nhặt lại đúng video đó)."""
        from .. import progress
        t = progress.snapshot()
        res = progress.cancel()
        if res == "late":
            return PlainTextResponse("Đang bấm đăng / lên lịch trên nền tảng — không dừng giữa chừng được (dừng thì "
                                     "không biết bài đã lên chưa). Chờ vài giây cho xong rồi tạm dừng chạy tự động.", 400)
        if res == "ok":
            worker.clear_manual()           # các video bấm "Edit ngay" còn chờ cũng thôi
            if worker.running:
                worker.stop()
            store.log(f"Bấm Dừng: {t['title']}{' · ' + t['page'] if t.get('page') else ''} — chạy tự động đã tạm dừng, "
                      "bật lại thì việc này làm lại từ đầu")
        if where == "progress":
            return progress_partial(request)
        return runcard(request)

    @app.post("/worker/toggle", response_class=HTMLResponse)
    def worker_toggle(request: Request):
        worker.stop() if worker.running else worker.start()
        store.log("Đã bật chạy tự động" if worker.running else "Đã tạm dừng chạy tự động")
        return runcard(request)

    # ---------------- Ctrl+K: tìm / lệnh nhanh ----------------
    @app.get("/partials/palette", response_class=HTMLResponse)
    def palette(request: Request, q: str = ""):
        from . import plan
        ql = q.strip().lower()
        cmds = [("Hôm nay", "/", "Trang"), ("Duyệt video", "/#cho-duyet", "Trang"), ("Lịch đăng", "/calendar", "Trang"),
                ("Video", "/queue", "Trang"), ("Video lỗi", "/queue?status=failed", "Trang"),
                ("Tài khoản", "/accounts", "Trang"), ("Trình duyệt · hồ sơ", "/browsers", "Trang"),
                ("Proxy", "/browsers/proxies", "Trang"), ("Cài đặt", "/settings", "Trang"),
                ("Đăng nhập Douyin", "/settings#douyin", "Lệnh"), ("Thêm tài khoản / kênh", "/accounts", "Lệnh"),
                ("Cài / đăng nhập AI (Claude, Codex, Gemini)", "/settings#ai", "Lệnh"), ("Cập nhật dytool", "/settings#cap-nhat", "Lệnh")]
        items = [{"label": l, "href": h, "kind": k} for l, h, k in cmds if not ql or ql in l.lower()]
        for p in plan.nav_pages(store):
            if not ql or ql in p["name"].lower():
                items.append({"label": p["name"], "href": f"/pages/{p['id']}", "kind": "Kênh", "color": p["color"]})
        if len(ql) >= 2:
            like = f"%{q.strip()}%"
            for v in store.q("SELECT v.id, v.page_id, v.desc, v.caption, v.status, p.name page_name, p.platform FROM videos v "
                             "JOIN pages p ON p.id=v.page_id WHERE v.id LIKE ? OR v.desc LIKE ? OR v.caption LIKE ? "
                             "ORDER BY v.updated_at DESC LIMIT 8", (like, like, like)):
                href = (f"/?v={v['page_id']}:{v['id']}" if v["status"] in ("review",) + QUEUED
                        else f"/queue?q={v['id']}")
                items.append({"label": (v["caption"] or v["desc"] or v["id"])[:80], "href": href,
                              "kind": status_vi(v["status"], v["platform"]), "sub": v["page_name"]})
        return render(request, "_palette.html", items=items[:20], q=q)

    @app.get("/partials/services", response_class=HTMLResponse)
    def services_partial(request: Request):
        from .. import progress, updates
        from ..cookies import cookie_status
        return render(request, "_services.html", health=health(), 
                      task=progress.snapshot(), n_updates=updates.pending(),
                      douyin=cookie_status(cfg["douyin"].get("cookie", "")), dy_logging_in=_alive("douyin"))

    # ---------------- Cập nhật (dytool / VoiceStudio) ----------------
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

    # ---- Douyin đòi xác minh (captcha): người dùng tự giải trong cửa sổ douyin.com (dytool không tự giải) ----
    @app.post("/douyin/captcha/open")
    def captcha_open():
        """Mở lại douyin.com (trang tìm kiếm — captcha hiện ở đó) trong hồ sơ trình duyệt Douyin."""
        err = worker.open_captcha_window()
        if err:
            return PlainTextResponse(err, 400)
        return HTMLResponse('<span class="small muted">Đã mở cửa sổ douyin.com — giải captcha ở đó</span>')

    @app.get("/partials/captcha", response_class=HTMLResponse)
    def captcha_partial(request: Request):
        """Câu hỏi captcha đang hiện trong cửa sổ douyin.com + bản dịch tiếng Việt (luồng nền captcha.py đọc 3 s / lần —
        ở đây chỉ lấy kết quả có sẵn, không chờ trình duyệt)."""
        from .. import captcha
        if not worker.captcha:
            return HTMLResponse("")
        captcha.ensure_watch(data, lambda: worker.captcha is not None)
        return render(request, "_captcha_q.html", snap=captcha.snapshot())

    @app.post("/douyin/captcha/check")
    def captcha_check():
        """Nút "Đã giải xong": tìm thử 1 lần ngay. Hết chặn -> quét tiếp; còn -> báo giải lại."""
        if worker.check_captcha(force=True):
            return HTMLResponse('<span class="small" style="color:var(--ok-text)">Đã hết đòi xác minh — dytool quét tiếp</span>')
        return PlainTextResponse("Douyin vẫn đòi xác minh — giải captcha trong cửa sổ douyin.com rồi bấm lại "
                                 "(nếu cửa sổ đã đóng, bấm Mở cửa sổ Douyin)", 400)

    @app.post("/services/douyin-login")
    def douyin_login(request: Request):
        """Mở hồ sơ trình duyệt dành cho Douyin để đăng nhập douyin.com, lưu cookie vào config."""
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
        relogin = {a["id"] for a in accs if browsers.had_old_profile(store.account_dir(data, a["id"]))}
        return render(request, "accounts.html", accounts=accs, pages=pages, mirrors=mirrors, shard=_shard_info(),
                      choices=choices, relogin=relogin,
                      logging_in={int(k[5:]) for k, p in procs.items() if k.startswith("login") and p.poll() is None})

    def _shard_info() -> dict:
        """Trình duyệt nhúng (SDK ShardX) cho trang Tài khoản (ô đổi hồ sơ) và trang Cài đặt (nhân, hồ sơ Douyin).
        Chỉ đọc file — không chạm mạng, trang mở tức thì."""
        from .. import browsers
        return {"profiles": browsers.list_profiles(), "douyin": browsers.get_choice(data),
                "douyin_old": browsers.had_old_profile(data), "engine": browsers.engine_installed(),
                "dir": str(browsers.SDK_DIR)}

    # ---------------- Cài đặt chung (cập nhật, AI, trình duyệt, Douyin, thông tin) ----------------
    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        import os
        from .. import updates
        from ..cookies import cookie_status
        # tăng tốc AI: đọc phiên bản torch (bản "+cu…" = có CUDA), không import torch (không khởi động CUDA / tốn VRAM)
        return render(request, "settings.html", shard=_shard_info(),
                      douyin=cookie_status(cfg["douyin"].get("cookie", "")), dy_logging_in=_alive("douyin"),
                      version=updates.installed("dytool"), root=str(ROOT), data_dir=str(data),
                      config_file=str(ROOT / config_path), accel=accel)

    # tăng tốc AI: phiên bản torch (bản "+cu…" = có CUDA; không import torch -> không khởi động CUDA / tốn VRAM) và
    # tên card NVIDIA. nvidia-smi mất ~1.2 s -> đọc 1 lần ở luồng nền lúc mở app, trang Cài đặt chỉ đọc kết quả
    accel = {"gpu": False, "torch": "", "card": ""}

    def _read_accel():
        try:
            from importlib.metadata import version as _v
            accel["torch"] = _v("torch")
            accel["gpu"] = "+cu" in accel["torch"]
            if accel["gpu"]:
                q = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True,
                                   text=True, timeout=10)
                accel["card"] = q.stdout.strip().splitlines()[0] if q.returncode == 0 and q.stdout.strip() else ""
        except Exception:
            pass
    import threading as _th
    _th.Thread(target=_read_accel, daemon=True).start()

    # ---------------- Trình duyệt nhúng (SDK ShardX) ----------------
    # ---------------- Hồ sơ trình duyệt (trang quản lý, giống app ShardBrowser) ----------------
    def _profile_usage() -> dict[str, list[str]]:
        """id hồ sơ -> tài khoản / Douyin đang dùng nó."""
        from .. import browsers
        use: dict[str, list[str]] = {}
        for a in store.q("SELECT * FROM accounts ORDER BY id"):
            c = browsers.get_choice(store.account_dir(data, a["id"]))
            if c:
                use.setdefault(c["profile_id"], []).append(
                    f"{'TikTok' if a['platform'] == 'tiktok' else 'Facebook'} · {a['name']}")
        c = browsers.get_choice(data)
        if c:
            use.setdefault(c["profile_id"], []).append("Đăng nhập Douyin")
        return use

    def _bctx() -> dict:
        """Dữ liệu chung các trang Hồ sơ trình duyệt (thanh tab + trạng thái nhân)."""
        from .. import browser_mgr, browsers
        try:
            engine = json.loads((browsers.SDK_DIR / "manifest.json").read_text("utf-8")).get("installed_chromium_version")
        except (OSError, ValueError):
            engine = None
        proxies = browser_mgr.list_proxies()
        return {"engine": engine, "engine_ready": browsers.engine_installed(), "sdk_dir": str(browsers.SDK_DIR),
                "proxies": proxies, "proxy_by_id": {x["id"]: x for x in proxies}, "mask": browser_mgr.mask,
                "n_trash": len(browser_mgr.list_trash()), "n_ext": len(browser_mgr.list_extensions())}

    def _bop(fn, back: str, ok_msg=None):
        """Chạy 1 thao tác hồ sơ: lỗi -> 400 kèm lời nhắn (giao diện hiện thông báo nổi); xong -> về trang `back`."""
        from .. import browsers
        try:
            res = fn()
        except (browsers.BrowserError, ValueError) as e:
            return PlainTextResponse(str(e), 400)
        msg = ok_msg(res) if callable(ok_msg) else ok_msg
        if msg:
            store.log(msg)
        return RedirectResponse(back(res) if callable(back) else back, 303)

    def _pname(pid: str) -> str:
        from .. import browsers
        return browsers.read_meta(pid).get("name") or pid

    @app.get("/browsers", response_class=HTMLResponse)
    def browsers_page(request: Request, tag: str = "", q: str = ""):
        from .. import browsers
        profiles = browsers.profile_details()
        tags = sorted({t for p in profiles for t in p["tags"]}, key=str.lower)
        usage = _profile_usage()
        needle = q.strip().lower()

        def hit(p) -> bool:
            if not needle:
                return True
            hay = " ".join([p["name"], p["notes"], " ".join(p["tags"]), " ".join(usage.get(p["id"], []))]).lower()
            return needle in hay
        shown = [p for p in profiles if (not tag or tag in p["tags"]) and hit(p)]
        shown.sort(key=lambda p: (not p["pinned"], not p["running"]))      # ghim lên đầu, rồi hồ sơ đang mở
        return render(request, "browsers.html", profiles=shown, all_count=len(profiles), tags=tags, tag=tag, q=q,
                      usage=usage, total_mb=round(sum(p["size_mb"] or 0 for p in profiles), 1),
                      check_sites=browsers.CHECK_SITES, platforms=browsers.PLATFORMS, **_bctx())

    @app.post("/browsers/{pid}/pin")
    def browsers_pin(pid: str, back: str = Form("/browsers")):
        from .. import browsers
        back = back if back.startswith("/browsers") else "/browsers"
        return _bop(lambda: browsers.write_meta(pid, pinned=None if browsers.read_meta(pid).get("pinned") else True),
                    back)

    @app.post("/browsers/{pid}/check")
    def browsers_check(pid: str, site: str = Form(...), back: str = Form("")):
        """Mở hồ sơ tới 1 trang kiểm tra dấu vân tay / IP (danh sách cố định browsers.CHECK_SITES)."""
        from .. import browsers
        if site not in browsers.CHECK_SITES:
            return PlainTextResponse("Trang kiểm tra không hợp lệ", 400)
        url = browsers.CHECK_SITES[site][1]
        return _bop(lambda: browsers.open_url(pid, url), f"/browsers/{pid}" if back == "detail" else "/browsers")

    @app.post("/browsers/bulk")
    async def browsers_bulk(request: Request):
        """Thao tác nhiều hồ sơ 1 lần (như ShardBrowser): mở / đóng / gắn proxy / thêm nhãn / bỏ nhãn / ghim / xoá."""
        from .. import browser_mgr, browsers
        form = await request.form()
        ids = [i for i in form.getlist("ids") if browsers._valid_id(i)]
        act = form.get("act", "")
        if not ids:
            return PlainTextResponse("Chưa chọn hồ sơ nào — tick ô ở góc từng hồ sơ", 400)
        usage = _profile_usage()
        done, errs = 0, []
        for pid in ids:
            name = _pname(pid)
            try:
                if act == "open":
                    browsers.open_window(pid)
                elif act == "close":
                    if pid in usage and not browsers.profile_details(pid)[0]["opened_here"]:
                        errs.append(f"{name}: đang được dytool dùng, không đóng")
                        continue
                    browsers.close_window(pid)
                elif act == "proxy":
                    m = browsers.read_meta(pid)
                    proxy_id = str(form.get("proxy_id", ""))
                    if proxy_id and not browser_mgr.get_proxy(proxy_id):
                        return PlainTextResponse("Proxy không tồn tại", 400)
                    browser_mgr.save_network(pid, proxy_id, m.get("webrtc") or "auto")
                elif act in ("tag", "untag"):
                    tag = " ".join(str(form.get("tag", "")).split())[:30]
                    if not tag:
                        return PlainTextResponse("Gõ tên nhãn", 400)
                    tags = [t for t in browsers.read_meta(pid).get("tags") or [] if t != tag]
                    browsers.write_meta(pid, tags=tags + [tag] if act == "tag" else tags)
                elif act in ("pin", "unpin"):
                    browsers.write_meta(pid, pinned=True if act == "pin" else None)
                elif act == "trash":
                    if usage.get(pid):
                        errs.append(f"{name}: đang dùng cho {', '.join(usage[pid])}")
                        continue
                    if browsers.is_running(pid):
                        errs.append(f"{name}: đang mở — đóng trước")
                        continue
                    browser_mgr.trash(pid)
                else:
                    return PlainTextResponse("Thao tác không hợp lệ", 400)
                done += 1
            except (browsers.BrowserError, ValueError, OSError) as e:
                errs.append(f"{name}: {e}")
        if act in ("open", "close", "trash", "proxy") and done:
            store.log({"open": "Đã mở", "close": "Đã đóng", "trash": "Đã bỏ vào thùng rác",
                       "proxy": "Đã đổi proxy cho"}[act] + f" {done} hồ sơ trình duyệt")
        if errs and not done:
            return PlainTextResponse("Không làm được: " + "; ".join(errs[:4]), 400)
        back = str(form.get("back", "/browsers"))
        if errs:          # làm được một phần: ghi nhật ký những hồ sơ bị bỏ qua
            store.log(f"Hồ sơ trình duyệt: xong {done}, bỏ qua {len(errs)} — " + "; ".join(errs[:5]))
        return RedirectResponse(back if back.startswith("/browsers") else "/browsers", 303)

    @app.post("/browsers/new")
    def browsers_new(name: str = Form(""), lang: str = Form("auto"), platform: str = Form("Windows"),
                     template: str = Form(""), refresh_rate: str = Form("")):
        from .. import browser_mgr, browsers
        name = " ".join(name.split())[:80] or "Hồ sơ mới"
        rr = int(refresh_rate) if refresh_rate.isdigit() and 24 <= int(refresh_rate) <= 480 else None
        if template:
            try:
                known = {t for ids in browser_mgr.templates().values() for t in ids}
            except Exception:
                known = set()
            if template not in known:
                return PlainTextResponse("Không có mẫu máy này", 400)
        return _bop(lambda: browsers.create_profile(name, english=lang == "en", platform=platform,
                                                    template=template, refresh_rate=rr),
                    lambda c: f"/browsers/{c['profile_id']}", f"Đã tạo hồ sơ trình duyệt '{name}'")

    # ----- proxy -----
    @app.get("/browsers/proxies", response_class=HTMLResponse)
    def browsers_proxies(request: Request):
        from .. import browser_mgr
        from .. import proxy_rotate
        return render(request, "browser_proxies.html", usage=browser_mgr.proxy_usage(), checking=_proxy_checking,
                      douyin_net=browser_mgr.douyin_net(), kinds=proxy_rotate.KINDS, providers=proxy_rotate.PROVIDERS,
                      isps=proxy_rotate.PROXYVN_ISP, redact=proxy_rotate.redact, **_bctx())

    _proxy_checking: dict = {"on": False}

    @app.post("/browsers/proxies/add")
    def proxies_add(text: str = Form(""), scheme: str = Form("socks5"), label: str = Form("")):
        from .. import browser_mgr
        added, bad = browser_mgr.add_proxies(text, scheme, label)
        if bad and not added:
            return PlainTextResponse("Không thêm được proxy nào: " + "; ".join(bad[:3]), 400)
        store.log(f"Đã thêm {added} proxy" + (f" ({len(bad)} dòng lỗi: {'; '.join(bad[:3])})" if bad else ""),
                  "error" if bad else "info")
        return RedirectResponse("/browsers/proxies", 303)

    @app.post("/browsers/proxies/add-rotating")
    def proxies_add_rotating(kind: str = Form(""), provider: str = Form("proxyvn"), key: str = Form(""),
                             new_url: str = Form(""), cur_url: str = Form(""), change_url: str = Form(""),
                             fixed: str = Form(""), scheme: str = Form("http"), isp: str = Form("random"),
                             region: str = Form("0"), label: str = Form("")):
        """Thêm proxy xoay (proxy_rotate.py). Kiểu API: lấy proxy ngay (key sai thì hiện lỗi ở dòng proxy)."""
        from .. import browser_mgr
        return _bop(lambda: browser_mgr.add_rotating(kind, provider, key, new_url, cur_url, change_url, fixed,
                                                     scheme, isp, region, label),
                    "/browsers/proxies", lambda p: f"Đã thêm proxy xoay '{p['label']}'")

    @app.post("/browsers/proxies/{proxy_id}/rotate")
    def proxies_rotate(proxy_id: str):
        """Nút Đổi IP: đổi rồi kiểm tra luôn (hiện IP mới). Proxy đang dùng cho Douyin mà Douyin đang đòi captcha ->
        tìm thử lại ngay."""
        from .. import browser_mgr, proxy_rotate
        try:
            p = browser_mgr.rotate(proxy_id)
        except (proxy_rotate.RotateError, ValueError) as e:
            return PlainTextResponse(f"Không đổi được IP: {e}", 400)
        except Exception as e:
            return PlainTextResponse(str(e), 400)
        if ((p.get("rot") or {}).get("kind")) == "link":
            time.sleep(4)                       # cổng cố định: IP mới cần vài giây mới lên
        res = browser_mgr.check_proxy(proxy_id)
        store.log(f"Proxy '{p.get('label')}': đã đổi IP" + (f" — IP mới {res.get('ip')}" if res.get("ok") else
                                                              f" — kiểm tra lỗi: {res.get('error')}"))
        if worker.captcha and browser_mgr.douyin_net().get("proxy_id") == proxy_id:
            threading.Thread(target=lambda: worker.check_captcha(force=True), daemon=True).start()
        return RedirectResponse("/browsers/proxies", 303)

    @app.post("/browsers/proxies/douyin")
    def proxies_douyin(proxy_id: str = Form(""), rotate_on_captcha: str = Form("")):
        from .. import browser_mgr
        return _bop(lambda: browser_mgr.save_douyin_net(proxy_id, bool(rotate_on_captcha)), "/browsers/proxies",
                    "Đã đổi proxy cho tìm / tải Douyin" if proxy_id else "Tìm / tải Douyin: không dùng proxy")

    @app.post("/browsers/proxies/check-all")
    def proxies_check_all():
        from .. import browser_mgr
        if not _proxy_checking["on"]:
            _proxy_checking["on"] = True

            def run():
                try:
                    for x in browser_mgr.list_proxies():
                        browser_mgr.check_proxy(x["id"])
                finally:
                    _proxy_checking["on"] = False
            import threading
            threading.Thread(target=run, daemon=True).start()
        return RedirectResponse("/browsers/proxies", 303)

    @app.post("/browsers/proxies/{proxy_id}/check")
    def proxies_check(proxy_id: str):
        from .. import browser_mgr
        return _bop(lambda: browser_mgr.check_proxy(proxy_id), "/browsers/proxies")

    @app.post("/browsers/proxies/{proxy_id}/label")
    def proxies_label(proxy_id: str, label: str = Form("")):
        from .. import browser_mgr
        return _bop(lambda: browser_mgr.update_proxy(proxy_id, label), "/browsers/proxies")

    @app.post("/browsers/proxies/{proxy_id}/delete")
    def proxies_delete(proxy_id: str):
        from .. import browser_mgr
        return _bop(lambda: browser_mgr.delete_proxy(proxy_id), "/browsers/proxies",
                    lambda names: "Đã xoá 1 proxy" + (f" — bỏ proxy khỏi hồ sơ: {', '.join(names)}" if names else ""))

    # ----- tiện ích -----
    @app.get("/browsers/extensions", response_class=HTMLResponse)
    def browsers_extensions(request: Request):
        from .. import browser_mgr, browsers
        use: dict[str, list[str]] = {}
        for p in browsers.list_profiles():
            for e in browsers.read_meta(p["id"]).get("extensions") or []:
                use.setdefault(e, []).append(p["name"])
        return render(request, "browser_extensions.html", extensions=browser_mgr.list_extensions(), ext_usage=use,
                      **_bctx())

    @app.post("/browsers/extensions/add")
    def extensions_add(src: str = Form("")):
        from .. import browser_mgr
        return _bop(lambda: browser_mgr.add_extension(src), "/browsers/extensions",
                    lambda x: f"Đã thêm tiện ích {x['name']} {x['version']}")

    @app.post("/browsers/extensions/{ext_id}/delete")
    def extensions_delete(ext_id: str):
        from .. import browser_mgr
        return _bop(lambda: browser_mgr.remove_extension(ext_id), "/browsers/extensions", "Đã gỡ 1 tiện ích")

    # ----- thùng rác -----
    @app.get("/browsers/trash", response_class=HTMLResponse)
    def browsers_trash(request: Request):
        from .. import browser_mgr
        return render(request, "browser_trash.html", trash=browser_mgr.list_trash(), days=browser_mgr.TRASH_DAYS,
                      **_bctx())

    @app.post("/browsers/trash/empty")
    def trash_empty():
        from .. import browser_mgr

        def run():
            for x in browser_mgr.list_trash():
                browser_mgr.purge(x["id"])
        return _bop(run, "/browsers/trash", "Đã dọn sạch thùng rác hồ sơ trình duyệt")

    @app.post("/browsers/trash/{pid}/restore")
    def trash_restore(pid: str):
        from .. import browser_mgr
        return _bop(lambda: browser_mgr.restore(pid), f"/browsers/{pid}", lambda _: f"Đã khôi phục hồ sơ '{_pname(pid)}'")

    @app.post("/browsers/trash/{pid}/purge")
    def trash_purge(pid: str):
        from .. import browser_mgr
        return _bop(lambda: browser_mgr.purge(pid), "/browsers/trash", "Đã xoá hẳn 1 hồ sơ trình duyệt")

    # ----- 1 hồ sơ -----
    @app.get("/browsers/{pid}", response_class=HTMLResponse)
    def browser_detail(request: Request, pid: str):
        from .. import browser_mgr, browsers
        try:
            browsers.profile_dir(pid)
        except browsers.BrowserError:
            return RedirectResponse("/browsers", 303)
        p = browsers.profile_details(only=pid)[0]
        try:
            tpls = browser_mgr.templates()
        except Exception:
            tpls = {}
        return render(request, "browser_detail.html", prof=p,   # không đặt tên `p`: base.html dùng biến p cho menu
                      used=_profile_usage().get(pid, []), templates=tpls,
                      noise_on=browser_mgr.noise_on(pid), noise_vectors=browser_mgr.NOISE_VECTORS,
                      webrtc_modes=browser_mgr.WEBRTC, extensions=browser_mgr.list_extensions(),
                      platforms=browsers.PLATFORMS, screen_modes=browsers.SCREEN_MODES, **_bctx())

    @app.post("/browsers/{pid}/open")
    def browsers_open(pid: str, back: str = Form("")):
        from .. import browsers
        return _bop(lambda: browsers.open_window(pid), f"/browsers/{pid}" if back == "detail" else "/browsers")

    @app.post("/browsers/{pid}/close")
    def browsers_close(pid: str, back: str = Form("")):
        from .. import browsers
        return _bop(lambda: browsers.close_window(pid), f"/browsers/{pid}" if back == "detail" else "/browsers")

    @app.post("/browsers/{pid}/general")
    def browsers_general(pid: str, name: str = Form(""), notes: str = Form(""), tags: str = Form(""),
                         lang: str = Form("auto")):
        from .. import browser_mgr, browsers

        def run():
            browser_mgr.save_general(pid, name, notes, tags)
            browsers.set_language(pid, lang == "en")
            new = " ".join(name.split())[:80]
            # tên hiện ở trang Tài khoản / Cài đặt đọc từ browser.json -> cập nhật theo
            for target in [store.account_dir(data, a["id"]) for a in store.q("SELECT id FROM accounts")] + [data]:
                c = browsers.get_choice(target)
                if c and c["profile_id"] == pid:
                    browsers.set_choice(target, pid, new)
        return _bop(run, f"/browsers/{pid}?saved=general#chung")

    @app.post("/browsers/{pid}/network")
    def browsers_network(pid: str, proxy_id: str = Form(""), webrtc: str = Form("auto")):
        from .. import browser_mgr
        return _bop(lambda: browser_mgr.save_network(pid, proxy_id, webrtc), f"/browsers/{pid}?saved=network#mang",
                    f"Hồ sơ '{_pname(pid)}': đổi proxy / WebRTC (áp dụng lần mở sau)")

    @app.post("/browsers/{pid}/fingerprint")
    def browsers_fingerprint(pid: str, template: str = Form(""), platform: str = Form("Windows")):
        from .. import browser_mgr
        return _bop(lambda: browser_mgr.regenerate(pid, template, platform), f"/browsers/{pid}?saved=fp#van-tay",
                    lambda t: f"Hồ sơ '{_pname(pid)}': dấu vân tay mới ({t})")

    @app.post("/browsers/{pid}/noise")
    async def browsers_noise(pid: str, request: Request):
        from .. import browser_mgr
        form = await request.form()
        vecs = [str(v) for v in form.getlist("noise")]
        return _bop(lambda: browser_mgr.set_noise(pid, vecs), f"/browsers/{pid}?saved=noise#van-tay")

    @app.post("/browsers/{pid}/extensions")
    async def browsers_set_ext(pid: str, request: Request):
        from .. import browser_mgr
        form = await request.form()
        ids = [str(v) for v in form.getlist("ext")]
        return _bop(lambda: browser_mgr.save_extensions(pid, ids), f"/browsers/{pid}?saved=ext#tien-ich")

    @app.post("/browsers/{pid}/bookmarks")
    def browsers_bookmarks(pid: str, text: str = Form("")):
        from .. import browser_mgr
        return _bop(lambda: browser_mgr.save_bookmarks(pid, text), f"/browsers/{pid}?saved=bm#bookmark")

    @app.post("/browsers/{pid}/launch")
    def browsers_launch_opts(pid: str, screen_mode: str = Form(""), quic: str = Form("auto"),
                             hide_name: str = Form(""), android_media: str = Form(""), webrtc_ip: str = Form(""),
                             refresh_rate: str = Form("")):
        from .. import browser_mgr
        return _bop(lambda: browser_mgr.save_launch(pid, screen_mode, quic, bool(hide_name), bool(android_media),
                                                    webrtc_ip, refresh_rate),
                    f"/browsers/{pid}?saved=launch#khoi-dong")

    @app.post("/browsers/{pid}/advanced")
    def browsers_advanced(pid: str, extra_args: str = Form("")):
        from .. import browser_mgr
        return _bop(lambda: browser_mgr.save_advanced(pid, extra_args), f"/browsers/{pid}?saved=adv#nang-cao")

    @app.post("/browsers/{pid}/cookies/export")
    def browsers_cookies_export(pid: str):
        from fastapi.responses import Response
        from .. import browser_mgr, browsers
        try:
            cookies = browser_mgr.export_cookies(pid)
        except browsers.BrowserError as e:
            return PlainTextResponse(str(e), 400)
        import unicodedata          # tên file trong header phải ASCII: bỏ dấu tiếng Việt
        plain = unicodedata.normalize("NFKD", _pname(pid).replace("đ", "d").replace("Đ", "D"))
        name = "".join(ch if ch.isascii() and ch.isalnum() else "-" for ch in plain if not unicodedata.combining(ch))[:40] or pid
        store.log(f"Hồ sơ '{_pname(pid)}': xuất {len(cookies)} cookie")
        return Response(json.dumps(cookies, ensure_ascii=False, indent=1), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="cookies-{name}.json"'})

    @app.post("/browsers/{pid}/cookies/import")
    async def browsers_cookies_import(pid: str, request: Request):
        from .. import browser_mgr
        form = await request.form()
        f = form.get("file")
        text = (await f.read()).decode("utf-8-sig", "replace") if hasattr(f, "read") else str(form.get("text") or "")
        if len(text) > 5_000_000:
            return PlainTextResponse("File cookie quá lớn", 400)
        from starlette.concurrency import run_in_threadpool      # Playwright bản đồng bộ không chạy trên vòng lặp async
        return await run_in_threadpool(_bop, lambda: browser_mgr.import_cookies(pid, text), f"/browsers/{pid}?saved=cookie#cookie",
                    lambda n: f"Hồ sơ '{_pname(pid)}': nhập {n} cookie")

    @app.post("/browsers/{pid}/clone")
    def browsers_clone(pid: str):
        from .. import browser_mgr
        return _bop(lambda: browser_mgr.clone(pid), lambda c: f"/browsers/{c['profile_id']}",
                    lambda c: f"Đã nhân bản hồ sơ '{_pname(pid)}' -> '{c['profile_name']}'")

    @app.post("/browsers/{pid}/delete")
    def browsers_delete(pid: str):
        from .. import browser_mgr
        used = _profile_usage().get(pid)
        if used:
            return PlainTextResponse(f"Hồ sơ đang dùng cho {', '.join(used)} — đổi sang hồ sơ khác trước rồi xoá", 400)
        name = _pname(pid)
        return _bop(lambda: browser_mgr.trash(pid), "/browsers",
                    f"Đã bỏ hồ sơ trình duyệt '{name}' vào thùng rác")

    def _auto_profile(target: Path, name: str, who: str, quiet: bool = False, english: bool = False,
                      account: bool = False) -> bool:
        """Chưa gắn hồ sơ -> tự tạo hồ sơ trình duyệt mới và gắn luôn (người dùng không phải liên kết tay).
        account=True (tài khoản FB / TikTok): hồ sơ mới chờ tự gắn proxy tĩnh lúc Đăng nhập lần đầu (_auto_proxy)."""
        from .. import browser_mgr, browsers
        try:
            c, new = browsers.ensure_profile(target, name, english=english)
        except browsers.BrowserError as e:
            if not quiet:
                store.log(f"{who}: không tạo được hồ sơ trình duyệt — {e}", "error")
            return False
        if new:
            if account:
                browser_mgr.mark_auto_proxy(c["profile_id"])
            store.log(f"{who}: đã tạo hồ sơ trình duyệt '{c['profile_name']}'")
        return True

    def _auto_proxy(target: Path, who: str) -> None:
        """Hồ sơ tài khoản mới, chưa có proxy -> gắn 1 proxy tĩnh trống (kiểm tra trước). Hết proxy -> báo nhắc."""
        from .. import browser_mgr, browsers
        c = browsers.get_choice(target)
        if not c:
            return
        try:
            _p, msg = browser_mgr.assign_free_proxy(c["profile_id"])
        except Exception as e:
            msg, _p = f"không tự gắn được proxy — {e}", None
        if msg:
            store.log(f"{who}: {msg}", "info" if _p else "warn")

    def _acc_profile_name(acc) -> str:
        plat = "TikTok" if acc["platform"] == "tiktok" else "Facebook"
        return f"dytool · {plat} · {acc['name']}"

    @app.post("/accounts/{aid}/browser/new")
    def new_account_profile(aid: int):
        """Tạo hồ sơ mới thay cho hồ sơ đang gắn (hồ sơ cũ vẫn giữ trong data/shardx/profiles, chọn lại được)."""
        from .. import browsers
        acc = store.one("SELECT * FROM accounts WHERE id=?", (aid,))
        if acc:
            acc_dir = store.account_dir(data, aid)
            old = browsers.get_choice(acc_dir)
            browsers.set_choice(acc_dir, "")
            if not _auto_profile(acc_dir, _acc_profile_name(acc), f"Tài khoản #{aid}", english=acc["platform"] == "tiktok", account=True) and old:
                browsers.set_choice(acc_dir, old["profile_id"], old.get("profile_name", ""))
        return RedirectResponse("/accounts", 303)

    def _set_profile(target: Path, choice: str, who: str):
        """choice = '<profile_id>|<tên hồ sơ>' hoặc '' (bỏ chọn)."""
        from .. import browsers
        pid, _, name = choice.partition("|")
        if pid and not any(p["id"] == pid for p in browsers.list_profiles()):
            store.log(f"{who}: không có hồ sơ trình duyệt này", "error")
            return
        browsers.set_choice(target, pid, name)
        store.log(f"{who}: dùng hồ sơ trình duyệt '{name}' — bấm Đăng nhập để đăng nhập trong hồ sơ đó" if pid
                  else f"{who}: đã bỏ chọn hồ sơ trình duyệt")

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
        # tạo sẵn hồ sơ trình duyệt (chỉ ghi file); không được thì để tới lúc bấm Đăng nhập
        _auto_profile(store.account_dir(data, aid), _acc_profile_name(acc), f"Tài khoản #{aid}", quiet=True,
                      english=platform == "tiktok", account=True)
        return RedirectResponse("/accounts" + ("#tiktok" if platform == "tiktok" else ""), 303)

    @app.post("/accounts/{aid}/login")
    def login(aid: int):
        """Mở hồ sơ trình duyệt của tài khoản để người dùng tự đăng nhập; xong thì tự quét Page / lấy tên kênh."""
        acc_dir = store.account_dir(data, aid)
        acc = store.one("SELECT * FROM accounts WHERE id=?", (aid,))
        if not acc:
            return RedirectResponse("/accounts", 303)
        if not _auto_profile(acc_dir, _acc_profile_name(acc), f"Tài khoản #{aid}", english=acc["platform"] == "tiktok",
                             account=True):
            return RedirectResponse("/settings#trinh-duyet", 303)
        _auto_proxy(acc_dir, f"Tài khoản #{aid}")       # lần đăng nhập đầu của hồ sơ mới: gắn proxy tĩnh trống
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
        from .. import avatars
        avatars.refresh_fb_background(store, data, only_missing=False, max_age_days=0)

    def _save_tiktok(aid: int, who: dict, login: bool = True):
        """Tài khoản TikTok có đúng 1 kênh đăng (dòng pages platform='tiktok'), mặc định tắt. Tên kênh = tên hiển thị
        trên TikTok (nickname), không phải tên người dùng tự gõ lúc thêm tài khoản; ảnh kênh -> data/avatars."""
        acc = store.one("SELECT * FROM accounts WHERE id=?", (aid,))
        user = (who.get("username") or "").lstrip("@")
        nick = (who.get("nickname") or "").strip()[:80]
        old = store.one("SELECT id, name FROM pages WHERE account_id=? AND platform='tiktok'", (aid,))
        if not user and not login:
            store.log(f"Tài khoản TikTok #{aid}: không đọc được tên kênh (phiên đăng nhập hết? bấm Đăng nhập lại)",
                      "error")
            return
        name = nick or (f"@{user}" if user else (old["name"] if old else acc["name"] if acc else f"TikTok #{aid}"))
        if old:     # đăng nhập lại: cập nhật tên, giữ cài đặt / nguồn của kênh
            store.x("UPDATE pages SET name=?, profile_id=?, asset_id=? WHERE id=?",
                    (name, user, user or f"tt{aid}", old["id"]))
            pid = old["id"]
        else:
            store.upsert_page(aid, name, user, user or f"tt{aid}", platform="tiktok")
            pid = store.one("SELECT id FROM pages WHERE account_id=? AND platform='tiktok'", (aid,))["id"]
        if user or nick:    # 1 tài khoản TikTok = 1 kênh: tài khoản mang luôn tên kênh
            store.x("UPDATE accounts SET name=? WHERE id=?", (name, aid))
        if login:
            store.x("UPDATE accounts SET status='da_dang_nhap', fb_user=? WHERE id=?", (user, aid))
        av = store.account_dir(data, aid) / "tt_avatar.img"
        from .. import avatars
        got_av = av.exists() and avatars.save(data, pid, av.read_bytes())
        av.unlink(missing_ok=True)
        store.log(f"Tài khoản TikTok #{aid}: {'đã đăng nhập' if login else 'cập nhật'} {name}"
                  + (f" (@{user})" if user else "") + (" + ảnh kênh" if got_av else ""), page_id=pid)

    @app.post("/accounts/{aid}/refresh-info")
    def account_refresh_info(aid: int):
        """Nút "Cập nhật tên & ảnh": TikTok -> mở trình duyệt của tài khoản đọc lại tên + ảnh kênh (vài giây);
        Facebook -> lấy lại ảnh các Page (ảnh công khai, không mở trình duyệt)."""
        acc = store.one("SELECT * FROM accounts WHERE id=?", (aid,))
        if not acc:
            return RedirectResponse("/accounts", 303)
        from .. import avatars
        if acc["platform"] != "tiktok":
            def fb():
                n = 0
                for p in store.q("SELECT id, profile_id, asset_id FROM pages WHERE account_id=?", (aid,)):
                    n += avatars.fetch_fb(data, p["id"], [p["asset_id"], p["profile_id"]])
                store.log(f"Tài khoản #{aid}: cập nhật ảnh {n} Page")
            _threading.Thread(target=fb, daemon=True).start()
            return RedirectResponse("/accounts", 303)
        key = f"info{aid}"
        if (procs.get(key) and procs[key].poll() is None) or (procs.get(f"login{aid}") and
                                                             procs[f"login{aid}"].poll() is None):
            return RedirectResponse("/accounts", 303)
        acc_dir = store.account_dir(data, aid)
        code = ("import sys,json; from pathlib import Path; from dytool import tt_browser as t; "
                "d=Path(sys.argv[1]); print('TT='+json.dumps(t.refresh_info(d)))")
        log = open(acc_dir / "info.log", "w")
        p = procs[key] = subprocess.Popen([sys.executable, "-c", code, str(acc_dir)], cwd=ROOT,
                                          stdout=log, stderr=subprocess.STDOUT)
        store.log(f"Tài khoản TikTok #{aid}: đang đọc lại tên + ảnh kênh…")

        def watch():
            p.wait()
            log.close()
            procs.pop(key, None)
            out = (acc_dir / "info.log").read_text(errors="ignore")
            line = next((l for l in out.splitlines() if l.startswith("TT=")), None)
            if line:
                _save_tiktok(aid, json.loads(line[3:]), login=False)
            else:
                last = (out.strip().splitlines() or [""])[-1][:200]
                store.log(f"Tài khoản TikTok #{aid}: không đọc được tên + ảnh kênh — {last}", "error")
        _threading.Thread(target=watch, daemon=True).start()
        return RedirectResponse("/accounts", 303)

    @app.get("/avatars/{pid}")
    def avatar(pid: int):
        from .. import avatars
        f = avatars.path(data, pid)
        if not f.is_file():
            return PlainTextResponse("", 404)
        return FileResponse(f, media_type="image/png", headers={"Cache-Control": "max-age=86400"})

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
        if acc:     # ghi lại để biết đã xoá gì (thư mục data/accounts/<id> + hồ sơ trình duyệt vẫn giữ)
            plat = "TikTok" if acc["platform"] == "tiktok" else "Facebook"
            store.log(f"Đã xoá tài khoản {plat} #{aid} '{acc['name']}' khỏi tool "
                      f"(dữ liệu đăng nhập vẫn giữ ở data/accounts/{aid})")
        return RedirectResponse("/accounts", 303)

    # ---------------- Kênh: tổng quan + cài đặt kênh (1 trang, nhiều tab) ----------------
    @app.get("/pages/{pid}", response_class=HTMLResponse)
    def page_view(request: Request, pid: int, st: str = "", q: str = "", pg: int = 1):
        """Bấm tên kênh ở menu: phần chính = video của kênh (lọc st / tìm q / trang pg) + cột phải nhịp đăng, hôm nay,
        nguồn, nhật ký. Cài đặt kênh ở cùng trang, mở bằng nút "Cài đặt kênh" (#nguon, #lich…)."""
        page = store.one("SELECT p.*, a.name acc_name FROM pages p JOIN accounts a ON a.id=p.account_id "
                         "WHERE p.id=?", (pid,))
        if not page:        # kênh đã bị xoá / tab cũ -> về danh sách, không lỗi 500
            return RedirectResponse("/accounts", 303)
        from . import plan
        s = store.page_settings(page)
        sources = store.q("SELECT * FROM sources WHERE page_id=? ORDER BY id", (pid,))
        mirror = store.q("SELECT c.*, p.name dst_name FROM crossposts c JOIN pages p ON p.id=c.dst_page_id "
                         "WHERE c.src_page_id=? ORDER BY c.updated_at DESC LIMIT 10", (pid,))
        others = store.q("SELECT id, name, platform FROM pages WHERE id<>? ORDER BY platform, id", (pid,))
        week_n = store.one("SELECT COUNT(*) n FROM videos WHERE page_id=? AND status IN ('schedule_pending','scheduled') "
                           "AND scheduled_at BETWEEN ? AND ?", (pid, int(time.time()), int(time.time()) + 7 * 86400))["n"]
        # đăng kèm: Page này đăng kèm sang kênh TikTok nào / kênh TikTok này nhận đăng kèm từ Page nào
        mirror_to = None
        if page["platform"] != "tiktok" and int(s.get("tiktok_mirror") or 0):
            mirror_to = store.one("SELECT id, name FROM pages WHERE id=?", (int(s["tiktok_mirror"]),))
        mirrored_by = [r for r in store.q("SELECT * FROM pages WHERE platform<>'tiktok'")
                       if int(store.page_settings(r).get("tiktok_mirror") or 0) == pid]
        o = plan.channel_overview(store, store.page_settings, pid)
        vids = plan.channel_videos(store, pid, st, q, pg)
        return render(request, "page.html", page=page, s=s, sources=sources, o=o, vids=vids,
                      stats=o["counts"], json=json, tiktoks=store.tiktok_channels(), mirror=mirror,
                      others=others, week_n=week_n, month_n=o["month_n"], mirror_to=mirror_to,
                      mirrored_by=mirrored_by, color=plan.colors(store).get(pid, plan.PALETTE[0]))

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
        auto = request.headers.get("HX-Request") == "true"      # tự lưu khi sửa (trang kênh mới)
        if bad:
            # không lưu gì cả, báo đúng ô sai (trước đây: lỗi 500, người dùng không thấy gì và mất thay đổi)
            if auto:
                return PlainTextResponse("Chưa lưu — ô sau không phải số: " + ", ".join(bad), 400)
            from urllib.parse import quote
            return RedirectResponse(f"/pages/{pid}?tab=lich&bad={quote(','.join(bad))}", 303)
        store.save_page_settings(pid, s)
        was = page["enabled"]
        store.x("UPDATE pages SET enabled=? WHERE id=?", (1 if form.get("enabled") == "on" else 0, pid))
        if auto:
            if (form.get("enabled") == "on") != bool(was):
                store.log(f"{'Bật' if form.get('enabled') == 'on' else 'Tắt'} kênh {page['name']}", page_id=pid)
            # kèm phần trang đổi theo cài đặt (hx-swap-oob): nhãn Đang chạy / tắt, ô tóm tắt, chấm kênh ở menu
            from . import plan
            fresh = store.one("SELECT * FROM pages WHERE id=?", (pid,))
            return render(request, "_ch_saved.html", page=fresh, s=store.page_settings(fresh),
                          ch=next((c for c in plan.nav_pages(store) if c["id"] == pid), None),
                          saved_at=vn_fmt(time.time(), "%H:%M:%S"))
        store.log(f"Đã lưu cài đặt Page #{pid}", page_id=pid)
        return RedirectResponse(f"/pages/{pid}?saved=1&tab=lich", 303)

    @app.post("/pages/{pid}/copy-settings")
    def copy_settings(pid: int, to: int = Form(...), with_sources: str = Form(None)):
        """Sao chép cài đặt kênh (lịch, edit, giọng, caption, lọc) sang kênh khác. Không chép đăng kèm TikTok / nhóm
        (riêng từng Page)."""
        src = store.one("SELECT * FROM pages WHERE id=?", (pid,))
        dst = store.one("SELECT * FROM pages WHERE id=?", (to,))
        if not src or not dst or src["id"] == dst["id"]:
            return PlainTextResponse("Chọn 1 kênh khác để chép sang", 400)
        s, d = store.page_settings(src), store.page_settings(dst)
        for k, v in s.items():
            if k not in ("tiktok_mirror", "share_groups", "group_text"):
                d[k] = v
        store.save_page_settings(to, d)
        n = 0
        if with_sources == "on":
            have = {(r["kind"], r["value"]) for r in store.q("SELECT kind, value FROM sources WHERE page_id=?", (to,))}
            for r in store.q("SELECT * FROM sources WHERE page_id=?", (pid,)):
                if (r["kind"], r["value"]) not in have:
                    store.x("INSERT INTO sources(page_id, kind, value, params, enabled) VALUES (?,?,?,?,?)",
                            (to, r["kind"], r["value"], r["params"], r["enabled"]))
                    n += 1
        store.log(f"Chép cài đặt {src['name']} -> {dst['name']}" + (f" (+{n} nguồn)" if n else ""), page_id=to)
        return RedirectResponse(f"/pages/{to}?copied={pid}&tab=lich", 303)

    def _keyword(kind: str, value: str, zh: str, params: dict):
        """Từ khoá tiếng Việt -> tìm Douyin bằng cụm tiếng Trung (keywords.translate, dùng lại kết quả lúc xem trước).
        zh = cụm người dùng chọn ở phần xem trước. Trả (từ khoá lưu vào nguồn, lỗi). Giữ câu gốc ở params['vi']."""
        from .. import keywords
        value = " ".join(value.split())
        params.pop("vi", None)
        if kind != "keyword" or not value or keywords.is_chinese(value):
            return value, None
        pick = " ".join((zh or "").split())
        if not keywords.is_chinese(pick):
            r = keywords.translate(value, _sub_cfg())
            if not r["zh"]:
                return None, f"Chưa dịch được '{value}' sang tiếng Trung ({r['error'][:120]}) — nhập thẳng từ khoá tiếng Trung"
            pick = r["zh"]
        params["vi"] = value[:120]
        return pick[:120], None

    MAX_SOURCE_LINES = 50

    def _source_lines(value: str) -> list[str]:
        """Ô thêm nguồn: mỗi dòng = 1 nguồn (bỏ dòng trống, dòng trùng; tối đa MAX_SOURCE_LINES)."""
        out = []
        for line in (value or "").splitlines():
            line = " ".join(line.split())
            if line and line not in out:
                out.append(line)
        return out[:MAX_SOURCE_LINES]

    def _zh_map(zh: list[str], zh_src: list[str]) -> dict:
        """Cụm tiếng Trung người dùng chọn ở phần xem trước, theo dòng (zh_src[i] -> zh[i])."""
        return {" ".join(s.split()): z for s, z in zip(zh_src, zh)}

    def _source_params(kind: str, max_results: int, sort_type: int) -> dict:
        """Quét mọi thời gian (không lọc ngày đăng): kênh = mọi video; từ khoá = N video, mặc định nhiều like (hot)."""
        if kind == "channel":
            return {}
        return {"max_results": max(5, min(int(max_results), 500)),
                "sort_type": sort_type if sort_type in (0, 1, 2) else 1}

    def _warm_translate(kind: str, lines: list[str], picked: dict) -> None:
        """Dịch song song các dòng chưa có bản xem trước (keywords.translate nhớ kết quả) — 20 dòng không phải chờ nối tiếp."""
        from .. import keywords
        todo = [x for x in lines if kind == "keyword" and not keywords.is_chinese(x)
                and not keywords.is_chinese(picked.get(x, ""))]
        if len(todo) > 1:
            from concurrent.futures import ThreadPoolExecutor
            cfg = _sub_cfg()
            with ThreadPoolExecutor(4) as ex:
                list(ex.map(lambda x: keywords.translate(x, cfg), todo))

    @app.get("/partials/kw-translate", response_class=HTMLResponse)
    def kw_translate(request: Request, value: str = "", kind: str = "keyword", uid: str = "new"):
        """Xem trước lúc gõ: từ khoá tiếng Việt sẽ được tìm trên Douyin bằng cụm tiếng Trung nào (mỗi dòng 1 hộp)."""
        from .. import keywords
        import re
        uid = uid if re.fullmatch(r"[\w-]{1,20}", uid) else "new"
        lines = [x for x in _source_lines(value) if not keywords.is_chinese(x)]
        if kind != "keyword" or not lines:
            return HTMLResponse("")
        _warm_translate(kind, lines, {})
        cfg = _sub_cfg()
        return render(request, "_kw_preview.html", items=[(x, keywords.translate(x, cfg)) for x in lines], uid=uid)

    @app.post("/pages/{pid}/sources")
    def add_source(pid: int, kind: str = Form(...), value: str = Form(...), max_results: int = Form(50),
                   sort_type: int = Form(1), zh: list[str] = Form([]), zh_src: list[str] = Form([])):
        """Mỗi dòng của ô nhập = 1 nguồn. Dòng trùng nguồn đã có thì bỏ qua; dòng dịch lỗi ghi nhật ký kênh."""
        if kind not in ("keyword", "channel"):
            return PlainTextResponse("Loại nguồn không hợp lệ", 400)
        lines = _source_lines(value)
        if not lines:
            return PlainTextResponse("Nhập ít nhất 1 từ khoá hoặc link kênh", 400)
        picked = _zh_map(zh, zh_src)
        _warm_translate(kind, lines, picked)
        have = {r["value"] for r in store.q("SELECT value FROM sources WHERE page_id=? AND kind=?", (pid, kind))}
        added, errors = [], []
        for line in lines:
            params = _source_params(kind, max_results, sort_type)
            val, err = _keyword(kind, line, picked.get(line, ""), params)
            if err:
                errors.append(err)
                continue
            if val in have:
                continue
            have.add(val)
            store.x("INSERT INTO sources(page_id, kind, value, params) VALUES (?,?,?,?)",
                    (pid, kind, val, json.dumps(params, ensure_ascii=False)))
            added.append(f"'{params['vi']}' → '{val}'" if params.get("vi") else f"'{val}'")
        if errors and not added:
            return PlainTextResponse("\n".join(errors[:5]), 400)
        if added:
            store.log(f"Thêm {len(added)} nguồn: " + ", ".join(added)[:900], page_id=pid)
        for e in errors:
            store.log(f"Không thêm được nguồn: {e}", "error", page_id=pid)
        return RedirectResponse(f"/pages/{pid}?tab=nguon", 303)

    @app.post("/sources/{sid}/update")
    def update_source(sid: int, kind: str = Form(...), value: str = Form(...), max_results: int = Form(50),
                      sort_type: int = Form(1), enabled: str = Form(None),
                      zh: list[str] = Form([]), zh_src: list[str] = Form([])):
        row = store.one("SELECT page_id FROM sources WHERE id=?", (sid,))
        if not row:
            return RedirectResponse("/accounts", 303)
        if kind not in ("keyword", "channel"):
            return PlainTextResponse("Loại nguồn không hợp lệ", 400)
        params = _source_params(kind, max_results, sort_type)
        value = " ".join(value.split())
        value, err = _keyword(kind, value, _zh_map(zh, zh_src).get(value, ""), params)
        if err:
            return PlainTextResponse(err, 400)
        store.x("UPDATE sources SET kind=?, value=?, params=?, enabled=? WHERE id=?",
                (kind, value, json.dumps(params, ensure_ascii=False), 1 if enabled == "on" else 0, sid))
        store.log(f"Đã sửa nguồn '{params.get('vi') or value}'", page_id=row["page_id"])
        return RedirectResponse(f"/pages/{row['page_id']}?saved=1&tab=nguon", 303)

    @app.post("/sources/{sid}/toggle")
    def toggle_source(sid: int):
        row = store.one("SELECT page_id FROM sources WHERE id=?", (sid,))
        if not row:
            return RedirectResponse("/accounts", 303)
        store.x("UPDATE sources SET enabled=1-enabled WHERE id=?", (sid,))
        return RedirectResponse(f"/pages/{row['page_id']}?tab=nguon", 303)

    @app.post("/sources/{sid}/delete")
    def del_source(sid: int):
        row = store.one("SELECT page_id FROM sources WHERE id=?", (sid,))
        if not row:         # bấm Xoá 2 lần / tab cũ
            return RedirectResponse("/accounts", 303)
        store.x("DELETE FROM sources WHERE id=?", (sid,))
        return RedirectResponse(f"/pages/{row['page_id']}?tab=nguon", 303)

    @app.post("/videos/{pid}/{vid}/dismiss-missing")
    def dismiss_missing(pid: int, vid: str):
        """Đã kiểm tra trên nền tảng: bỏ cảnh báo 'không thấy bài' (giữ trạng thái Đã đăng)."""
        store.set_video_if(vid, pid, expect=("uploaded",), error=None)
        return HTMLResponse('<span class="small muted">Đã bỏ cảnh báo</span>')

    @app.post("/pages/{pid}/fetch-links")
    def fetch_links_now(pid: int):
        """Nút "Lấy link bài": đọc ngay danh sách bài của kênh (luồng nền, chờ việc nặng đang chạy xong)."""
        def run():
            with worker._busy:
                try:
                    if not worker.fetch_links(pid):
                        store.log("Lấy link bài: chưa thấy bài nào khớp", page_id=pid)
                except Exception as e:
                    store.log(f"Lấy link bài lỗi: {e}", "error", pid)
        threading.Thread(target=run, daemon=True).start()
        return HTMLResponse('<span class="small muted">Đang lấy link bài… tự hiện khi xong</span>')

    @app.post("/pages/{pid}/scan")
    def scan_now(pid: int):
        """Nút "Quét nguồn ngay": quét cả khi chạy tự động đang tắt (luồng việc bấm tay) / kênh đang tắt."""
        page = store.one("SELECT name FROM pages WHERE id=?", (pid,))
        if not page:
            return PlainTextResponse("Không có kênh này", 404)
        try:
            r = worker.scan_now(pid)
        except RuntimeError as e:
            return PlainTextResponse(str(e), 400)
        store.log(f"[{page['name']}] Bấm quét nguồn ngay" + (" — chờ việc đang chạy xong" if r == "queued" else ""),
                  page_id=pid)
        return RedirectResponse(f"/pages/{pid}", 303)

    # ---------------- Duyệt (trên trang Hôm nay, trong hộp xem trước video) ----------------
    def review_list(page: int | None = None, only_review: bool = False) -> list[dict]:
        """Video chờ duyệt + video ĐÃ DUYỆT (kênh bật "Tự đăng") nhưng chưa gửi lên nền tảng — vẫn đổi giờ / giữ lại /
        bỏ được. Chờ duyệt trước, nhiều tim trước. only_review: chỉ video chờ duyệt (danh sách duyệt lần lượt, "2 / 5")."""
        sts = ("review",) if only_review else ("review",) + QUEUED
        sql = ("SELECT v.*, p.name page_name, p.platform, p.enabled page_enabled FROM videos v JOIN pages p ON p.id=v.page_id "
               f"WHERE v.status IN ({','.join('?' * len(sts))})" + (" AND v.page_id=?" if page else "") +
               " ORDER BY v.status!='review', v.likes DESC, v.updated_at DESC")
        rows = [dict(r) for r in store.q(sql, sts + ((page,) if page else ()))]
        for r in rows:
            r["key"] = f"{r['page_id']}:{r['id']}"
        if only_review:      # duyệt lần lượt: bỏ video chưa có bản edit (hộp không duyệt được -> ngõ cụt)
            rows = [r for r in rows if (data / "edited" / f"p{r['page_id']}_{r['id']}.mp4").is_file()]
        return rows

    def review_next(pid: int, vid: str, seq: str, before: list[str]) -> str:
        """Video chờ duyệt kế tiếp để hộp duyệt tự mở sau khi duyệt / bỏ: video đứng SAU video này trong danh sách lúc
        trước khi bấm (before), vòng lại đầu; video mới vào hàng chờ xếp cuối. seq = "0" (mọi kênh) hoặc id kênh."""
        now = [r["key"] for r in review_list(int(seq) if seq.isdigit() and seq != "0" else None, only_review=True)]
        key = f"{pid}:{vid}"
        i = before.index(key) if key in before else -1
        order = before[i + 1:] + before[:max(i, 0)] + [k for k in now if k not in before]
        return next((k for k in order if k in now and k != key), "")

    @app.get("/review")
    def review_old(v: str = ""):
        """Trang Hộp duyệt cũ đã bỏ: duyệt ngay trên trang Hôm nay (hộp xem trước video). Link cũ -> mở đúng video."""
        import re
        return RedirectResponse("/?v=" + v if re.fullmatch(r"\d+:\d{6,30}", v) else "/#cho-duyet", 303)

    def review_ctx(pid: int) -> dict:
        """Dữ liệu cho khối duyệt dùng chung (_review_form.html): khung giờ trống kế tiếp, giờ gợi ý, kênh đăng kèm.
        Dùng ở Hộp duyệt và hộp xem trước video."""
        pg = store.one("SELECT * FROM pages WHERE id=?", (pid,))
        s = store.page_settings(pg)
        slot = store.next_free_slot(pid, s["schedule_slots"], int(s["max_posts_per_day"]))
        mirror = None
        if int(s.get("tiktok_mirror") or 0):
            mirror = store.one("SELECT name FROM pages WHERE id=?", (int(s["tiktok_mirror"]),))
        return {"slot": slot, "slot_label": vn_fmt(slot, "%H:%M %d/%m") if slot else None, "mirror": mirror,
                "auto_pub": bool(s.get("auto_publish")),
                "suggest": suggest_slot(pid)}

    @app.post("/review/act")
    def review_act(request: Request, pid: int = Form(...), vid: str = Form(...), mode: str = Form("slot"), when: str = Form(""),
                   caption: str = Form(None), page: str = Form(""), nxt: str = Form(""), act: str = Form(""),
                   ret: str = Form(""), seq: str = Form("")):
        """Nút Duyệt / Bỏ ở Hộp duyệt. mode: slot = khung giờ trống tiếp theo, approve = theo cài đặt kênh,
        now = đăng ngay, at = giờ tự chọn, reject = bỏ. Xong -> mở video kế tiếp.
        ret=view: gửi từ hộp xem trước video (trang kênh / Video / Lịch) -> trả lại hộp đó đã cập nhật, không rời trang."""
        import datetime as dt
        from urllib.parse import urlencode
        from ..store import vn_ts
        if act in ("reject", "hold"):
            mode = act
        fields = {"caption": caption} if caption is not None else {}
        msg = ""
        # video đã tự duyệt nhưng chưa gửi lên nền tảng cũng đổi được; worker đang đăng ('uploading') thì không
        allowed = ("review",) + QUEUED
        prev = store.one("SELECT status, scheduled_at FROM videos WHERE id=? AND page_id=?", (vid, pid))
        before = ([r["key"] for r in review_list(int(seq) if seq != "0" else None, only_review=True)]
                  if seq.isdigit() else [])
        new = "schedule_pending"
        if mode in ("slot", "at"):
            if mode == "slot":
                pg = store.one("SELECT * FROM pages WHERE id=?", (pid,))
                s = store.page_settings(pg) if pg else {}
                ts = store.next_free_slot(pid, s.get("schedule_slots", ""), int(s.get("max_posts_per_day", 1))) if pg else None
                if ts is None:
                    return PlainTextResponse("Kênh không còn khung giờ trống trong 30 ngày — chọn Đăng theo cài đặt kênh "
                                             "hoặc tự chọn giờ", 400)
            else:
                try:
                    ts = vn_ts(dt.datetime.strptime(when, "%Y-%m-%dT%H:%M"))
                except ValueError:
                    return PlainTextResponse("Chưa chọn đủ ngày và giờ đăng", 400)
                if ts < time.time() + 20 * 60:
                    return PlainTextResponse("Giờ đăng phải sau ít nhất 20 phút nữa", 400)
            ok = store.set_video_if(vid, pid, allowed, status="schedule_pending", scheduled_at=ts, error=None, **fields)
            msg = f"Đã lên lịch {vn_fmt(ts)}"
            if ok:
                store.x("DELETE FROM crossposts WHERE video_id=? AND src_page_id=? AND status='pending'", (vid, pid))
                store.log(f"Duyệt + lên lịch {vid} lúc {vn_fmt(ts)}", page_id=pid)
        else:
            new = {"approve": "approved", "now": "publish_now", "reject": "rejected", "hold": "review"}.get(mode)
            if not new:
                return PlainTextResponse("Thao tác không hợp lệ", 400)
            ok = store.set_video_if(vid, pid, allowed, status=new, scheduled_at=None, error=None, **fields)
            msg = {"approved": "Đã duyệt — đăng theo cài đặt kênh", "publish_now": "Đã duyệt — đăng ngay",
                   "rejected": "Đã bỏ video", "review": "Đã giữ lại — video chờ bạn duyệt, chưa đăng"}[new]
            if ok and new == "review":
                nxt = f"{pid}:{vid}"         # ở lại video này để duyệt
            if ok and new in ("rejected", "review"):
                store.x("DELETE FROM crossposts WHERE video_id=? AND src_page_id=? AND status='pending'", (vid, pid))
        if not ok:
            msg = "Video không còn ở Hộp duyệt (tool đang / đã đăng, hoặc đã được xử lý)"
        if ret == "view" and request.headers.get("HX-Request"):
            # Hoàn tác (như Gmail / Superhuman) thay cho hỏi lại: trả video về đúng trạng thái + giờ cũ
            undo = ({"pid": pid, "vid": vid, "frm": new, "to": prev["status"], "at": prev["scheduled_at"] or "", "seq": seq,
                     "t": int(time.time())}
                    if ok and prev and mode != "hold" else None)
            # mở từ danh sách Chờ duyệt (seq): duyệt / bỏ xong tự mở video chờ duyệt kế tiếp; Giữ lại thì ở lại
            if ok and seq and mode != "hold":
                n = review_next(pid, vid, seq, before)
                if n:
                    npid, nvid = n.split(":")
                    return view_html(request, int(npid), nvid, msg=f"Video vừa rồi: {msg[0].lower() + msg[1:]}",
                                     msg_ok=True, seq=seq, undo=undo)
                return view_html(request, pid, vid, msg=msg, msg_ok=True, seq=seq, undo=undo, done=True)
            return view_html(request, pid, vid, msg=msg, msg_ok=ok, seq=seq, undo=undo)
        return RedirectResponse("/?" + urlencode({"msg": msg}) + "#cho-duyet", 303)

    @app.post("/review/undo")
    def review_undo(request: Request, pid: int = Form(...), vid: str = Form(...), frm: str = Form(...), to: str = Form(...),
                    at: str = Form(""), seq: str = Form(""), t: str = Form("0")):
        """Nút Hoàn tác sau Duyệt / Bỏ trong hộp duyệt: trả trạng thái + giờ đăng như trước. Chỉ đổi khi video VẪN ở
        trạng thái vừa đặt (worker chưa nhận đi đăng) -> không bao giờ kéo lại bài đang / đã đăng; chỉ trong 10 phút
        (nút Hoàn tác cũ để lâu không ghi đè quyết định mới hơn). Trạng thái đích chỉ là chờ duyệt / chờ đăng."""
        ok_states = ("review", "rejected") + QUEUED
        if frm not in ok_states or to not in ("review",) + QUEUED:
            return PlainTextResponse("Không hoàn tác được", 400)
        if not t.isdigit() or time.time() - int(t) > 600:
            return PlainTextResponse("Đã quá 10 phút — không hoàn tác nữa, hãy duyệt / đổi giờ lại video này", 400)
        sched = int(at) if at.isdigit() else None
        ok = store.set_video_if(vid, pid, (frm,), status=to, scheduled_at=sched)
        if ok:
            store.log(f"Hoàn tác duyệt {vid}", page_id=pid)
        return view_html(request, pid, vid, seq=seq, msg_ok=ok,
                         msg="Đã hoàn tác" if ok else "Không hoàn tác được — tool đã nhận video này đi đăng")

    @app.get("/media/raw/{vid}")
    def media_raw(vid: str):
        """Video gốc (trước edit) để so sánh ở Hộp duyệt. Chỉ phục vụ data/raw/<số>.mp4 — không mount cả thư mục."""
        import re
        from fastapi.responses import FileResponse
        if not re.fullmatch(r"\d{6,30}", vid) or not (data / "raw" / f"{vid}.mp4").is_file():
            return PlainTextResponse("Không có video gốc", 404)
        return FileResponse(data / "raw" / f"{vid}.mp4", media_type="video/mp4")

    # ---- Xem video gốc trên Douyin trước khi tải (người dùng 2026-11-25): app chuyển tiếp luồng video, KHÔNG lưu file.
    # Link CDN của Douyin cần Referer douyin.com (trình phát trong trang không tự gửi) và hết hạn sau vài giờ.
    # Phải là luồng H.264: luồng tốt nhất Douyin trả hay là H.265 -> WebView2 chỉ phát tiếng, không hình.
    # Thử link H.264 lưu lúc quét (meta.url_h264, byte đầu ~0,3 s); chưa có (video quét trước bản này) / hết hạn
    # (403 / 404 / 410) thì lấy link H.264 mới qua backend tải (preview_url ~6 s), nhớ 30 phút; hỗ trợ Range để tua.
    _preview_urls: dict[str, tuple[str, float]] = {}
    _preview_lock = threading.Lock()
    _preview_vlocks: dict[str, threading.Lock] = {}    # 1 khoá / video: trình phát gửi vài yêu cầu cùng lúc -> chỉ lấy link 1 lần

    def _preview_url(vid: str, stored: str, force: bool = False) -> str:
        from ..douyin import DouyinError
        from .. import services
        t_ask = time.time()
        with _preview_lock:
            hit = _preview_urls.get(vid)
            if hit and not force and time.time() - hit[1] < 1800:
                return hit[0]
            vlock = _preview_vlocks.setdefault(vid, threading.Lock())
        if stored and not force:
            return stored
        with vlock:
            with _preview_lock:         # yêu cầu song song vừa lấy xong link mới trong lúc chờ khoá -> dùng luôn
                hit = _preview_urls.get(vid)
                if hit and hit[1] >= t_ask:
                    return hit[0]
            try:
                url = worker._douyin().preview_url(vid)
            except DouyinError as e:
                if "Không kết nối được" not in str(e) or not services.ensure_tdl(cfg, wait=60):
                    raise
                url = worker._douyin().preview_url(vid)       # backend tải vừa bật
        with _preview_lock:
            _preview_urls[vid] = (url, time.time())
            if len(_preview_urls) > 200:
                for k in sorted(_preview_urls, key=lambda k: _preview_urls[k][1])[:100]:
                    _preview_urls.pop(k, None)
        return url

    @app.get("/media/preview/{pid}/{vid}")
    async def media_preview(request: Request, pid: int, vid: str):
        """Phát video gốc trên Douyin của video chưa tải về (hộp xem video) — chuyển tiếp từng đoạn, không ghi ra đĩa."""
        import re
        from starlette.concurrency import run_in_threadpool
        from fastapi.responses import StreamingResponse
        from ..douyin import UA, DouyinError
        if not re.fullmatch(r"\d{6,30}", vid):
            return PlainTextResponse("Mã video không hợp lệ", 400)
        row = store.one("SELECT meta FROM videos WHERE page_id=? AND id=?", (pid, vid))
        if not row:
            return PlainTextResponse("Không tìm thấy video", 404)
        try:            # link H.264 lưu lúc quét; video quét trước bản này không có -> lấy link mới
            stored = (json.loads(row["meta"] or "{}") or {}).get("url_h264") or ""
        except ValueError:
            stored = ""
        headers = {"User-Agent": UA, "Referer": "https://www.douyin.com/"}
        if request.headers.get("range"):
            headers["Range"] = request.headers["range"]
        client = httpx.AsyncClient(timeout=httpx.Timeout(30, read=60), follow_redirects=True)
        up = None
        try:
            for force in (False, True):        # link nhớ đã hết hạn (403 / 404 / 410) -> lấy link mới 1 lần
                url = await run_in_threadpool(_preview_url, vid, stored, force)
                up = await client.send(client.build_request("GET", url, headers=headers), stream=True)
                if up.status_code in (200, 206):
                    break
                await up.aclose()
                up = None
            if up is None:
                await client.aclose()
                return PlainTextResponse("Douyin không cho phát video này (có thể đã bị xoá / để riêng tư)", 502)
        except (DouyinError, httpx.HTTPError) as e:
            await client.aclose()
            return PlainTextResponse(f"Không lấy được video gốc từ Douyin: {e}", 502)
        out = {k: up.headers[k] for k in ("content-length", "content-range", "accept-ranges") if k in up.headers}
        out["content-type"] = "video/mp4"
        out["cache-control"] = "no-store"

        async def body():
            try:
                async for chunk in up.aiter_bytes(1 << 16):
                    yield chunk
            finally:
                await up.aclose()
                await client.aclose()
        return StreamingResponse(body(), status_code=up.status_code, headers=out)

    @app.get("/videos/{pid}/{vid}/view", response_class=HTMLResponse)
    def video_view(request: Request, pid: int, vid: str, seq: str = ""):
        """Hộp xem lại 1 video ở mọi trạng thái (trước chỉ Hộp duyệt mới xem được — video tự lên lịch thì không thấy
        đâu): video đã edit / gốc, caption, giờ hẹn, chỗ xem bài trên Facebook / TikTok."""
        return view_html(request, pid, vid, seq=seq)

    def view_html(request: Request, pid: int, vid: str, msg: str = "", msg_ok: bool = True, seq: str = "",
                  undo: dict | None = None, done: bool = False):
        """Vẽ hộp xem trước 1 video; video còn duyệt được (REVIEWABLE) thì kèm khối duyệt dùng chung."""
        import re
        if not re.fullmatch(r"\d{6,30}", vid):
            return PlainTextResponse("Mã video không hợp lệ", 400)
        v = store.one("SELECT v.*, p.name page_name, p.asset_id, p.platform, p.profile_id, p.enabled page_enabled "
                      "FROM videos v JOIN pages p ON p.id=v.page_id WHERE v.page_id=? AND v.id=?", (pid, vid))
        if not v:
            return PlainTextResponse("Không tìm thấy video", 404)
        edited = (data / "edited" / f"p{pid}_{vid}.mp4").is_file()
        raw = (data / "raw" / f"{vid}.mp4").is_file()
        # chỉ duyệt được khi đã có bản edit (giống Hộp duyệt: ở đó chỉ có video đã edit)
        can_review = v["status"] in ("review",) + QUEUED and edited
        seq = seq if seq.isdigit() else ""
        # vị trí trong danh sách chờ duyệt ("2 / 5") + video trước / sau (J / K) khi mở từ danh sách
        pos = total = 0
        prv = nxt = ""
        if seq:
            keys = [r["key"] for r in review_list(int(seq) if seq != "0" else None, only_review=True)]
            total = len(keys)
            key = f"{pid}:{vid}"
            if key in keys:
                i = keys.index(key)
                pos = i + 1
                prv = keys[i - 1] if i > 0 else ""
                nxt = keys[i + 1] if i + 1 < total else ""
        import json
        return render(request, "_video_view.html", v=v, edited=edited, raw=raw, msg=msg, msg_ok=msg_ok, seq=seq,
                      nxt=nxt, prv=prv, pos=pos, total=total, done=done,
                      undo=json.dumps({k: str(x) for k, x in undo.items()}) if undo else "",
                      can_review=can_review, **(review_ctx(pid) if can_review else {}))

    # ---------------- Lịch đăng ----------------
    @app.get("/calendar", response_class=HTMLResponse)
    def calendar(request: Request, week: str = "", page: int | None = None, view: str = "week", msg: str = ""):
        from . import plan
        start = plan.week_start(week)
        w = plan.week(store, store.page_settings, start, page)
        listing = []
        if view == "list":
            listing = store.q("SELECT v.*, p.name page_name, p.asset_id, p.platform FROM videos v JOIN pages p ON p.id=v.page_id "
                              "WHERE v.status IN ('schedule_pending','scheduled')" + (" AND v.page_id=?" if page else "")
                              + " ORDER BY v.scheduled_at", (page,) if page else ())
        return render(request, "calendar.html", w=w, start=start.isoformat(), cur_page=page, view=view, msg=msg,
                      pending=plan.unscheduled(store, page), listing=listing,
                      chans=[p for p in plan.nav_pages(store)])

    @app.post("/calendar/move")
    def calendar_move(pid: int = Form(...), vid: str = Form(...), date: str = Form(...), hm: str = Form(...),
                      week: str = Form(""), page: str = Form("")):
        """Kéo bài (chờ gửi lịch) hoặc video đã duyệt vào 1 ô của lịch -> đặt giờ đăng đó."""
        import datetime as dt
        from ..store import vn_ts
        try:
            ts = vn_ts(dt.datetime.strptime(f"{date} {hm}", "%Y-%m-%d %H:%M"))
        except ValueError:
            return PlainTextResponse("Ô lịch không hợp lệ", 400)
        if ts < time.time() + 20 * 60:
            return PlainTextResponse("Chỉ xếp được vào giờ sau ít nhất 20 phút nữa", 400)
        clash = store.one("SELECT id FROM videos WHERE page_id=? AND id<>? AND status IN ('schedule_pending','scheduled','uploading') "
                          "AND scheduled_at=?", (pid, vid, ts))
        if clash:
            return PlainTextResponse(f"Kênh này đã có bài lúc {vn_fmt(ts)} — chọn ô khác", 400)
        if not store.set_video_if(vid, pid, ("approved", "schedule_pending"), status="schedule_pending",
                                  scheduled_at=ts, error=None):
            return PlainTextResponse("Bài này đã gửi lịch lên nền tảng / đã đăng — không đổi giờ ở đây được", 400)
        store.x("DELETE FROM crossposts WHERE video_id=? AND src_page_id=? AND status='pending'", (vid, pid))
        store.log(f"Đổi giờ đăng {vid} -> {vn_fmt(ts)}", page_id=pid)
        from urllib.parse import urlencode
        return RedirectResponse("/calendar?" + urlencode({k: v for k, v in (("week", week), ("page", page)) if v}), 303)

    @app.post("/calendar/auto")
    def calendar_auto(week: str = Form(""), page: str = Form("")):
        from urllib.parse import urlencode
        from . import plan
        done, full = plan.auto_place(store, store.page_settings, int(page) if page.isdigit() else None)
        msg = f"Đã xếp {done} video" + (f", {full} video không còn khung giờ trống trong 30 ngày" if full else "")
        store.log("Tự xếp lịch: " + msg)
        return RedirectResponse("/calendar?" + urlencode({k: v for k, v in (("week", week), ("page", page), ("msg", msg)) if v}), 303)

    def suggest_slot(page_id: int) -> str:
        """Giờ gợi ý: sau giờ đã lên lịch muộn nhất của Page + khoảng cách tối thiểu, ít nhất 30 phút nữa."""
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

    # khai báo TRƯỚC /videos/{pid}/{vid}/{action} (route chung đó cũng khớp ".../edit")
    @app.post("/videos/{pid}/{vid}/edit", response_class=HTMLResponse)
    def video_edit(request: Request, pid: int, vid: str):
        """Nút "Edit ngay": edit 1 video Đã tải ngay (video Tìm thấy: tải về trước rồi edit), kể cả khi chạy tự
        động đang tắt / kênh đang tắt."""
        row = store.one("SELECT v.status, p.name FROM videos v JOIN pages p ON p.id=v.page_id "
                        "WHERE v.id=? AND v.page_id=?", (vid, pid))
        if not row:
            return PlainTextResponse("Không tìm thấy video", 404)
        if row["status"] not in ("new", "downloaded"):
            return PlainTextResponse(f'Chỉ edit được video "Tìm thấy" / "Đã tải" — video này đang ở '
                                     f'"{STATUS_VI.get(row["status"], row["status"])}". Tải lại trang để xem mới.', 400)
        if row["status"] == "downloaded" and not (data / "raw" / f"{vid}.mp4").exists():
            return PlainTextResponse("Không thấy file video gốc trên máy (có thể đã bị dọn) — bấm Bỏ rồi để tool "
                                     "tìm lại, hoặc chờ chạy tự động tải lại", 400)
        try:
            r = worker.request_edit(pid, vid)
        except RuntimeError as e:
            return PlainTextResponse(str(e), 400)
        store.log(f"[{row['name']}] Bấm Edit ngay {vid}"
                  + (" — chờ việc đang chạy xong" if r == "queued" else ""), page_id=pid)
        return render(request, "_edit_btn.html", pid=pid, vid=vid)

    @app.post("/videos/{pid}/{vid}/edit-cancel", response_class=HTMLResponse)
    def video_edit_cancel(request: Request, pid: int, vid: str):
        """Bỏ video khỏi hàng chờ Edit ngay (đang edit rồi thì dùng nút Dừng)."""
        if worker.cancel_manual(pid, vid):
            store.log(f"Huỷ Edit ngay {vid}", page_id=pid)
        return render(request, "_edit_btn.html", pid=pid, vid=vid)

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
    def queue(request: Request, status: str | None = None, page: int = 1, q: str = "", ch: int | None = None):
        per = 100       # chia trang thay vì vẽ 300 dòng một lúc
        page = max(1, page)
        conds, args = [], []
        if status:
            conds.append("v.status=?"); args.append(status)
        if ch:
            conds.append("v.page_id=?"); args.append(ch)
        if q.strip():
            like = f"%{q.strip()}%"
            conds.append("(v.id LIKE ? OR v.desc LIKE ? OR v.caption LIKE ?)"); args += [like, like, like]
        where, args = (" WHERE " + " AND ".join(conds) if conds else ""), tuple(args)
        total = store.one("SELECT COUNT(*) n FROM videos v" + where, args)["n"]
        sql = ("SELECT v.*, p.name page_name FROM videos v JOIN pages p ON p.id=v.page_id" + where
               + " ORDER BY v.updated_at DESC LIMIT ? OFFSET ?")
        from . import plan
        return render(request, "queue.html", videos=store.q(sql, (*args, per, (page - 1) * per)),
                      totals=store.counts(), cur=status, page=page, pages=max(1, -(-total // per)), total=total,
                      q=q, ch=ch, chans=plan.nav_pages(store))

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
