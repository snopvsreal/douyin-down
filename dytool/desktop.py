"""App desktop dytool: cửa sổ riêng (pywebview + Edge WebView2) bọc giao diện web, icon ở khay hệ thống.

Chạy: dytool.exe (launcher C#, installer/launcher.cs) -> .venv\\Scripts\\pythonw.exe -m dytool.desktop
      hoặc khi phát triển: uv run python -m dytool.desktop

- Server FastAPI chạy ngay trong tiến trình này (thread nền), worker tự bật.
- Đóng cửa sổ = thu xuống khay, tool vẫn chạy (lên lịch, edit, đăng). Thoát hẳn: chuột phải icon khay > Thoát.
- Mở dytool.exe lần 2: đưa cửa sổ đang chạy lên trước (POST /app/show), không mở server thứ 2.
"""

import os
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("DYTOOL_PORT", "8000"))
URL = f"http://127.0.0.1:{PORT}"
ICON = ROOT / "installer" / "dytool.ico"
TITLE = "dytool"


def _prepare_env() -> None:
    """Môi trường giống run_ui.bat: PATH có ffmpeg/uv, ghi log khi chạy bằng pythonw. Trình duyệt = nhân ShardX nhúng (browsers.py)."""
    os.chdir(ROOT)
    extra = [ROOT / "tools" / "ffmpeg" / "bin", Path.home() / ".local" / "bin"]   # bản cài kèm / bản đã chép
    os.environ["PATH"] = os.pathsep.join(str(p) for p in extra if p.exists()) + os.pathsep + os.environ.get("PATH", "")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    (ROOT / "data").mkdir(exist_ok=True)
    cfg = ROOT / "config.yaml"
    if not cfg.exists():                      # lần chạy đầu sau khi cài
        shutil.copy(ROOT / "config.example.yaml", cfg)
    if sys.stdout is None or "pythonw" in Path(sys.executable).name.lower():
        path = ROOT / "data" / "app.log"
        try:                                  # xoay vòng: quá 5 MB -> đổi thành app.log.1 (giữ 1 bản cũ)
            if path.stat().st_size > 5 * 1024 * 1024:
                path.replace(path.with_suffix(".log.1"))
        except OSError:
            pass
        log = open(path, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
        print(f"\n===== dytool mở lúc {time.strftime('%Y-%m-%d %H:%M:%S')} =====", flush=True)


def _alive() -> bool:
    import httpx
    try:
        return httpx.get(URL + "/partials/log", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


_app_mutex = None


def _first_instance() -> bool:
    """Khoá theo tên của Windows: lần mở thứ 2 trở đi trả False (kể cả khi lần 1 chưa khởi động xong)."""
    global _app_mutex
    if sys.platform != "win32":
        return True
    import ctypes
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = ctypes.c_void_p
    _app_mutex = k32.CreateMutexW(None, True, "Local\\dytool-desktop")
    return not (_app_mutex and k32.GetLastError() == 183)     # 183 = ERROR_ALREADY_EXISTS


def _message(text: str, title: str = TITLE) -> None:
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
    else:
        print(text)


def _start_server():
    import uvicorn
    from .web.app import create_app
    # autostart=False: chỉ bật worker SAU KHI server đã chiếm được cổng. Bật trước thì lần mở thứ 2 (cổng đã bị
    # lần 1 giữ) vẫn kịp chạy 1 worker trong ~30s chờ -> 2 worker cùng đăng 1 video
    app = create_app("config.yaml", autostart=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, name="dytool-server", daemon=True).start()
    for _ in range(150):                      # chờ server lên (tối đa ~30s)
        if server.started:
            # việc nền khi mở app (model giọng nói, kiểm tra cập nhật) — không chờ, vào thẳng trang chính; chạy tự
            # động KHÔNG tự bật (web/app.py begin_startup)
            app.state.begin_startup()
            return app, server
        time.sleep(0.2)
    raise RuntimeError(f"Không mở được server ở cổng {PORT} (cổng đang bị chương trình khác dùng?)")


def _windows_dark() -> bool:
    """Windows đang ở chế độ tối cho app (Settings > Personalization > Colors)?"""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


WEBVIEW2_KEY = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def _has_webview2() -> bool:
    """Microsoft Edge WebView2 Runtime đã cài? (cửa sổ dytool cần nó). Windows 10 LTSC / Sandbox thường không có."""
    if sys.platform != "win32":
        return True
    import winreg
    for hive, key in ((winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\" + WEBVIEW2_KEY),
                      (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\" + WEBVIEW2_KEY),
                      (winreg.HKEY_CURRENT_USER, "Software\\" + WEBVIEW2_KEY)):
        try:
            with winreg.OpenKey(hive, key) as k:
                v = str(winreg.QueryValueEx(k, "pv")[0])
                if v and v != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


def _browser_mode(app, server, pystray, Image) -> None:
    """Không có WebView2: không mở được cửa sổ riêng -> mở giao diện trong trình duyệt mặc định, giữ icon ở khay.
    Trước: pywebview lỗi ngầm, tool chạy nhưng người dùng không thấy gì."""
    import threading
    import webbrowser

    def open_ui(*_):
        webbrowser.open(URL + "/")

    if app:
        app.state.show_window = open_ui          # mở dytool.exe lần 2 -> mở lại tab giao diện
    open_ui()
    threading.Thread(target=_message, daemon=True, args=(
        "Máy này chưa có Microsoft Edge WebView2 Runtime nên dytool mở giao diện trong trình duyệt.\n\n"
        "Muốn dytool có cửa sổ riêng: cài WebView2 (miễn phí, của Microsoft) tại\n"
        "https://developer.microsoft.com/microsoft-edge/webview2/  rồi mở lại dytool.",)).start()

    def quit_app(icon, item):
        icon.stop()
        from . import services
        services.shutdown(app.state.worker if app else None)   # tắt hết dịch vụ đi kèm (không chạy ngầm sau khi thoát)
        if server:
            server.should_exit = True

    with Image.open(ICON) if ICON.exists() else Image.new("RGB", (64, 64), "#172030") as im:
        im.load()
        img = im.copy()
    pystray.Icon("dytool", img, TITLE, menu=pystray.Menu(
        pystray.MenuItem("Mở dytool (trình duyệt)", open_ui, default=True),
        pystray.Menu.SEPARATOR, pystray.MenuItem("Thoát dytool", quit_app))).run()


def _allow_close_on_shutdown(state: dict) -> None:
    """Windows tắt máy / đăng xuất: cho cửa sổ đóng hẳn. Không có thì on_closing luôn huỷ (thu xuống khay) ->
    Windows hiện 'dytool đang ngăn tắt máy'. Worker đang đăng dở -> lần mở sau trả về Chờ duyệt (không đăng lại)."""
    if sys.platform != "win32":
        return
    try:
        from Microsoft.Win32 import SessionEndingEventHandler, SystemEvents     # pythonnet (đi kèm pywebview)

        def ending(sender, e):
            state["quitting"] = True
            print("Windows đang tắt máy / đăng xuất -> đóng dytool", flush=True)
        SystemEvents.SessionEnding += SessionEndingEventHandler(ending)
    except Exception as e:
        print("Không theo dõi được sự kiện tắt máy:", e, flush=True)


def _style_window(window) -> None:
    """Cho cửa sổ giống app Windows: icon dytool (thay icon pythonw) + thanh tiêu đề tối khi Windows đang tối."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from System import Action                     # pythonnet (đi kèm pywebview trên Windows)
        from System.Drawing import Icon
        form = window.native

        def apply():
            if ICON.exists():
                form.Icon = Icon(str(ICON))
            if _windows_dark():                       # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (Windows 10 20H1+)
                hwnd = ctypes.c_void_p(int(form.Handle.ToInt64()))
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(1)), 4)
        form.Invoke(Action(apply))
    except Exception as e:                            # chỉ là trang trí, không được làm hỏng app
        print("Không chỉnh được kiểu cửa sổ:", e, flush=True)


class WindowApi:
    """Gọi từ trang (`window.pywebview.api.…`): 3 nút cửa sổ tự vẽ ở thanh trên cùng (base.html). Thuộc tính bắt đầu
    bằng `_` không lộ ra JavaScript."""

    def __init__(self):
        self._window = None

    def win(self, act: str):
        from . import winchrome
        w = self._window
        if act == "min":
            w.minimize()
        elif act == "max":
            w.restore() if winchrome.is_maximized(w.native) else w.maximize()
        elif act == "close":            # như nút ✕ của Windows: on_closing thu xuống khay
            from System import Action
            w.native.Invoke(Action(w.native.Close))
        return self.state()

    def state(self) -> dict:
        from . import winchrome
        try:
            return {"max": winchrome.is_maximized(self._window.native)}
        except Exception:
            return {"max": False}

    def bg(self, color: str):
        """Màu nền trang (đổi sáng / tối) -> màu viền đổi kích thước quanh trang, cho liền màu."""
        import re
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color or ""):
            return
        try:
            from System import Action
            from System.Drawing import ColorTranslator
            f = self._window.native
            f.Invoke(Action(lambda: setattr(f, "BackColor", ColorTranslator.FromHtml(color))))
        except Exception as e:
            print("Không đổi được màu viền cửa sổ:", e, flush=True)

    def _push(self):
        try:
            self._window.evaluate_js(
                f"document.documentElement.classList.toggle('win-max', {str(self.state()['max']).lower()})")
        except Exception:
            pass


def _frameless(window, url: str) -> None:
    """Sau khi cửa sổ hiện: đổi kích thước bằng mép, snap, phóng to không đè taskbar, kéo bằng app-region (winchrome).
    Cửa sổ mở với trang trống; bật vùng kéo của WebView2 xong mới tải giao diện (cài đặt này chỉ có hiệu lực từ lần
    tải trang kế — tải lại trang giữa lúc pywebview đang mở trang đầu thì kẹt ở about:blank)."""
    try:
        if sys.platform == "win32":
            from System import Action
            from . import winchrome
            form = window.native
            window.events.loaded.wait(20)          # trang trống đầu tiên đã vẽ = WebView2 khởi tạo xong
            form.Invoke(Action(lambda: (winchrome.install(form), winchrome.enable_drag_regions(form))))
    except Exception as e:                         # thiếu phần này cửa sổ vẫn dùng được (nút tự vẽ vẫn chạy)
        print("Không bật được khung cửa sổ tự vẽ:", e, flush=True)
    window.load_url(url)


def main() -> None:
    _prepare_env()
    if sys.platform == "win32":
        import ctypes   # nhóm cửa sổ trên taskbar theo app dytool, không theo python
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("dytool.app")

    import httpx
    if not _first_instance():
        # đã có 1 dytool đang mở (có thể còn đang khởi động, import torch mất vài giây): chờ nó lên rồi đưa
        # cửa sổ của nó ra trước — không tự bật server / worker thứ 2
        for _ in range(60):
            try:
                if httpx.post(URL + "/app/show", timeout=3).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
        _message("dytool đang chạy (xem biểu tượng ở khay hệ thống cạnh đồng hồ).")
        return
    if _alive():
        # dytool đang chạy: có cửa sổ (app desktop) -> đưa lên trước rồi thoát
        try:
            if httpx.post(URL + "/app/show", timeout=3).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        own = None                             # server chạy từ run_ui.bat -> chỉ mở cửa sổ gắn vào nó
    else:
        # Bật server ở luồng riêng, SONG SONG với nạp pywebview / pystray (~1,5 s mỗi thứ) -> cửa sổ hiện sớm hơn
        # vài giây; cửa sổ chờ server sẵn sàng rồi mới tải giao diện (load_ui)
        own = {"app": None, "server": None, "err": None, "ready": threading.Event()}

        def boot():
            try:
                own["app"], own["server"] = _start_server()
            except Exception as e:
                traceback.print_exc()
                own["err"] = e
            finally:
                own["ready"].set()
        threading.Thread(target=boot, daemon=True, name="dytool-boot").start()

    def server_up() -> bool:
        """Chờ server của mình lên (chạy gắn vào server có sẵn thì True ngay). Lỗi -> báo người dùng, False."""
        if own is None:
            return True
        own["ready"].wait()
        if own["err"] is not None:
            _message(f"dytool không khởi động được:\n{own['err']}\n\nChi tiết: {ROOT / 'data' / 'app.log'}")
            return False
        return True

    def _app():
        return own["app"] if own else None

    from PIL import Image
    import pystray

    if not _has_webview2():
        if server_up():
            _browser_mode(_app(), own and own["server"], pystray, Image)
        return
    import webview
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True     # link douyin / facebook mở bằng trình duyệt
    # cửa sổ vừa màn hình (màn nhỏ / taskbar cao thì 880px bị tràn, mất thanh trạng thái dưới đáy)
    width, height = 1360, 880
    try:
        scr = webview.screens[0]
        width, height = min(width, int(scr.width * 0.9)), min(height, int(scr.height * 0.85))
    except Exception:
        pass
    # màu nền lúc trang chưa vẽ = màu nền app (app.css --canvas) để không nháy màu khi mở.
    # frameless: không có thanh tiêu đề của Windows — thanh trên cùng của trang làm thanh tiêu đề (winchrome.py)
    api = WindowApi()
    bg = "#121212" if _windows_dark() else "#f5f5f5"
    window = webview.create_window(TITLE, html=f'<body style="margin:0;background:{bg}"></body>', width=width,
                                   height=height, min_size=(720, 520), background_color=bg, text_select=True,
                                   frameless=True, easy_drag=False, js_api=api)
    api._window = window
    state = {"quitting": False, "hinted": False}

    def show():
        window.restore()
        window.show()
        try:
            window.native.Invoke(__import__("System").Action(lambda: window.native.Activate()))
        except Exception:
            pass

    def quit_app(icon=None, item=None):
        app = _app()
        w = app.state.worker if app else None
        busy = w is not None and w.running and w.current not in ("Đang chờ việc mới", "Đang dừng")
        if busy and not window.create_confirmation_dialog(
                "Thoát dytool?", f"Tool đang làm: {w.current}.\nThoát bây giờ sẽ dừng việc này (làm lại sau khi mở lại)."):
            return
        state["quitting"] = True
        if w:
            w.stop()
        tray.stop()
        window.destroy()            # webview.start() trả về -> cuối main() tắt hết dịch vụ rồi mới tắt server

    def on_closing():
        if state["quitting"]:
            return True
        window.hide()                          # đóng cửa sổ = thu xuống khay, tool vẫn chạy
        if not state["hinted"]:
            state["hinted"] = True
            try:
                tray.notify("dytool vẫn đang chạy ở khay hệ thống. Chuột phải icon để mở lại hoặc thoát.", TITLE)
            except Exception:
                pass
        return False

    def toggle_worker(icon, item):
        app = _app()
        if app:
            w = app.state.worker
            w.stop() if w.running else w.start()

    def load_ui(url: str):
        """Cửa sổ đã hiện (trang trống) -> chờ server lên rồi mới tải giao diện."""
        if not server_up():
            state["quitting"] = True
            window.destroy()
            return
        if own:
            own["app"].state.show_window = show
        _frameless(window, url)

    window.events.closing += on_closing
    window.events.shown += lambda: _style_window(window)
    window.events.shown += lambda: threading.Thread(target=load_ui, args=(URL + "/",), daemon=True).start()
    window.events.maximized += lambda: api._push()
    window.events.restored += lambda: api._push()
    window.events.shown += lambda: _allow_close_on_shutdown(state)

    menu_items = [pystray.MenuItem("Mở dytool", lambda i, it: show(), default=True)]
    if own:
        menu_items.append(pystray.MenuItem(
            lambda it: "Tạm dừng chạy tự động" if _app() and _app().state.worker.running else "Bật chạy tự động",
            toggle_worker))
    menu_items += [pystray.Menu.SEPARATOR, pystray.MenuItem("Thoát dytool", quit_app)]
    def _icon_image():
        # đọc hết vào bộ nhớ rồi đóng file: Image.open giữ file mở -> trình cài bản mới không ghi đè được dytool.ico
        if not ICON.exists():
            return Image.new("RGB", (64, 64), "#172030")
        with Image.open(ICON) as im:
            im.load()
            return im.copy()

    tray = pystray.Icon("dytool", _icon_image(),
                        TITLE, menu=pystray.Menu(*menu_items))
    tray.run_detached()

    webview.start(private_mode=False, storage_path=str(ROOT / "data" / "webview"))
    # cửa sổ đã đóng hẳn (Thoát) -> dọn dẹp
    try:
        tray.stop()
    except Exception:
        pass
    app, server = _app(), own and own["server"]
    if app:
        # tắt hết dịch vụ đi kèm: worker (như nút Dừng), trình duyệt đã mở, VoiceStudio chạy ngầm, backend tải Douyin,
        # cửa sổ đăng nhập, ffmpeg… — thoát app là không còn gì chạy ngầm
        from . import services
        services.shutdown(app.state.worker)
    if server:
        server.should_exit = True
        time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        _message(f"dytool gặp lỗi khi mở. Chi tiết: {ROOT / 'data' / 'app.log'}")
        raise
