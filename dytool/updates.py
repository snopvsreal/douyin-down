"""Tự cập nhật ngay trong app: dytool, VoiceStudio.

Kiểm tra: GitHub releases/latest của từng app (lúc mở app + mỗi 6 giờ) so với bản đang cài
(dytool: metadata gói; VoiceStudio: DisplayVersion trong danh sách phần mềm của Windows).
Cập nhật: tải file cài chính thức trong bản phát hành -> so SHA256 với mã GitHub công bố (asset.digest) -> không khớp
hoặc không có mã thì KHÔNG cài -> cài im lặng:
- VoiceStudio: đóng app (không khi đang edit/lồng tiếng) -> /S -> mở lại nếu trước đó đang mở
(Trình duyệt ShardX nhúng qua SDK shardx: nhân tự cập nhật khi mở app — browsers.install_engine_background.)
- dytool: chạy trình cài /SILENT /RELAUNCH: trình cài tự đóng dytool, hiện tiến độ, cài xong tự mở lại dytool
"""

import hashlib
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
CHECK_EVERY = 6 * 3600

APPS = {
    "dytool": {"name": "dytool", "repo": "snopvsreal/douyin-down", "asset": r"^dytool-Setup-[\d.]+\.exe$"},
    "voicestudio": {"name": "VoiceStudio", "repo": "debpalash/VoiceStudio", "asset": r"^VoiceStudio-[\d.]+-win-x64\.exe$",
                    "reg": r"^VoiceStudio( \d|$)"},
}

_lock = threading.Lock()
_latest: dict[str, dict] = {}            # app -> {version, url, file, sha256, notes, page}
_checked = {"at": 0.0, "error": None}
_job: dict[str, dict] = {}               # app -> {state: downloading|installing|done|error, msg, pct}


def _ver(s: str | None) -> tuple:
    nums = re.findall(r"\d+", (s or "").lstrip("vV").split("-")[0])
    return tuple(int(n) for n in nums[:4])


# ---------------- bản đang cài ----------------
def _reg_version(pattern: str) -> str | None:
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
                            if re.search(pattern, str(winreg.QueryValueEx(sub, "DisplayName")[0])):
                                return str(winreg.QueryValueEx(sub, "DisplayVersion")[0])
                    except OSError:
                        continue
        except OSError:
            continue
    return None


_inst_cache: dict[str, tuple[float, str | None]] = {}


def installed(app: str) -> str | None:
    """Bản đang cài. Nhớ 60 giây: thanh trạng thái hỏi mỗi 5 giây, mỗi lần quét cả 3 nhánh registry Uninstall."""
    hit = _inst_cache.get(app)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    v = _installed(app)
    _inst_cache[app] = (time.time(), v)
    return v


def _installed(app: str) -> str | None:
    if app == "dytool":
        try:
            from importlib.metadata import version
            return version("dytool")
        except Exception:
            m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text("utf-8"))
            return m.group(1) if m else None
    return _reg_version(APPS[app]["reg"])


# ---------------- bản mới nhất trên GitHub ----------------
def _fetch_latest(app: str) -> dict | None:
    a = APPS[app]
    r = httpx.get(f"https://api.github.com/repos/{a['repo']}/releases/latest", timeout=20, follow_redirects=True,
                  headers={"Accept": "application/vnd.github+json", "User-Agent": "dytool-updater"})
    r.raise_for_status()
    rel = r.json()
    asset = next((x for x in rel.get("assets", []) if re.search(a["asset"], x["name"])), None)
    if not asset:
        return None
    digest = asset.get("digest") or ""
    return {"version": rel["tag_name"].lstrip("vV"), "url": asset["browser_download_url"], "file": asset["name"],
            "size": asset.get("size") or 0, "sha256": digest[7:].lower() if digest.startswith("sha256:") else "",
            "notes": (rel.get("body") or "")[:2000], "page": rel.get("html_url")}


def check(force: bool = False) -> None:
    """Hỏi GitHub (tối đa 1 lần / 6 giờ trừ khi force). Lỗi mạng -> giữ kết quả cũ."""
    if not force and time.time() - _checked["at"] < CHECK_EVERY:
        return
    errs = []
    for app in APPS:
        try:
            info = _fetch_latest(app)
            with _lock:
                if info:
                    _latest[app] = info
        except Exception as e:
            errs.append(f"{APPS[app]['name']}: {e}")
    _checked["at"] = time.time()
    _checked["error"] = "; ".join(errs) or None


def start_background_checks() -> None:
    if _checked.get("loop"):
        return
    _checked["loop"] = True

    def loop():
        while True:
            try:
                check()
            except Exception:
                pass
            time.sleep(600)
    threading.Thread(target=loop, daemon=True, name="dytool-updates").start()


def status() -> list[dict]:
    rows = []
    for app, a in APPS.items():
        cur, new = installed(app), _latest.get(app)
        j = _job.get(app)
        if app == "dytool" and j and j["state"] == "installing" and time.time() - j["at"] > 15 * 60:
            # trình cài đáng lẽ đã đóng app này rồi mở lại bản mới; vẫn còn đây sau 15 phút = trình cài bị huỷ / lỗi
            _set(app, "error", "trình cài không chạy xong (bị huỷ hoặc lỗi) — xem data/updates/dytool-update.log "
                               "rồi bấm Cập nhật lại")
        rows.append({"app": app, "name": a["name"], "installed": cur, "latest": new and new["version"],
                     "page": new and new["page"], "size_mb": new and round(new["size"] / 1048576, 1),
                     # không đặt tên khoá "update": Jinja đọc r.update thành hàm dict.update (luôn đúng)
                     "has_update": bool(cur and new and _ver(new["version"]) > _ver(cur)),
                     "missing": cur is None, "job": _job.get(app)})
    return rows


