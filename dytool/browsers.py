"""Trình duyệt của dytool = nhân ShardX của ShardBrowser, chạy NHÚNG qua SDK Python chính thức `shardx`
(github.com/ProxyShard/ShardBrowser/tree/main/sdks/python, MIT). Không cần cài / mở app ShardBrowser nữa.

- Nhân trình duyệt + thư viện dấu vân tay: SDK tự tải từ CDN của ShardBrowser về data/shardx (lần đầu ~200 MB, sau
  đó tự cập nhật). Đặt ở C:\\dytool\\data chứ không phải AppData: máy người dùng chặn chạy exe trong AppData.
- Mỗi tài khoản Facebook / TikTok (và đăng nhập Douyin) có 1 hồ sơ riêng: data/shardx/profiles/<id>/ (dấu vân tay
  cố định + cookie). dytool chỉ gọi SDK (create_profile / launch), không tự viết logic giả dấu vân tay.
- Mở hồ sơ: SDK chạy nhân với cổng CDP -> Playwright connect_over_cdp. Hồ sơ đang mở sẵn (cửa sổ đăng nhập chưa
  đóng) -> nối vào phiên đó, không mở lần 2.

Hồ sơ của tài khoản: data/accounts/<id>/browser.json   {"engine": "sdk", "profile_id": "...", "profile_name": "..."}
Hồ sơ cho đăng nhập Douyin: data/browser.json
(browser.json kiểu cũ — hồ sơ của app ShardBrowser, không có "engine" — coi như chưa có: bấm Đăng nhập tạo hồ sơ mới.)
"""

import contextlib
import json
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILE = "browser.json"
ENGINE = "sdk"
SDK_DIR = ROOT / "data" / "shardx"
PROFILES_DIR = SDK_DIR / "profiles"
META = "dytool.json"          # tên hồ sơ do dytool đặt, nằm cạnh profile.json của SDK


class BrowserError(RuntimeError):
    pass


_sdk = None
_sdk_lock = threading.Lock()


_stdout_fixed = False


@contextlib.contextmanager
def _quiet():
    """SDK in tiến trình ra stdout (có ký tự '→'): stdout ghi file log theo bảng mã cp1252 -> UnicodeEncodeError.
    Chuyển stdout / stderr sang UTF-8 (ký tự lạ thì thay thế) 1 lần. KHÔNG dùng contextlib.redirect_stdout: nó đổi
    sys.stdout của CẢ tiến trình (không theo luồng) -> luồng tải nhân chạy nền nuốt mất log của luồng khác, 2 lần gọi
    chồng nhau có thể để stdout kẹt ở bộ nhớ. pythonw: stdout = None -> print không làm gì, không lỗi."""
    global _stdout_fixed
    if not _stdout_fixed:
        import sys
        for s in (sys.stdout, sys.stderr):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        _stdout_fixed = True
    yield


def sdk():
    global _sdk
    with _sdk_lock:
        if _sdk is None:
            try:
                from shardx import ShardX
            except ImportError as e:
                raise BrowserError(f"Thiếu thư viện shardx — bấm Cài lại thư viện ở trang Cài đặt ({e})") from e
            _sdk = ShardX(cache_dir=str(SDK_DIR), profiles_dir=str(PROFILES_DIR))
            _use_mirror(_sdk.runtime)
        return _sdk


# ---------------- nguồn tải nhân: bản sao trên GitHub của mình ----------------
# SDK mặc định tải runtime.json từ GitHub ProxyShard + file zip từ kho R2 của họ, không kiểm tra mã băm. dytool thay
# 3 chỗ tải (_fetch_manifest / _download / _install_fingerprints) để lấy từ bản phát hành tag `shardx-…` của
# MIRROR_REPO (GitHub Actions trong kho đó tự tạo khi ShardBrowser ra nhân mới), BẮT BUỘC khớp SHA256 GitHub công bố (asset.digest). Phần giải nén,
# thay nhân an toàn, ghi phiên bản vẫn là của SDK. runtime.json chép nguyên -> nhân đã có trên máy không tải lại.
MIRROR_REPO = "snopvsreal/shardx-engine"
TAG_PREFIX = "shardx-"


