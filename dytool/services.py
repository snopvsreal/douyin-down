"""Bật / kiểm tra / tắt các dịch vụ phụ: backend tải Douyin, VoiceStudio chạy ngầm; tắt hết khi thoát app."""

import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


# Dịch vụ chạy trên chính máy này (backend tải 5555, VoiceStudio 3900): trên Windows cổng đang đóng KHÔNG bị từ
# chối ngay mà chờ hết thời gian kết nối (đo: 2 s / lần) -> thanh trạng thái mất 4 s mỗi lần làm mới. Dịch vụ đang
# chạy luôn nhận kết nối trong vài ms -> chờ kết nối 0.3 s là đủ; vẫn cho 3 s để trả lời (VoiceStudio đang bận).
LOCAL_TIMEOUT = httpx.Timeout(3.0, connect=0.3)


def tdl_alive(cfg: dict) -> bool:
    try:
        return httpx.get(cfg["douyin"]["api"].rstrip("/") + "/docs", timeout=LOCAL_TIMEOUT).status_code == 200
    except httpx.HTTPError:
        return False


_proc: subprocess.Popen | None = None      # backend tải đã bật lần gần nhất
_tdl_lock = threading.Lock()


def ensure_tdl(cfg: dict, config_path: str = "config.yaml", wait: float = 90) -> bool:
    """Backend tải chưa chạy thì bật lên (tiến trình tách riêng: không tắt theo giao diện) và chờ sẵn sàng."""
    global _proc
    if tdl_alive(cfg):
        return True
    data = Path(cfg["_data"])
    with _tdl_lock:            # bật sẵn lúc bắt đầu chạy tự động + worker cần tải cùng lúc -> chỉ bật 1 backend
        if _proc is None or _proc.poll() is not None:    # lần trước còn đang khởi động -> chờ tiếp, không bật thêm
            detach = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
                      if sys.platform == "win32" else {"start_new_session": True})
            with open(data / "tdl_server.log", "a") as log:     # tiến trình con giữ handle riêng; đóng bản của mình
                _proc = subprocess.Popen([sys.executable, "-m", "dytool.cli", "-c", str(ROOT / config_path), "server"],
                                         cwd=ROOT, stdout=log, stderr=log, stdin=subprocess.DEVNULL, **detach)
    deadline = time.time() + wait
    while time.time() < deadline:
        if tdl_alive(cfg):
            time.sleep(10)          # backend còn cập nhật tham số Douyin vài giây sau khi mở cổng
            return True
        time.sleep(2)
    return False


# ---------------- Máy chủ giọng nói (OmniVoice) chạy ngầm ----------------
# dytool tự chạy model OmniVoice trong tiến trình riêng (voice_server.py, cổng 3910) — KHÔNG cần cài app VoiceStudio.
# Giữ API giống backend VoiceStudio nên vs_api.py / dub.py gọi như cũ. Cổng khác 3900 để không đụng app VoiceStudio
# nếu máy vẫn cài. config.yaml `edit.dub.api` trỏ máy khác / cổng khác thì dùng địa chỉ đó (không tự bật).
VOICE_PORT = 3910
_OLD_DEFAULT = "http://127.0.0.1:3900"
VS_RESTART_EXIT = 75          # = voice_server.RESTART_EXIT (không import: kéo theo FastAPI / torch)
VOICE_DEVICES = {"auto": "Tự động", "gpu": "Card đồ hoạ (GPU)", "cpu": "CPU"}


def voice_device() -> str:
    """Thiết bị đọc giọng nói người dùng chọn (Cài đặt › Dịch & lồng tiếng): auto = GPU, card lỗi thì tự chuyển CPU;
    gpu = luôn GPU (lỗi thì báo lỗi); cpu = luôn CPU (cả Demucs tách giọng). Lưu data/voice/device.json — máy chủ
    giọng nói đọc lúc bật."""
    import json
    try:
        m = json.loads((ROOT / "data" / "voice" / "device.json").read_text("utf-8-sig")).get("mode")
    except (OSError, ValueError, AttributeError):
        m = None
    return m if m in VOICE_DEVICES else "auto"


def set_voice_device(mode: str) -> None:
    import json
    import os
    if mode not in VOICE_DEVICES:
        raise ValueError(mode)
    p = ROOT / "data" / "voice" / "device.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"mode": mode}), "utf-8")
    os.replace(tmp, p)


def restart_voicestudio(cfg: dict) -> bool:
    """Tắt rồi bật lại máy chủ giọng nói do dytool bật (đổi thiết bị đọc). False = máy chủ không do dytool bật."""
    if not stop_voicestudio() and vs_state(cfg) != "off":
        return False
    start_voicestudio(cfg)
    return True


