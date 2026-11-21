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
    """Môi trường giống run_ui.bat: PATH có ffmpeg/uv, ghi log khi chạy bằng pythonw. Trình duyệt = ShardBrowser."""
    os.chdir(ROOT)
    extra = [ROOT / "tools" / "ffmpeg" / "bin", Path.home() / ".local" / "bin"]   # bản cài kèm / bản đã chép
    os.environ["PATH"] = os.pathsep.join(str(p) for p in extra if p.exists()) + os.pathsep + os.environ.get("PATH", "")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    (ROOT / "data").mkdir(exist_ok=True)
    cfg = ROOT / "config.yaml"
    if not cfg.exists():                      # lần chạy đầu sau khi cài
        shutil.copy(ROOT / "config.example.yaml", cfg)
    if sys.stdout is None or "pythonw" in Path(sys.executable).name.lower():
        log = open(ROOT / "data" / "app.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
        print(f"\n===== dytool mở lúc {time.strftime('%Y-%m-%d %H:%M:%S')} =====", flush=True)


def _alive() -> bool:
    import httpx
    try:
        return httpx.get(URL + "/partials/log", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def _message(text: str, title: str = TITLE) -> None:
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
    else:
        print(text)


def _start_server():
    import uvicorn
    from .web.app import create_app
    app = create_app("config.yaml", autostart=True)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, name="dytool-server", daemon=True).start()
    for _ in range(150):                      # chờ server lên (tối đa ~30s)
        if server.started:
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


def main() -> None:
    _prepare_env()
    if sys.platform == "win32":
        import ctypes   # nhóm cửa sổ trên taskbar theo app dytool, không theo python
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("dytool.app")

    import httpx
    if _alive():
        # dytool đang chạy: có cửa sổ (app desktop) -> đưa lên trước rồi thoát
        try:
            if httpx.post(URL + "/app/show", timeout=3).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        app = server = None                    # server chạy từ run_ui.bat -> chỉ mở cửa sổ gắn vào nó
    else:
        try:
            app, server = _start_server()
        except Exception as e:
            traceback.print_exc()
            _message(f"dytool không khởi động được:\n{e}\n\nChi tiết: {ROOT / 'data' / 'app.log'}")
            return

    import webview
    from PIL import Image
    import pystray

    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True     # link douyin / facebook mở bằng trình duyệt
    # cửa sổ vừa màn hình (màn nhỏ / taskbar cao thì 880px bị tràn, mất thanh trạng thái dưới đáy)
    width, height = 1360, 880
    try:
        scr = webview.screens[0]
        width, height = min(width, int(scr.width * 0.9)), min(height, int(scr.height * 0.85))
    except Exception:
        pass
    # màu nền lúc trang chưa vẽ = màu khung app (app.css --base) để không nháy màu khi mở
    window = webview.create_window(TITLE, URL, width=width, height=height, min_size=(720, 520),
                                   background_color="#202020" if _windows_dark() else "#f3f3f3", text_select=True)
    state = {"quitting": False, "hinted": False}

    def show():
        window.restore()
        window.show()
        try:
            window.native.Invoke(__import__("System").Action(lambda: window.native.Activate()))
        except Exception:
            pass

    def quit_app(icon=None, item=None):
        w = app.state.worker if app else None
        busy = w is not None and w.running and w.current not in ("Đang chờ việc mới", "Đang dừng")
        if busy and not window.create_confirmation_dialog(
                "Thoát dytool?", f"Tool đang làm: {w.current}.\nThoát bây giờ sẽ dừng việc này (làm lại sau khi mở lại)."):
            return
        state["quitting"] = True
        if w:
            w.stop()
        if server:
            server.should_exit = True
        tray.stop()
        window.destroy()

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
        w = app.state.worker
        w.stop() if w.running else w.start()

    if app:
        app.state.show_window = show
    window.events.closing += on_closing
    window.events.shown += lambda: _style_window(window)

    menu_items = [pystray.MenuItem("Mở dytool", lambda i, it: show(), default=True)]
    if app:
        menu_items.append(pystray.MenuItem(
            lambda it: "Tạm dừng chạy tự động" if app.state.worker.running else "Bật chạy tự động", toggle_worker))
    menu_items += [pystray.Menu.SEPARATOR, pystray.MenuItem("Thoát dytool", quit_app)]
    tray = pystray.Icon("dytool", Image.open(ICON) if ICON.exists() else Image.new("RGB", (64, 64), "#172030"),
                        TITLE, menu=pystray.Menu(*menu_items))
    tray.run_detached()

    webview.start(private_mode=False, storage_path=str(ROOT / "data" / "webview"))
    # cửa sổ đã đóng hẳn (Thoát) -> dọn dẹp
    try:
        tray.stop()
    except Exception:
        pass
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