def _mirror_assets() -> dict:
    """{tên file: asset} của bản phát hành `shardx-…` mới nhất trong MIRROR_REPO."""
    import httpx
    r = httpx.get(f"https://api.github.com/repos/{MIRROR_REPO}/releases?per_page=30", follow_redirects=True,
                  headers={"Accept": "application/vnd.github+json"}, timeout=httpx.Timeout(20, connect=10))
    r.raise_for_status()
    rels = [x for x in r.json() if str(x.get("tag_name", "")).startswith(TAG_PREFIX) and not x.get("draft")]
    if not rels:
        raise BrowserError(f"Chưa có bản sao nhân trình duyệt nào trên github.com/{MIRROR_REPO}")
    rel = max(rels, key=lambda x: x.get("published_at") or "")
    return {a["name"]: a for a in rel.get("assets", [])}


def _fetch_verified(asset: dict, dest: Path) -> Path:
    """Tải 1 file của bản phát hành, so SHA256 với asset.digest. Không có mã / không khớp -> xoá, báo lỗi."""
    import hashlib
    import os
    import httpx
    digest = str(asset.get("digest") or "")
    if not digest.startswith("sha256:"):
        raise BrowserError(f"{asset.get('name')}: GitHub không công bố mã SHA256 — không dùng")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    try:
        with httpx.stream("GET", asset["browser_download_url"], follow_redirects=True,
                          timeout=httpx.Timeout(60, connect=15)) as r:
            r.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    f.write(chunk)
                    h.update(chunk)
        if h.hexdigest() != digest[7:].lower():
            raise BrowserError(f"{asset.get('name')}: mã SHA256 không khớp — file bị hỏng hoặc bị thay, không dùng")
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return dest


def _use_mirror(rt) -> None:
    import shutil
    import zipfile
    state: dict = {}

    def assets() -> dict:
        if "assets" not in state:
            state["assets"] = _mirror_assets()
        return state["assets"]

    def fetch_manifest() -> dict:
        # lỗi (mất mạng, GitHub giới hạn lượt hỏi) -> {} như SDK: giữ nhân đang có, không tải lại
        try:
            a = assets().get("runtime.json")
            if not a:
                return {}
            p = _fetch_verified(a, rt.root / ".mirror-runtime.json")
            state["manifest"] = json.loads(p.read_text("utf-8"))
            p.unlink(missing_ok=True)
            return state["manifest"]
        except Exception as e:
            print(f"Không đọc được danh sách phiên bản nhân trên GitHub: {e}", flush=True)
            return {}

    def download(arch, dest: Path, unpacks: bool = False):
        a = assets().get(arch.key)
        if not a:
            raise BrowserError(f"Bản sao trên github.com/{MIRROR_REPO} thiếu {arch.key}")
        tmp = _fetch_verified(a, dest / f".{arch.key}.tmp")
        etag = ((state.get("manifest") or {}).get("archives") or {}).get(arch.key, "")
        return tmp, etag

    def install_fingerprints() -> None:
        # như Runtime._install_fingerprints của SDK, chỉ khác nguồn tải
        from shardx.runtime import FINGERPRINTS_ARCHIVE, FINGERPRINTS_TOP_DIR, RETIRED_TEMPLATES
        a = assets().get(FINGERPRINTS_ARCHIVE.key)
        if not a:
            raise BrowserError(f"Bản sao trên github.com/{MIRROR_REPO} thiếu {FINGERPRINTS_ARCHIVE.key}")
        staging = rt.fingerprints_dir / ".staging"
        shutil.rmtree(staging, ignore_errors=True)
        tmp = _fetch_verified(a, staging / "bundle.zip")
        with zipfile.ZipFile(tmp) as z:
            z.extractall(staging)
        src = staging / FINGERPRINTS_TOP_DIR
        for p in (src if src.exists() else staging).iterdir():
            if p.suffix == ".json":
                shutil.copy(p, rt.fingerprints_dir / p.name)
        for old in RETIRED_TEMPLATES:
            (rt.fingerprints_dir / f"{old}.json").unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)

    rt._fetch_manifest = fetch_manifest
    rt._download = download
    rt._install_fingerprints = install_fingerprints