def vs_api(cfg: dict) -> str:
    """Địa chỉ máy chủ giọng nói: cấu hình riêng (khác mặc định cũ của VoiceStudio) hoặc máy chủ của dytool."""
    dub = cfg.get("edit", {}).get("dub", cfg if "api" in cfg and "edit" not in cfg else {})
    api = str(dub.get("api") or "").strip().rstrip("/")
    return api if api and api != _OLD_DEFAULT else f"http://127.0.0.1:{VOICE_PORT}"


def vs_state(cfg: dict) -> str:
    """'ok' (máy chủ đã mở cổng — model có thể còn đang nạp, xem /model/status) · 'starting' · 'off'."""
    try:
        code = httpx.get(vs_api(cfg) + "/health", timeout=LOCAL_TIMEOUT).status_code
    except httpx.HTTPError:
        return "starting" if vs_ours_running() else "off"
    return "ok" if code < 500 else "starting"


def vs_model_ready() -> bool:
    from . import voice_model
    return voice_model.ready()


_vs_proc: subprocess.Popen | None = None     # máy chủ giọng nói do dytool bật
_vs_used = 0.0                               # lần cuối cần giọng nói (để tự tắt khi rảnh lâu)
_vs_started = 0.0                            # lúc bật gần nhất
_vs_retry_at = 0.0                           # vừa bật đã chết -> chờ tới lúc này mới bật lại


def vs_ours_running() -> bool:
    return _vs_proc is not None and _vs_proc.poll() is None


def start_voicestudio(cfg: dict) -> str:
    """Bật máy chủ giọng nói chạy ngầm, KHÔNG chờ (mở cổng vài giây, nạp model ~15–30 s). Trả 'ok' / 'starting' /
    'missing' (chưa có model — đã bắt đầu tải ở nền) / 'remote' (api đặt ở máy khác) / 'crashed' (vừa bật đã chết —
    xem data/voicestudio.log; 10 phút sau mới thử lại)."""
    global _vs_proc, _vs_started, _vs_retry_at
    import os
    from urllib.parse import urlparse
    vs_touch()
    state = vs_state(cfg)
    if state != "off":
        return state
    u = urlparse(vs_api(cfg))
    if u.hostname not in ("127.0.0.1", "localhost"):
        return "remote"
    if not vs_model_ready():
        from . import voice_model
        voice_model.ensure_background()       # 3,3 GB — tải xong lần sau bật được
        return "missing"
    if _vs_proc is not None and _vs_proc.poll() is not None:
        # thoát mã VS_RESTART_EXIT = máy chủ tự xin khởi động lại (card đồ hoạ lỗi -> lần sau chạy CPU): bật lại ngay
        if _vs_proc.returncode != VS_RESTART_EXIT and time.time() - _vs_started < 300:
            _vs_retry_at = time.time() + 600
        _vs_proc = None
    if time.time() < _vs_retry_at:
        return "crashed"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH")}
    env.update(PYTHONUNBUFFERED="1", PYTHONUTF8="1", TORCHDYNAMO_DISABLE="1", HF_HUB_OFFLINE="1",
               HF_HUB_DISABLE_TELEMETRY="1")
    log_path = ROOT / "data" / "voicestudio.log"
    log_path.parent.mkdir(exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as log:   # tiến trình con giữ handle riêng
        log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} dytool bật máy chủ giọng nói (OmniVoice) =====\n")
        log.flush()
        # python (không phải pythonw) vẫn không hiện cửa sổ: __init__ đặt CREATE_NO_WINDOW cho mọi Popen.
        # stdin=PIPE là dây sống: dytool thoát (kể cả bị tắt ngang) -> ống đóng -> máy chủ tự thoát, trả VRAM
        exe = Path(sys.executable)
        if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
            exe = exe.parent / "python.exe"
        _vs_proc = subprocess.Popen([str(exe), "-m", "dytool.voice_server", "--port", str(u.port or VOICE_PORT)],
                                    cwd=str(ROOT), env=env, stdout=log, stderr=log, stdin=subprocess.PIPE)
    _vs_started = time.time()
    return "starting"


def vs_touch() -> None:
    global _vs_used
    _vs_used = time.time()


def stop_voicestudio() -> bool:
    """Tắt máy chủ giọng nói do dytool bật (đóng dây sống stdin; không thoát thì tắt cả cây)."""
    global _vs_proc
    p = _vs_proc
    if p is None or p.poll() is not None:
        return False
    try:
        p.stdin.close()
        p.wait(20)
    except Exception:
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, timeout=15)
        except Exception:
            p.kill()
    _vs_proc = None
    return True