def pending() -> int:
    return sum(1 for r in status() if r["has_update"])


def check_soon() -> None:
    """Chưa kiểm tra lần nào (chạy giao diện không kèm --autostart) -> kiểm tra ở thread nền, không chặn trang."""
    if not _checked["at"] and not _checked.get("running"):
        _checked["running"] = True

        def run():
            try:
                check(force=True)
            finally:
                _checked["running"] = False
        threading.Thread(target=run, daemon=True).start()


def checked_at() -> float:
    return _checked["at"]


def check_error() -> str | None:
    return _checked["error"]


# ---------------- cập nhật ----------------
def _set(app: str, state: str, msg: str = "", pct: int | None = None) -> None:
    _job[app] = {"state": state, "msg": msg, "pct": pct, "at": time.time()}


def _download(app: str, info: dict) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", info["sha256"]):
        raise RuntimeError("bản phát hành không công bố mã SHA256 cho file cài — không cài để đảm bảo an toàn")
    folder = Path(ROOT / "data" / "updates")
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / info["file"]
    h = hashlib.sha256()
    with httpx.stream("GET", info["url"], follow_redirects=True, timeout=60,
                      headers={"User-Agent": "dytool-updater"}) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or info["size"] or 0)
        got = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if total:
                    _set(app, "downloading", f"Đang tải {got // 1048576}/{total // 1048576} MB", got * 100 // total)
    if h.hexdigest() != info["sha256"]:
        dest.unlink(missing_ok=True)
        raise RuntimeError("file tải về không khớp mã SHA256 GitHub công bố — đã xoá, không cài")
    return dest


def _procs(name: str) -> list[int]:
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    return [int(m) for m in re.findall(r'^"[^"]+","(\d+)"', out, re.M)]


def _kill(name: str) -> bool:
    if not _procs(name):
        return False
    subprocess.run(["taskkill", "/IM", name, "/T", "/F"], capture_output=True)
    for _ in range(20):
        if not _procs(name):
            break
        time.sleep(0.5)
    return True


def busy_reason(app: str) -> str | None:
    """Lý do chưa nên cập nhật ngay lúc này (None = được)."""
    from . import progress
    task = progress.STATE.get("task")
    if app == "voicestudio" and task and task.get("kind") == "edit":
        return "đang edit / lồng tiếng 1 video — đợi xong rồi cập nhật"
    if app == "dytool" and task and task.get("kind") == "upload":
        return "đang đăng 1 video — cập nhật lúc này (đóng app) có thể làm bài bị đăng dở / đăng trùng, đợi xong"
    return None


def _install_voicestudio(exe: Path) -> None:
    was_open = _kill("VoiceStudio.exe")
    _set("voicestudio", "installing", "Đang cài VoiceStudio…")
    rc = subprocess.run([str(exe), "/S"]).returncode
    if rc not in (0, 3010):
        raise RuntimeError(f"trình cài VoiceStudio báo lỗi (mã {rc})")
    if was_open:
        import os
        for base in (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs", Path(os.environ.get("ProgramFiles", ""))):
            vs = base / "VoiceStudio" / "VoiceStudio.exe"
            if vs.exists():
                subprocess.Popen([str(vs)], cwd=str(vs.parent))
                break


def _install_dytool(exe: Path) -> None:
    _set("dytool", "installing", "Đang mở trình cài — dytool sẽ tự đóng rồi mở lại khi cài xong")
    # Trình cài đóng dytool (PrepareToInstall, `taskkill /T` = cả cây tiến trình con) rồi mở lại khi xong (/RELAUNCH).
    # Mở qua `cmd /c start`: cmd thoát ngay -> trình cài KHÔNG còn là con của app, không bị đóng theo cây của app
    # (trước: trình cài tự đóng chính nó, app biến mất mà không cập nhật)
    args = [str(exe), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCH",
            f"/LOG={ROOT / 'data' / 'updates' / 'dytool-update.log'}"]
    if sys.platform == "win32":
        subprocess.Popen(["cmd.exe", "/c", "start", "", *args],
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                         close_fds=True)
    else:
        subprocess.Popen(args, close_fds=True)


def start(app: str) -> str | None:
    """Bắt đầu cập nhật ở thread nền. Trả về lý do từ chối (hoặc None nếu đã bắt đầu)."""
    if app not in APPS:
        return "không rõ app"
    info = _latest.get(app)
    if not info:
        return "chưa kiểm tra được bản mới — bấm Kiểm tra lại"
    cur = installed(app)
    if not cur:
        return f"{APPS[app]['name']} chưa cài trên máy này — cài bằng trình cài dytool hoặc trang phát hành chính thức"
    if _ver(info["version"]) <= _ver(cur):
        return f"đang dùng bản mới nhất ({cur})"
    why = busy_reason(app)
    if why:
        return why
    with _lock:      # nhận việc nguyên tử: 2 lần bấm sát nhau không chạy 2 lượt tải / 2 trình cài
        j = _job.get(app)
        if j and j["state"] in ("downloading", "installing"):
            return "đang cập nhật rồi"
        _set(app, "downloading", "Đang tải…", 0)

    def run():
        try:
            exe = _download(app, info)
            {"voicestudio": _install_voicestudio, "dytool": _install_dytool}[app](exe)
            if app != "dytool":
                _inst_cache.pop(app, None)       # đọc lại bản đang cài ngay (không đợi hết 60 giây)
                _set(app, "done", f"Đã cập nhật lên {info['version']}")
        except Exception as e:
            _set(app, "error", str(e)[:300])
    threading.Thread(target=run, daemon=True, name=f"update-{app}").start()
    return None
