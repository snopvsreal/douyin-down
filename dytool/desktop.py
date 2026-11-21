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
    """Môi trường giống run_ui.bat: PATH có ffmpeg/uv, Chromium ngoài AppData, ghi log khi chạy bằng pythonw."""
    os.chdir(ROOT)
    extra = [ROOT / "tools" / "ffmpeg" / "bin", Path.home() / ".local" / "bin"]   # bản cài kèm / bản đã chép
    os.environ["PATH"] = os.pathsep.join(str(p) for p in extra if p.exists()) + os.pathsep + os.environ.get("PATH", "")
    if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
        for p in (ROOT / "ms-playwright", Path(r"C:\ms-playwright")):
            if p.exists():
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(p)
                break
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


def _set_window_icon(window) -> None:
    """WinForms lấy icon của pythonw.exe -> đổi sang icon dytool cho thanh tiêu đề và taskbar."""
    if sys.platform != "win32" or not ICON.exists():
        return
    try:
        from System import Action                     # pythonnet (đi kèm pywebview trên Windows)
        from System.Drawing import Icon
        form = window.native
        form.Invoke(Action(lambda: setattr(form, "Icon", Icon(str(ICON)))))
    except Exception as e:                            # chỉ là trang trí, không được làm hỏng app
        print("Không đặt được icon cửa sổ:", e, flush=True)


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
    window = webview.create_window(TITLE, URL, width=1360, height=880, min_size=(900, 620),
                                   background_color="#0f141b", text_select=True)
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
    window.events.shown += lambda: _set_window_icon(window)

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