def vs_idle_stop(cfg: dict) -> bool:
    """Rảnh lâu (mặc định 20 phút, đặt ở trang Giọng nói; 0 = không tắt) -> tắt máy chủ giọng nói để trả VRAM / RAM."""
    minutes = vs_idle_minutes(cfg)
    if minutes <= 0 or not vs_ours_running() or time.time() - _vs_used < minutes * 60:
        return False
    return stop_voicestudio()


# ---------------- Trang VoiceStudio trong dytool ----------------

VS_SETTINGS = ROOT / "data" / "voicestudio.json"
IDLE_CHOICES = (0, 10, 20, 30, 60, 120)


def vs_idle_minutes(cfg: dict) -> float:
    """Phút rảnh trước khi tự tắt backend ngầm: đặt ở trang VoiceStudio (data/voicestudio.json) > config.yaml
    edit.dub.idle_stop_minutes > 20. 0 = không tự tắt."""
    import json
    try:
        v = json.loads(VS_SETTINGS.read_text("utf-8-sig")).get("idle_stop_minutes")
        if isinstance(v, (int, float)) and v >= 0:
            return float(v)
    except (OSError, ValueError, AttributeError):
        pass
    try:
        return float(cfg.get("edit", {}).get("dub", {}).get("idle_stop_minutes", 20) or 0)
    except (TypeError, ValueError):
        return 20.0


def vs_set_idle_minutes(minutes: int) -> None:
    import json
    if minutes not in IDLE_CHOICES:
        raise ValueError("giá trị không hợp lệ")
    try:
        d = json.loads(VS_SETTINGS.read_text("utf-8-sig"))
        d = d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        d = {}
    d["idle_stop_minutes"] = minutes
    VS_SETTINGS.parent.mkdir(exist_ok=True)
    VS_SETTINGS.write_text(json.dumps(d, ensure_ascii=False, indent=2), "utf-8")


_vs_info: dict = {"at": 0.0, "v": {}, "busy": False}


def vs_info(cfg: dict) -> dict:
    """Trạng thái chi tiết cho trang VoiceStudio (poll 3 s): trả kết quả gần nhất NGAY, hỏi lại ở luồng nền — backend
    đang nạp model trả lời chậm, trang tự làm mới không được đứng chờ (bài học "app đơ")."""
    import threading
    if time.time() - _vs_info["at"] > 2 and not _vs_info["busy"]:
        _vs_info["busy"] = True

        def run():
            try:
                _vs_info["v"] = _vs_probe(cfg)
                _vs_info["at"] = time.time()
            finally:
                _vs_info["busy"] = False
        t = threading.Thread(target=run, daemon=True, name="vs-info")
        t.start()
        if not _vs_info["v"]:
            t.join(4)               # lần đầu mở trang: chờ chút để có dữ liệu thật
    return _vs_info["v"] or {"state": "off"}


def _vs_probe(cfg: dict) -> dict:
    api = vs_api(cfg)
    from . import voice_model
    dl = voice_model.state()
    out = {"state": vs_state(cfg), "api": api, "ours": vs_ours_running(), "idle": vs_idle_minutes(cfg),
           "idle_left": None, "download": dl}
    if out["state"] == "off" and dl.get("phase") != "ready":
        out["state"] = "missing"              # chưa có model: đang tải / chưa tải / tải lỗi (xem download)
    if out["ours"] and out["idle"]:
        out["idle_left"] = max(0, int(out["idle"] * 60 - (time.time() - _vs_used)))
    t = httpx.Timeout(4.0, connect=0.3)
    try:
        out["health"] = httpx.get(api + "/health", timeout=t).json()
    except (httpx.HTTPError, ValueError):
        out["health"] = None
    if out["state"] == "ok":
        for key, path in (("model", "/model/status"), ("sys", "/sysinfo"),
                          ("offload", "/api/settings/perf/offload-after-generation")):
            try:
                out[key] = httpx.get(api + path, timeout=t).json()
            except (httpx.HTTPError, ValueError):
                out[key] = None
    elif out["state"] == "starting":
        try:
            out["boot"] = httpx.get(api + "/startup/progress", timeout=t).json()
        except (httpx.HTTPError, ValueError):
            out["boot"] = None
    return out


def vs_log_tail(n: int = 120) -> str:
    """Cuối data/voicestudio.log, bỏ dòng tiến độ tqdm (CR) chỉ giữ trạng thái cuối của mỗi dòng."""
    p = ROOT / "data" / "voicestudio.log"
    try:
        with open(p, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 64_000))
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return ""
    lines = [ln.split(chr(13))[-1] for ln in text.splitlines()]
    return chr(10).join(lines[-n:])


