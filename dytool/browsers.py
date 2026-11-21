"""Trình duyệt của dytool = ShardBrowser (github.com/ProxyShard/ShardBrowser, MIT). Không dùng Chromium riêng nữa.

Mỗi tài khoản Facebook / TikTok (và bước đăng nhập Douyin) gắn với 1 hồ sơ ShardBrowser do người dùng tự tạo trong
ShardBrowser. dytool gọi API cục bộ của ShardBrowser: POST /profiles/{id}/start -> địa chỉ CDP -> Playwright
connect_over_cdp -> đăng nhập / đăng bài -> POST /profiles/{id}/stop. dytool không tạo hồ sơ và không đọc / sửa cài đặt
trong hồ sơ (proxy, ngôn ngữ, …).

Hồ sơ của tài khoản: data/accounts/<id>/browser.json   {"profile_id": "...", "profile_name": "..."}
Hồ sơ cho đăng nhập Douyin: data/browser.json
API + token: config.yaml  browser: {shardx_api: "http://127.0.0.1:40325", shardx_token: "..."}
"""

import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
FILE = "browser.json"
DEFAULT_API = "http://127.0.0.1:40325"
DOWNLOAD = "https://github.com/ProxyShard/ShardBrowser/releases"


class BrowserError(RuntimeError):
    pass


def _cfg() -> dict:
    from . import config
    try:
        return config.load(ROOT / "config.yaml").get("browser") or {}
    except Exception:
        return {}