def engine_installed() -> bool:
    return any(SDK_DIR.glob("ShardX-*/chrome.exe")) or any(SDK_DIR.glob("ShardX-*/*/Contents/MacOS/*"))


def install_engine() -> None:
    """Tải / cập nhật nhân trình duyệt + thư viện dấu vân tay (không làm gì nếu đã mới nhất)."""
    s = sdk()
    try:
        with _quiet():
            s.runtime.install()
    except Exception as e:
        raise BrowserError(f"Không tải được nhân trình duyệt ShardX: {e}") from e


_install_thread: threading.Thread | None = None     # luồng tải nhân lúc mở app (màn hình khởi động chờ luồng này)


def install_engine_background() -> threading.Thread:
    """Gọi khi mở app: tải sẵn nhân lần đầu để bấm Đăng nhập không phải chờ ~1 phút. Đang tải rồi thì trả luồng cũ."""
    global _install_thread
    if _install_thread is not None and _install_thread.is_alive():
        return _install_thread
    def run():
        try:
            install_engine()
        except BrowserError as e:
            print(e, flush=True)
            return
        try:                          # đọc sẵn 220 mẫu dấu vân tay -> lần đầu mở trang chi tiết hồ sơ không chờ ~0.5 s
            from . import browser_mgr
            browser_mgr.templates()
        except Exception:
            pass
    _install_thread = threading.Thread(target=run, daemon=True, name="shardx-install")
    _install_thread.start()
    return _install_thread


def wait_install(timeout: float = 900) -> None:
    """Đang tải / cập nhật nhân ở nền (màn hình khởi động không còn chờ bước này) -> chờ xong rồi mới mở hồ sơ,
    không mở nhân đang chép dở."""
    t = _install_thread
    if t is not None and t.is_alive() and t is not threading.current_thread():
        t.join(timeout)


# ---------------- hồ sơ ----------------