# ---------------- Thoát app: tắt hết dịch vụ đi kèm ----------------

def _children_tree() -> dict[int, list[tuple[int, str]]]:
    """ppid -> [(pid, tên exe)] của mọi tiến trình đang chạy (Toolhelp32, không cần psutil)."""
    import ctypes
    from ctypes import wintypes

    class PE(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)       # TH32CS_SNAPPROCESS
    tree: dict[int, list[tuple[int, str]]] = {}
    if not snap or snap == wintypes.HANDLE(-1).value:
        return tree
    try:
        e = PE()
        e.dwSize = ctypes.sizeof(PE)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            tree.setdefault(e.th32ParentProcessID, []).append((e.th32ProcessID, e.szExeFile))
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return tree


# Tiến trình con là app người dùng tự mở (không phải dịch vụ chạy ngầm) -> để nguyên khi thoát dytool
_KEEP_ON_EXIT = {"voicestudio.exe"}


def kill_children() -> int:
    """Tắt mọi tiến trình con (cả cây) mà dytool đã mở, trừ app người dùng tự mở (VoiceStudio). Trả số cây đã tắt.
    Gồm backend tải Douyin (bật kiểu tách riêng nhưng vẫn ghi cha là dytool), cửa sổ đăng nhập, trình duyệt,
    ffmpeg / Demucs còn sót, WebView2 của cửa sổ đã đóng."""
    import os
    if sys.platform != "win32":
        return 0
    n = 0
    for pid, exe in _children_tree().get(os.getpid(), []):
        if exe.lower() in _KEEP_ON_EXIT:
            continue
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=15)
            n += 1
        except Exception:
            pass
    return n


def _exe_path(pid: int) -> str:
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    h = k32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        return buf.value if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) else ""
    finally:
        k32.CloseHandle(h)


def kill_port_owner(port: int) -> bool:
    """Tắt tiến trình đang nghe cổng (cả cây) — chỉ khi file chạy nằm trong thư mục dytool (không đụng app khác).
    Cho backend tải Douyin bật từ lần mở app trước (lúc đó bật tách riêng -> không còn là con của dytool này)."""
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return False
    import os
    root = str(ROOT).lower()
    parent = {pid: ppid for ppid, kids in _children_tree().items() for pid, _ in kids}
    mine, cur = set(), os.getpid()          # chính dytool + các đời cha: không bao giờ tắt
    while cur and cur not in mine:
        mine.add(cur)
        cur = parent.get(cur, 0)
    done = False
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
            pid = int(parts[4])
            # python của .venv chỉ là trình khởi chạy: tiến trình nghe cổng chạy python chung (uv-python) -> lấy đời
            # GẦN NHẤT (nó hoặc cha / ông) nằm trong thư mục dytool rồi tắt cả cây đó. Chỉ gần nhất: đi xa hơn có thể
            # gặp chính dytool (C:\dytool\.venv\Scripts\pythonw.exe) -> tự tắt mình
            top, cur = 0, pid
            for _ in range(3):
                if not cur or cur in mine:
                    break
                if _exe_path(cur).lower().startswith(root):
                    top = cur
                    break
                cur = parent.get(cur, 0)
            if top:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(top)], capture_output=True, timeout=15)
                done = True
    return done


def shutdown(worker=None, tdl_port: int = 5555) -> None:
    """Thoát app (Thoát ở khay): tắt hết dịch vụ đi kèm để không còn gì chạy ngầm.
    1) dừng worker như nút Dừng (video về trạng thái cũ, không thành Lỗi); đang bấm đăng thì chờ xong
    2) đóng trình duyệt mở từ trang Trình duyệt (đóng qua CDP để lưu cookie)
    3) tắt VoiceStudio chạy ngầm (đóng dây sống stdin)
    4) tắt cả cây mọi tiến trình con còn lại (backend tải Douyin, cửa sổ đăng nhập, ffmpeg…)."""
    from . import progress
    try:
        if worker is not None:
            if worker.running:
                worker.stop()
            late = progress.cancel() == "late"
            t = getattr(worker, "_thread", None)
            if t is not None and t.is_alive():
                t.join(120 if late else 20)      # đăng dở: chờ bấm đăng xong (tắt giữa chừng = không biết đã lên chưa)
    except Exception:
        pass
    try:
        from . import browsers
        for pid in list(getattr(browsers, "_opened", {})):
            try:
                browsers.close_window(pid)
            except Exception:
                pass
    except Exception:
        pass
    try:
        stop_voicestudio()
    except Exception:
        pass
    try:
        kill_children()
        kill_port_owner(tdl_port)
    except Exception:
        pass