def _launcher_settings() -> dict:
    """settings.json của ShardBrowser trên máy này (api_secret, api_port, browser_path…) hoặc {}."""
    import os
    try:
        return json.loads((Path(os.environ.get("APPDATA", "")) / "shardx-launcher" / "settings.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _mint_token(secret: str, ttl: int = 24 * 3600) -> str:
    """Token API y như ShardBrowser tự sinh ở Settings > Automation API (src-tauri/src/api.rs `mint`):
    JWT HS256 {sub: "shardx-api", iat, exp} ký bằng api_secret. Đọc lại secret mỗi lần -> bấm Regenerate vẫn chạy."""
    import base64
    import hashlib
    import hmac
    import time

    def b64(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    now = int(time.time())
    head = b64(json.dumps({"typ": "JWT", "alg": "HS256"}, separators=(",", ":")).encode())
    body = b64(json.dumps({"sub": "shardx-api", "iat": now, "exp": now + ttl}, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{b64(sig)}"


def is_local_api(api: str) -> bool:
    """Địa chỉ API đúng là máy này? So khớp CHÍNH XÁC tên máy sau khi tách URL — không so phần đầu chuỗi
    (startswith cho qua 'http://localhost.evil.com' / 'http://127.0.0.1@evil.com' -> token tự ký bị gửi ra ngoài)."""
    from urllib.parse import urlsplit
    try:
        u = urlsplit(api.strip())
        return u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost", "::1") and not u.username \
            and u.path in ("", "/") and not u.query
    except ValueError:
        return False


def shardx_settings() -> tuple[str, str]:
    """(địa chỉ API, token). Mặc định tự lấy từ ShardBrowser cài trên máy này — không cần dán token;
    config.yaml `browser:` chỉ dùng khi muốn chỉ định tay (ShardBrowser ở máy khác / cổng khác)."""
    c, ls = _cfg(), _launcher_settings()
    api = c.get("shardx_api") or (f"http://127.0.0.1:{ls['api_port']}" if ls.get("api_port") else DEFAULT_API)
    if is_local_api(api) and ls.get("api_secret"):
        token = _mint_token(ls["api_secret"])            # luôn mới, đúng secret hiện tại
    else:
        token = (c.get("shardx_token") or "").strip()
    return api.rstrip("/"), token


def auto_token() -> bool:
    """Token tự lấy được từ ShardBrowser trên máy (không cần người dùng dán)."""
    return bool(_launcher_settings().get("api_secret"))


def launcher_exe() -> Path | None:
    """File chạy ShardBrowser Launcher (tìm trong danh sách chương trình đã cài của Windows)."""
    import sys
    if sys.platform != "win32":
        return None
    import winreg
    roots = [(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")]
    for hive, base in roots:
        try:
            with winreg.OpenKey(hive, base) as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    try:
                        with winreg.OpenKey(k, winreg.EnumKey(k, i)) as sub:
                            if "ShardBrowser" not in str(winreg.QueryValueEx(sub, "DisplayName")[0]):
                                continue
                            for name in ("DisplayIcon", "InstallLocation"):
                                try:
                                    v = str(winreg.QueryValueEx(sub, name)[0]).split(",")[0].strip('" ')
                                except OSError:
                                    continue
                                p = Path(v)
                                if p.is_file() and p.suffix.lower() == ".exe":
                                    return p
                                if p.is_dir():
                                    exe = next((x for x in p.glob("*.exe") if "unins" not in x.name.lower()), None)
                                    if exe:
                                        return exe
                    except OSError:
                        continue
        except OSError:
            continue
    return None


def _start_launcher(api: str) -> bool:
    """ShardBrowser chưa chạy -> mở app (thu nhỏ) và chờ API sẵn sàng (tối đa ~45s)."""
    import subprocess
    import time
    exe = launcher_exe()
    if not exe:
        return False
    subprocess.Popen([str(exe)], cwd=str(exe.parent))
    for _ in range(45):
        time.sleep(1)
        try:
            httpx.get(api + "/health", timeout=2)
            return True
        except httpx.HTTPError:
            continue
    return False


def _api(method: str, path: str, _retry: bool = True, **kw):
    api, token = shardx_settings()
    if not token:
        raise BrowserError("Chưa có token API của ShardBrowser — mở ShardBrowser 1 lần, hoặc xem Cài đặt > Trình duyệt")
    try:
        # ShardBrowser trên máy này: chờ KẾT NỐI tối đa 1 s (Windows không từ chối ngay cổng đang đóng -> trước treo tới
        # 60 s khi launcher tắt); chờ TRẢ LỜI vẫn 60 s (mở hồ sơ lâu)
        to = httpx.Timeout(60.0, connect=1.0) if is_local_api(api) else 60
        r = httpx.request(method, api + path, headers={"Authorization": f"Bearer {token}"}, timeout=to, **kw)
    except httpx.ConnectError as e:
        if _retry and _start_launcher(api):          # tự mở ShardBrowser rồi thử lại 1 lần
            return _api(method, path, _retry=False, **kw)
        hint = "hãy mở app ShardBrowser" if launcher_exe() else f"chưa cài ShardBrowser — tải tại {DOWNLOAD}"
        raise BrowserError(f"Không kết nối được ShardBrowser tại {api} — {hint} ({e})") from e
    except httpx.HTTPError as e:
        raise BrowserError(f"Không kết nối được ShardBrowser tại {api} — hãy mở app ShardBrowser trước ({e})") from e
    if r.status_code == 401:
        raise BrowserError("Token API ShardBrowser sai hoặc đã đổi — lấy lại ở ShardBrowser > Settings > Automation API")
    if r.status_code >= 400:
        raise BrowserError(f"ShardBrowser trả lỗi {r.status_code}: {r.text[:200]}")
    return r.json() if r.content else {}


def list_profiles(start_launcher: bool = True) -> list[dict]:
    """Hồ sơ ShardBrowser để chọn trên giao diện: [{id, name, folder, running}].
    start_launcher=False: ShardBrowser đang tắt thì báo lỗi ngay, không tự mở rồi chờ ~45s (dùng khi vẽ trang)."""
    return [{"id": p.get("id"), "name": p.get("name") or p.get("id"), "folder": p.get("folder") or "",
             "running": bool(p.get("running"))} for p in _api("GET", "/profiles", _retry=start_launcher)]


def get_choice(acc_dir: Path) -> dict:
    """Hồ sơ đã gắn: {'profile_id', 'profile_name'} hoặc {} nếu chưa chọn."""
    try:
        c = json.loads((acc_dir / FILE).read_text("utf-8"))
        return c if c.get("profile_id") else {}
    except (OSError, ValueError):
        return {}


def set_choice(acc_dir: Path, profile_id: str, profile_name: str = "") -> None:
    if not profile_id:
        (acc_dir / FILE).unlink(missing_ok=True)
        return
    (acc_dir / FILE).write_text(json.dumps({"profile_id": profile_id, "profile_name": profile_name},
                                           ensure_ascii=False), "utf-8")


def missing_profile(acc_dir: Path) -> bool:
    return not get_choice(acc_dir)


FOLDER = "dytool"          # thư mục chứa các hồ sơ dytool tạo trong ShardBrowser


def create_profile(name: str) -> dict:
    """Tạo 1 hồ sơ mới y như nút "New profile" của ShardBrowser: lấy cấu hình do chính ShardBrowser sinh
    (GET /fingerprint/new, máy hiện tại) và lưu nguyên vẹn — dytool không sửa gì trong đó."""
    fp = _api("GET", "/fingerprint/new").get("fingerprint")
    if not fp:
        raise BrowserError("ShardBrowser không sinh được cấu hình hồ sơ mới (thư viện trống?) — mở ShardBrowser kiểm tra")
    meta = _api("POST", "/profiles", json={"name": name, "folder": FOLDER, "fingerprint": fp})
    return {"profile_id": meta["id"], "profile_name": meta.get("name") or name}


def ensure_profile(acc_dir: Path, name: str) -> tuple[dict, bool]:
    """Hồ sơ đã gắn, hoặc tạo hồ sơ mới tên `name` rồi gắn luôn. Trả (lựa chọn, có_tạo_mới)."""
    c = get_choice(acc_dir)
    if c:
        return c, False
    c = create_profile(name)
    set_choice(acc_dir, c["profile_id"], c["profile_name"])
    return c, True


ENGINE_COPY = Path(r"C:\ShardX\ShardX-Windows")      # bản chép nhân ra ngoài AppData (xem sync_engine)


def sync_engine() -> None:
    """Máy chặn chạy exe trong AppData (lỗi side-by-side) -> nhân ShardX được chép ra C:\\ShardX và ShardBrowser trỏ
    `browser_path` (settings.json) vào đó. ShardBrowser cập nhật nhân trong AppData -> chép lại bản mới trước khi mở hồ sơ.
    Chỉ làm khi browser_path đang trỏ vào bản chép này; lỗi (đang có hồ sơ mở, khoá file) -> dùng tạm bản cũ."""
    appdata = _shardx_dir()
    try:
        bp = json.loads((appdata / "settings.json").read_text("utf-8")).get("browser_path") or ""
        if Path(bp).parent != ENGINE_COPY:
            return
        m = json.loads((appdata / "runtime" / "manifest.json").read_text("utf-8"))
    except (OSError, ValueError):
        return
    src = appdata / "runtime" / "ShardX-Windows"
    stamp = _engine_stamp(m)
    mark = ENGINE_COPY.parent / ".dytool-engine"
    if not (src / "chrome.exe").exists() or (mark.exists() and mark.read_text("utf-8") == stamp
                                              and (ENGINE_COPY / "chrome.exe").exists()):
        return
    try:
        _copy_engine(src, stamp)
    except OSError:
        pass                                     # đang có hồ sơ mở / khoá file -> dùng tạm bản cũ, lần sau chép lại


def _engine_stamp(manifest: dict) -> str:
    return "%s|%s|%s" % (manifest.get("installed_chromium_version"), manifest.get("installed_engine_build"),
                         manifest.get("browser_etag"))


def _copy_engine(src: Path, stamp: str) -> None:
    """Chép nhân ShardX ra ENGINE_COPY qua thư mục tạm rồi đổi tên (không để lại bản chép dở)."""
    import shutil
    ENGINE_COPY.parent.mkdir(parents=True, exist_ok=True)
    mark = ENGINE_COPY.parent / ".dytool-engine"
    tmp = ENGINE_COPY.with_name(ENGINE_COPY.name + ".new")
    old = ENGINE_COPY.with_name(ENGINE_COPY.name + ".old")
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(src, tmp)
        shutil.rmtree(old, ignore_errors=True)
        if ENGINE_COPY.exists():
            ENGINE_COPY.rename(old)
        tmp.rename(ENGINE_COPY)
        shutil.rmtree(old, ignore_errors=True)
        mark.write_text(stamp, "utf-8")
    except OSError:
        if not ENGINE_COPY.exists() and old.exists():
            old.rename(ENGINE_COPY)              # đổi tên giữa chừng thất bại -> trả bản cũ về chỗ
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _shardx_dir() -> Path:
    import os
    return Path(os.environ.get("APPDATA", "")) / "shardx-launcher"


def engine_blocked(exe: Path) -> bool:
    """Windows có chặn chạy file nhân này không? Tạo tiến trình ở trạng thái TREO (CREATE_SUSPENDED): Windows kiểm tra
    manifest side-by-side / chính sách chặn ngay lúc tạo -> máy chặn exe trong AppData báo lỗi (WinError 14001,
    'side-by-side configuration is incorrect') mà không mở cửa sổ nào. Tạo được thì huỷ ngay."""
    import subprocess
    import sys
    if sys.platform != "win32" or not exe.exists():
        return False
    try:
        p = subprocess.Popen([str(exe), "--version"], creationflags=0x00000004,     # CREATE_SUSPENDED
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return True
    try:
        p.kill()
        p.wait(5)
    except Exception:
        pass
    return False


def _kill_launcher() -> None:
    import subprocess
    import time
    subprocess.run(["taskkill", "/IM", "shardbrowser-launcher.exe", "/T", "/F"], capture_output=True)
    for _ in range(20):
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq shardbrowser-launcher.exe", "/NH"],
                             capture_output=True, text=True).stdout
        if "shardbrowser-launcher.exe" not in out.lower():
            return
        time.sleep(0.5)


def relocate_engine(sx: Path) -> None:
    """Nhân ShardX trong AppData bị chặn -> chép ra C:\\ShardX, trỏ ShardBrowser sang (settings.json `browser_path`,
    ShardBrowser có sẵn mục này) rồi mở lại launcher. Phải tắt launcher trước khi sửa settings.json: đang chạy thì nó
    ghi đè lại khi thoát. Đang có hồ sơ mở thì không làm (tắt launcher sẽ đóng hồ sơ đó)."""
    try:
        running = _api("GET", "/running", _retry=False)
    except BrowserError:
        running = []
    if running:
        raise BrowserError("Nhân trình duyệt ShardBrowser bị Windows chặn chạy trong AppData. dytool cần chép nó ra "
                           "C:\\ShardX nhưng đang có hồ sơ mở — đóng các cửa sổ ShardBrowser rồi thử lại")
    print("Nhân ShardBrowser bị chặn chạy trong AppData -> chép ra C:\\ShardX và trỏ ShardBrowser sang đó", flush=True)
    try:
        manifest = json.loads((sx / "runtime" / "manifest.json").read_text("utf-8"))
    except (OSError, ValueError):
        manifest = {}
    try:
        _copy_engine(sx / "runtime" / "ShardX-Windows", _engine_stamp(manifest))
    except OSError as e:
        raise BrowserError(f"Không chép được nhân ShardBrowser ra C:\\ShardX: {e}") from e
    if engine_blocked(ENGINE_COPY / "chrome.exe"):
        raise BrowserError("Nhân ShardBrowser bị Windows chặn chạy cả ở C:\\ShardX — kiểm tra phần mềm diệt virus / "
                           "chính sách AppLocker của máy")
    _kill_launcher()
    path = sx / "settings.json"
    s = json.loads(path.read_text("utf-8"))           # đọc lại sau khi launcher đã tắt (nó có thể vừa ghi)
    s["browser_path"] = str(ENGINE_COPY / "chrome.exe")
    path.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    _start_launcher(shardx_settings()[0])


_engine_ok = False


def ensure_engine() -> None:
    """Gọi trước khi mở hồ sơ. Nhân ShardX chạy được -> thôi; bị chặn (máy chặn exe trong AppData) -> tự chuyển ra
    C:\\ShardX; đã chuyển rồi -> chép lại khi ShardBrowser cập nhật nhân. Kiểm tra 1 lần mỗi lần chạy app."""
    global _engine_ok
    import sys
    if _engine_ok or sys.platform != "win32":
        return
    sx = _shardx_dir()
    try:
        bp = json.loads((sx / "settings.json").read_text("utf-8")).get("browser_path") or ""
    except (OSError, ValueError):
        return                                         # ShardBrowser chưa chạy lần nào
    if bp:
        if Path(bp).parent == ENGINE_COPY:
            sync_engine()
        _engine_ok = True                              # người dùng tự đặt nhân khác -> để nguyên
        return
    exe = sx / "runtime" / "ShardX-Windows" / "chrome.exe"
    if not exe.exists():
        return                                         # chưa tải nhân xong -> lần sau kiểm tra lại
    if engine_blocked(exe):
        relocate_engine(sx)
    _engine_ok = True


def attach(pw, acc_dir: Path, headless: bool):
    """Mở hồ sơ ShardBrowser của tài khoản, gắn Playwright, trả về BrowserContext. ctx.close() đóng luôn hồ sơ."""
    choice = get_choice(acc_dir)
    if not choice:
        raise BrowserError("Chưa chọn hồ sơ ShardBrowser cho tài khoản này — vào Tài khoản & kênh, bấm Đăng nhập (dytool tự tạo hồ sơ)")
    pid = choice["profile_id"]
    ensure_engine()
    res = _api("POST", f"/profiles/{pid}/start", json={"headless": bool(headless)})
    ws = (res.get("cdp") or {}).get("web_socket_debugger_url")
    if not ws:
        raise BrowserError("ShardBrowser không mở được hồ sơ '%s': %s"
                           % (choice.get("profile_name") or pid, res.get("cdp_error") or "không có địa chỉ CDP"))
    browser = pw.chromium.connect_over_cdp(ws)       # chỉ dùng giao thức CDP của Playwright, không chạy Chromium riêng
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()

    def close():
        try:
            browser.close()          # chỉ ngắt kết nối Playwright
        except Exception:
            pass
        try:
            _api("POST", f"/profiles/{pid}/stop")
        except BrowserError:
            pass
    ctx.close = close                # nơi gọi vẫn dùng ctx.close() như trước
    return ctx