def get_choice(acc_dir: Path) -> dict:
    """Hồ sơ đã gắn: {'profile_id', 'profile_name'} hoặc {} nếu chưa có (kể cả hồ sơ cũ của app ShardBrowser)."""
    try:
        c = json.loads((acc_dir / FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if c.get("engine") != ENGINE or not _valid_id(c.get("profile_id")) or not (PROFILES_DIR / c["profile_id"]).is_dir():
        return {}
    return c


def _valid_id(pid) -> bool:
    """Mã hồ sơ SDK = uuid4().hex. Kiểm tra chặt trước khi ghép vào đường dẫn."""
    import re
    return isinstance(pid, str) and re.fullmatch(r"[0-9a-f]{32}", pid) is not None


def set_choice(acc_dir: Path, profile_id: str, profile_name: str = "") -> None:
    if not profile_id:
        (acc_dir / FILE).unlink(missing_ok=True)
        return
    if not _valid_id(profile_id):
        raise BrowserError("Mã hồ sơ trình duyệt không hợp lệ")
    (acc_dir / FILE).write_text(json.dumps({"engine": ENGINE, "profile_id": profile_id, "profile_name": profile_name},
                                           ensure_ascii=False), "utf-8")


def missing_profile(acc_dir: Path) -> bool:
    return not get_choice(acc_dir)


def had_old_profile(acc_dir: Path) -> bool:
    """Đang gắn hồ sơ của app ShardBrowser cũ (trước khi nhúng SDK) -> cần đăng nhập lại 1 lần."""
    try:
        c = json.loads((acc_dir / FILE).read_text("utf-8"))
        return bool(c.get("profile_id")) and c.get("engine") != ENGINE
    except (OSError, ValueError):
        return False


def list_profiles() -> list[dict]:
    """Hồ sơ dytool đã tạo: [{id, name, running}] — để đổi hồ sơ trên giao diện. Chỉ đọc file, không chạm mạng."""
    out = []
    if not PROFILES_DIR.is_dir():
        return out
    for d in PROFILES_DIR.iterdir():
        if not _valid_id(d.name) or not (d / "profile.json").exists():
            continue
        try:
            name = json.loads((d / META).read_text("utf-8")).get("name") or d.name
        except (OSError, ValueError):
            name = d.name
        out.append({"id": d.name, "name": name, "running": _running_cdp(d) is not None})
    return sorted(out, key=lambda p: p["name"].lower())


# hệ điều hành của mẫu dấu vân tay (thư viện 220 mẫu máy thật của ShardX) — lọc theo navigator.platform
PLATFORMS = (("Windows", "Windows"), ("macOS", "macOS"), ("Linux", "Linux"), ("Android", "Android (điện thoại)"))


def create_profile(name: str, english: bool = False, platform: str = "Windows", template: str = "",
                   refresh_rate: int | None = None) -> dict:
    """Hồ sơ mới y như nút "New profile" của ShardBrowser (SDK create_profile: mẫu dấu vân tay Windows thật + phần
    cứng ngẫu nhiên phù hợp máy). Múi giờ / ngôn ngữ để "auto" — SDK lấy theo IP mỗi lần mở (máy ở VN -> giờ VN,
    tiếng Việt), không giữ của mẫu (vd. Ba Lan). english=True (TikTok): giao diện tiếng Anh vì bộ chọn của
    tiktok-uploader viết theo tiếng Anh."""
    s = sdk()
    try:
        with _quiet():
            if template:
                p = s.create_profile(template, refresh_rate=refresh_rate)
            else:
                p = s.create_profile(platform=platform if platform in dict(PLATFORMS) else "Windows",
                                     refresh_rate=refresh_rate)
    except Exception as e:
        raise BrowserError(f"Không tạo được hồ sơ trình duyệt: {e}") from e
    cfg = p.config
    cfg["timezone"] = "auto"
    nav = cfg.setdefault("navigator", {})
    if english:
        nav.update(language="en-US", accept_language="en-US,en;q=0.9", languages=["en-US", "en"])
        cfg["icu_locale"] = "en-US"
    else:
        nav["language"] = "auto"
    with _quiet():
        s.save_profile(p)
    write_meta(p.id, name=name, created=int(time.time()))
    return {"profile_id": p.id, "profile_name": name}


def ensure_profile(acc_dir: Path, name: str, english: bool = False) -> tuple[dict, bool]:
    """Hồ sơ đã gắn, hoặc tạo hồ sơ mới tên `name` rồi gắn luôn. Trả (lựa chọn, có_tạo_mới)."""
    c = get_choice(acc_dir)
    if c:
        return c, False
    c = create_profile(name, english=english)
    set_choice(acc_dir, c["profile_id"], c["profile_name"])
    return c, True


# ---------------- cài đặt dytool của hồ sơ (dytool.json) ----------------
# {name, created, notes, tags[], proxy_id, webrtc, extensions[], bookmarks[{title,url}], extra_args, deleted_at}

def read_meta(pid: str) -> dict:
    for base in (PROFILES_DIR, SDK_DIR / "trash"):
        try:
            m = json.loads((base / pid / META).read_text("utf-8"))
            return m if isinstance(m, dict) else {}
        except (OSError, ValueError):
            continue
    return {}


def write_meta(pid: str, **upd) -> dict:
    """Gộp trường mới vào dytool.json (None = xoá trường). Ghi nguyên tử."""
    if not _valid_id(pid):
        raise BrowserError("Mã hồ sơ không hợp lệ")
    d = PROFILES_DIR / pid
    m = read_meta(pid)
    for k, v in upd.items():
        if v is None:
            m.pop(k, None)
        else:
            m[k] = v
    tmp = d / (META + ".tmp")
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(d / META)
    return m


# tham số tự thêm không được đè lên cái SDK / dytool tự đặt (hồ sơ, cổng điều khiển, proxy, tiện ích)
_BLOCKED_ARGS = ("--user-data-dir", "--fingerprint-profile", "--remote-debugging", "--remote-allow-origins",
                 "--proxy-server", "--load-extension", "--disable-extensions-except", "--headless", "--shardx-")


def parse_extra_args(text: str) -> list[str]:
    import shlex
    try:
        args = shlex.split(text or "", posix=True)
    except ValueError as e:
        raise BrowserError(f"Tham số khởi động sai cú pháp: {e}") from e
    for a in args:
        if not a.startswith("--"):
            raise BrowserError(f"Tham số phải bắt đầu bằng -- : {a}")
        if a.lower().startswith(_BLOCKED_ARGS):
            raise BrowserError(f"Tham số {a.split('=')[0]} do dytool tự đặt — không tự thêm được")
    return args


def _proxy_url(meta: dict) -> str | None:
    pid = meta.get("proxy_id")
    if not pid:
        return None
    from . import browser_mgr
    p = browser_mgr.get_proxy(pid)
    if not p:
        raise BrowserError("Proxy gắn với hồ sơ đã bị xoá — chọn proxy khác ở trang Hồ sơ trình duyệt")
    return p["url"]


def _proxy_reachable(url: str) -> None:
    """Proxy chết thì báo rõ ngay (không thì trình duyệt mở lên mà trang nào cũng lỗi, đăng bài lỗi khó hiểu)."""
    import socket
    from urllib.parse import urlsplit
    u = urlsplit(url)
    try:
        with socket.create_connection((u.hostname, u.port), timeout=6):
            return
    except OSError as e:
        raise BrowserError(f"Không kết nối được proxy {u.hostname}:{u.port} ({e}) — kiểm tra proxy ở trang Hồ sơ "
                           "trình duyệt") from e


def _write_bookmarks(udd: Path, items: list[dict]) -> None:
    """Bookmark của hồ sơ = thư mục "dytool" trên thanh dấu trang (ghi lại mỗi lần mở; bookmark người dùng tự thêm
    ngoài thư mục đó giữ nguyên). Chỉ ghi khi hồ sơ đang tắt."""
    import uuid as _uuid
    f = udd / "Default" / "Bookmarks"
    try:
        data = json.loads(f.read_text("utf-8"))
    except (OSError, ValueError):
        data = {}
    roots = data.setdefault("roots", {})
    bar = roots.setdefault("bookmark_bar", {"children": [], "name": "Bookmarks bar", "type": "folder"})
    kids = [k for k in bar.get("children", []) if not (k.get("type") == "folder" and k.get("name") == "dytool")]
    if not items and len(kids) == len(bar.get("children", [])):
        return                                             # không có gì để ghi / xoá
    if items:
        now = str((int(time.time()) + 11644473600) * 1000000)   # giờ kiểu Chrome (micro giây từ 1601)
        kids.insert(0, {"type": "folder", "name": "dytool", "guid": str(_uuid.uuid4()), "date_added": now,
                        "date_modified": now, "children": [
                            {"type": "url", "name": b["title"], "url": b["url"], "guid": str(_uuid.uuid4()),
                             "date_added": now} for b in items]})
    bar["children"] = kids
    # id: đánh lại tuần tự (Chrome đọc file không có checksum vẫn được, tự sửa id trùng)
    n = [0]

    def renum(node):
        n[0] += 1
        node["id"] = str(n[0])
        for ch in node.get("children", []) or []:
            renum(ch)
    for k in ("bookmark_bar", "other", "synced"):
        if k in roots:
            renum(roots[k])
    data.pop("checksum", None)
    data.setdefault("version", 1)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    if items:                                              # hiện thanh dấu trang
        pf = udd / "Default" / "Preferences"
        try:
            prefs = json.loads(pf.read_text("utf-8"))
        except (OSError, ValueError):
            prefs = {}
        prefs.setdefault("bookmark_bar", {})["show_on_all_tabs"] = True
        pf.write_text(json.dumps(prefs, ensure_ascii=False), "utf-8")


SCREEN_MODES = {"profile": "Đúng màn hình của mẫu máy", "cap_to_host": "Theo mẫu, thu nhỏ nếu lớn hơn màn hình thật",
                "use_host": "Màn hình thật của máy này"}


def _launch_kwargs(pid: str) -> dict:
    """Cài đặt riêng của hồ sơ -> tham số sdk.launch: proxy, WebRTC, tiện ích, tham số thêm; ghi bookmark."""
    meta = read_meta(pid)
    kw: dict = {"webrtc": meta.get("webrtc") or "auto"}
    url = _proxy_url(meta)
    if url:
        _proxy_reachable(url)
        kw["proxy"] = url
    args = parse_extra_args(meta.get("extra_args") or "")
    from . import browser_mgr
    exts = [str(browser_mgr.EXT_DIR / e) for e in meta.get("extensions") or []
            if (browser_mgr.EXT_DIR / e / "manifest.json").exists()]
    if exts:
        args.append("--load-extension=" + ",".join(exts))
    if args:
        kw["extra_args"] = args
    # tuỳ chọn khởi động của nhân ShardX (trang hồ sơ › Khởi động)
    if meta.get("screen_mode") in SCREEN_MODES:
        kw["screen_mode"] = meta["screen_mode"]
    if meta.get("quic") in ("on", "off"):
        kw["quic"] = meta["quic"] == "on"
    if meta.get("hide_name"):
        kw["hide_profile_name"] = True
    if meta.get("android_media"):
        kw["android_media"] = True
    if meta.get("webrtc_ip") and kw["webrtc"] != "block":
        kw["webrtc_public_ip"] = meta["webrtc_ip"]
    try:
        _write_bookmarks(PROFILES_DIR / pid, meta.get("bookmarks") or [])
    except OSError:
        pass
    return kw


# ---------------- quản lý hồ sơ (trang Hồ sơ trình duyệt) ----------------

_opened: dict = {}            # hồ sơ mở bằng nút "Mở" trên trang quản lý: id -> BrowserSession (để nút Đóng tắt được)


def profile_dir(pid: str) -> Path:
    if not _valid_id(pid) or not (PROFILES_DIR / pid / "profile.json").exists():
        raise BrowserError("Không có hồ sơ này")
    return PROFILES_DIR / pid


_profile_dir = profile_dir


def is_running(pid: str) -> bool:
    return _valid_id(pid) and _running_cdp(PROFILES_DIR / pid) is not None


def _dir_size(d: Path) -> int:
    import os
    total = 0
    for root, _dirs, files in os.walk(d):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


_sizes: dict = {}             # id -> (lúc tính, MB); tính ở luồng nền
_sizing: set = set()
SIZE_TTL = 300


def profile_size_mb(pid: str) -> float | None:
    """Dung lượng hồ sơ (MB), nhớ 5 phút. Chưa có -> None và tính ở luồng nền: hồ sơ dùng lâu có hàng nghìn file
    cache, duyệt hết mỗi lần mở trang làm trang chậm cả giây."""
    hit = _sizes.get(pid)
    if hit and time.time() - hit[0] < SIZE_TTL:
        return hit[1]
    if pid not in _sizing:
        _sizing.add(pid)

        def run():
            try:
                _sizes[pid] = (time.time(), round(_dir_size(PROFILES_DIR / pid) / 1e6, 1))
            finally:
                _sizing.discard(pid)
        threading.Thread(target=run, daemon=True, name="profile-size").start()
    return hit[1] if hit else None


def profile_details(only: str | None = None) -> list[dict]:
    """Hồ sơ kèm thông tin để hiện trên trang quản lý (chỉ đọc file). only = chỉ 1 hồ sơ (trang chi tiết)."""
    import re
    out = []
    for p in list_profiles():
        if only and p["id"] != only:
            continue
        d = PROFILES_DIR / p["id"]
        try:
            cfg = json.loads((d / "profile.json").read_text("utf-8"))
        except (OSError, ValueError):
            cfg = {}
        nav, scr, gl = cfg.get("navigator") or {}, cfg.get("screen") or {}, cfg.get("webgl") or {}
        # "ANGLE (NVIDIA, NVIDIA Quadro P2000 (0x00001C30) Direct3D11 …)" -> "NVIDIA Quadro P2000"
        m = re.match(r"ANGLE \([^,]*, ([^,(]+)", str(gl.get("renderer") or ""))
        gpu = (m.group(1) if m else str(gl.get("renderer") or "")).strip()
        lang = nav.get("language") or ""
        meta = read_meta(p["id"])
        s = _opened.get(p["id"])
        if s is not None and s.process.poll() is not None:     # người dùng tự đóng cửa sổ
            _opened.pop(p["id"], None)
            s = None
        out.append({**p, "gpu": gpu, "screen": f"{scr.get('width', '?')}×{scr.get('height', '?')}",
                    "cores": nav.get("hardware_concurrency"), "ram": nav.get("device_memory"),
                    "os": nav.get("platform") or "", "lang": lang, "english": lang.startswith("en"),
                    "template": cfg.get("name") or "",
                    "created": meta.get("created") or int((d / "profile.json").stat().st_mtime),
                    "notes": meta.get("notes") or "", "tags": meta.get("tags") or [],
                    "proxy_id": meta.get("proxy_id") or "", "webrtc": meta.get("webrtc") or "auto",
                    "extensions": meta.get("extensions") or [], "bookmarks": meta.get("bookmarks") or [],
                    "extra_args": meta.get("extra_args") or "", "pinned": bool(meta.get("pinned")),
                    "size_mb": profile_size_mb(p["id"]), "opened_here": s is not None,
                    "platform_version": (cfg.get("client_hints") or {}).get("platform_version") or "",
                    "mobile": _claims_mobile(cfg), "touch": nav.get("max_touch_points") or 0,
                    "timezone": cfg.get("timezone") or "", "webgpu": bool((cfg.get("webgpu") or {}).get("limits")),
                    "refresh_rate": scr.get("refresh_rate") or 0, "dpr": scr.get("device_pixel_ratio") or 1,
                    "screen_mode": meta.get("screen_mode") or "", "quic": meta.get("quic") or "auto",
                    "hide_name": bool(meta.get("hide_name")), "android_media": bool(meta.get("android_media")),
                    "webrtc_ip": meta.get("webrtc_ip") or ""})
    return out


def _claims_mobile(cfg: dict) -> bool:
    from shardx import Profile
    try:
        return Profile(cfg).claims_mobile
    except Exception:
        return False


def open_window(pid: str) -> None:
    """Mở cửa sổ hồ sơ để người dùng tự xem / đăng nhập / chỉnh trang (không gắn Playwright). Đang mở rồi -> thôi."""
    d = _profile_dir(pid)
    if _running_cdp(d):
        return
    if not engine_installed():
        raise BrowserError("Nhân trình duyệt đang tải — đợi tải xong rồi mở")
    s = sdk()
    try:
        with _quiet():
            _opened[pid] = s.launch(s.open_profile(pid), cdp=True, headless=False, **_launch_kwargs(pid))
    except BrowserError:
        raise
    except Exception as e:
        raise BrowserError(f"Không mở được hồ sơ: {e}") from e


def close_window(pid: str) -> None:
    """Đóng hồ sơ như ShardBrowser đóng (CDP Browser.close -> lưu cookie rồi thoát)."""
    d = _profile_dir(pid)
    s = _opened.pop(pid, None)
    if s is not None:
        s.stop()
        return
    ws = _running_cdp(d)
    if ws:
        from shardx.browser import _close_over_cdp
        _close_over_cdp(ws)


# trang kiểm tra dấu vân tay / IP mở được từ trang quản lý (chỉ các địa chỉ này — không nhận URL tuỳ ý từ form)
CHECK_SITES = {
    "browserscan": ("BrowserScan — độ thật của dấu vân tay", "https://www.browserscan.net/"),
    "pixelscan": ("Pixelscan — dấu vân tay có nhất quán", "https://pixelscan.net/"),
    "ip": ("IP / vị trí đang dùng", "https://ipinfo.io/"),
    "webrtc": ("Rò rỉ WebRTC", "https://browserleaks.com/webrtc"),
}


def open_url(pid: str, url: str) -> None:
    """Mở hồ sơ (chưa mở thì mở) rồi mở thêm 1 tab tới `url` qua cổng điều khiển của chính phiên đó."""
    import time as _t
    import urllib.parse
    import urllib.request
    d = _profile_dir(pid)
    open_window(pid)
    ws = None
    for _ in range(40):                          # cửa sổ vừa bật: chờ cổng điều khiển (tối đa ~20 s)
        ws = _running_cdp(d)
        if ws:
            break
        _t.sleep(0.5)
    if not ws:
        raise BrowserError("Hồ sơ chưa mở xong — thử lại sau vài giây")
    port = urllib.parse.urlparse(ws).port
    req = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(url, safe=':/?=&')}",
                                 method="PUT")
    try:
        urllib.request.urlopen(req, timeout=5).close()
    except OSError as e:
        raise BrowserError(f"Không mở được trang trong hồ sơ: {e}") from e


def rename_profile(pid: str, name: str) -> None:
    name = " ".join(name.split())[:80]
    if not name:
        raise BrowserError("Tên hồ sơ trống")
    profile_dir(pid)
    write_meta(pid, name=name)


def set_language(pid: str, english: bool) -> None:
    """Ngôn ngữ giao diện: theo IP (như tạo mới) hoặc tiếng Anh (hồ sơ TikTok). Áp dụng từ lần mở sau."""
    _profile_dir(pid)
    s = sdk()
    p = s.open_profile(pid)
    nav = p.config.setdefault("navigator", {})
    if english:
        nav.update(language="en-US", accept_language="en-US,en;q=0.9", languages=["en-US", "en"])
        p.config["icu_locale"] = "en-US"
    else:
        nav["language"] = "auto"
    with _quiet():
        s.save_profile(p)


# ---------------- mở hồ sơ ----------------

def _running_cdp(udd: Path) -> str | None:
    """Hồ sơ đang mở (vd. cửa sổ đăng nhập chưa đóng) -> địa chỉ CDP của phiên đó, không thì None."""
    import urllib.request
    # Chrome giữ khoá file `lockfile` trong thư mục hồ sơ khi đang chạy. Kiểm tra cái này trước: DevToolsActivePort còn
    # sót lại sau khi đóng, gọi vào cổng đã đóng thì Windows chờ ~2 s mỗi hồ sơ (trang Cài đặt / Tài khoản bị chậm)
    try:
        with open(udd / "lockfile", "rb"):
            return None                      # mở được = không ai giữ khoá = không chạy
    except FileNotFoundError:
        return None
    except PermissionError:
        pass                                 # đang chạy
    except OSError:
        return None
    try:
        port = int((udd / "DevToolsActivePort").read_text("utf-8").splitlines()[0].strip())
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.0) as r:
            return json.loads(r.read()).get("webSocketDebuggerUrl")
    except (OSError, ValueError, IndexError):
        return None


_MUTE_JS = """(() => {
  const play = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function () { this.muted = true; return play.apply(this, arguments); };
})();"""


def attach(pw, acc_dir: Path, headless: bool):
    """Mở hồ sơ của tài khoản, gắn Playwright, trả về BrowserContext. ctx.close() đóng luôn hồ sơ (nếu chính
    lần này mở nó)."""
    choice = get_choice(acc_dir)
    if not choice:
        raise BrowserError("Chưa có hồ sơ trình duyệt cho tài khoản này — vào Tài khoản & kênh, bấm Đăng nhập")
    pid = choice["profile_id"]
    s = sdk()
    sess = None
    ws = _running_cdp(PROFILES_DIR / pid)
    if not ws:
        wait_install()
        try:
            with _quiet():
                kw = _launch_kwargs(pid)
                # phiên tự động (đăng / quét) không phát tiếng: bản xem trước video trên Business Suite / TikTok
                # Studio tự chạy kèm âm thanh
                kw["extra_args"] = [*kw.get("extra_args", []), "--mute-audio"]
                sess = s.launch(s.open_profile(pid), cdp=True, headless=bool(headless), **kw)
        except BrowserError:
            raise
        except Exception as e:
            raise BrowserError(f"Không mở được hồ sơ '{choice.get('profile_name') or pid}': {e}") from e
        ws = sess.cdp_url
        if not ws:
            sess.stop()
            raise BrowserError("Nhân trình duyệt ShardX không mở cổng điều khiển (CDP) — thử lại, hoặc Cài lại thư viện")
    browser = pw.chromium.connect_over_cdp(ws)       # chỉ dùng giao thức CDP của Playwright, không chạy Chromium riêng
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    if not sess:
        # hồ sơ người dùng đang mở sẵn (không có --mute-audio): tắt tiếng video trong các trang dytool mở ra
        try:
            ctx.add_init_script(_MUTE_JS)
        except Exception:
            pass

    def close():
        try:
            browser.close()          # chỉ ngắt kết nối Playwright
        except Exception:
            pass
        if sess is not None:
            try:
                sess.stop()          # đóng nhân như ShardBrowser đóng hồ sơ (CDP Browser.close -> lưu cookie)
            except Exception:
                pass
    ctx.close = close                # nơi gọi vẫn dùng ctx.close() như trước
    return ctx
