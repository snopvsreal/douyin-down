"""Quản lý hồ sơ trình duyệt (trang Hồ sơ trình duyệt) — các chức năng giống app ShardBrowser, làm bằng SDK `shardx`:
kho proxy (dán nhiều dòng, kiểm tra IP / nước / UDP), cài đặt riêng từng hồ sơ (ghi chú, nhãn, proxy, WebRTC, tiện ích,
bookmark, tham số khởi động), dấu vân tay (chọn mẫu / làm mới, nhiễu), cookie nhập / xuất, nhân bản, thùng rác.

Dữ liệu (đều trong data/shardx — data/ không lên git):
- profiles/<id>/dytool.json   cài đặt dytool của hồ sơ (xem browsers.read_meta)
- proxies.json                [{id, url, label, check: {...}}]  (url có thể chứa mật khẩu proxy)
- extensions/<id Web Store>/  tiện ích đã giải nén (tải .crx từ máy chủ cập nhật chính thức của Google)
- trash/<id>/                 hồ sơ đã xoá (giữ 30 ngày, khôi phục được)
"""

import json
import re
import shutil
import time
import uuid
from pathlib import Path

from . import browsers
from .browsers import SDK_DIR, PROFILES_DIR, BrowserError, read_meta, write_meta, _valid_id, _quiet

PROXIES = SDK_DIR / "proxies.json"
EXT_DIR = SDK_DIR / "extensions"
TRASH = SDK_DIR / "trash"
TRASH_DAYS = 30
NOISE_VECTORS = [("canvas", "Canvas"), ("webgl", "WebGL"), ("audio", "Âm thanh"), ("fonts", "Phông chữ"),
                 ("sensors", "Cảm biến"), ("client_rects", "Kích thước phần tử (không nên bật)")]
WEBRTC = {"auto": "Tự động (khuyên dùng)", "tcp_only": "Chỉ TCP (proxy không có UDP)", "block": "Chặn hẳn WebRTC"}


# ================= proxy =================

def list_proxies() -> list[dict]:
    try:
        data = json.loads(PROXIES.read_text("utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_proxies(items: list[dict]) -> None:
    PROXIES.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROXIES.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(PROXIES)


def get_proxy(proxy_id: str) -> dict | None:
    return next((p for p in list_proxies() if p.get("id") == proxy_id), None)


def mask(url: str) -> str:
    """socks5://user:pass@host:port -> socks5://user:•••@host:port (hiện trên giao diện)."""
    return re.sub(r"(://[^:/@]*:)[^@]*@", r"\1•••@", url)


def normalize_proxy(line: str, scheme: str = "socks5") -> str:
    """Nhận các dạng hay gặp, trả URL chuẩn scheme://[user:pass@]host:port:
    socks5://u:p@h:port · http://h:port · h:port · h:port:u:p · u:p@h:port · u:p:h:port (port là số)."""
    from shardx import parse_proxy
    s = line.strip()
    if not s:
        raise ValueError("trống")
    if "://" not in s:
        parts = s.split(":")
        if "@" in s:
            s = f"{scheme}://{s}"
        elif len(parts) == 2:
            s = f"{scheme}://{s}"
        elif len(parts) == 4 and parts[1].isdigit():                      # host:port:user:pass
            s = f"{scheme}://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
        elif len(parts) == 4 and parts[3].isdigit():                      # user:pass:host:port
            s = f"{scheme}://{parts[0]}:{parts[1]}@{parts[2]}:{parts[3]}"
        else:
            raise ValueError("không nhận ra dạng proxy")
    p = parse_proxy(s)                                                      # báo lỗi nếu thiếu host / port / sai loại
    cred = ""
    if p.username or p.password:
        from urllib.parse import quote
        cred = f"{quote(p.username or '', safe='')}:{quote(p.password or '', safe='')}@"
    return f"{p.scheme}://{cred}{p.host}:{p.port}"


def add_proxies(text: str, scheme: str = "socks5", label: str = "") -> tuple[int, list[str]]:
    """Dán nhiều proxy (mỗi dòng 1 cái). Trả (số đã thêm, các dòng lỗi). Trùng thì bỏ qua."""
    scheme = scheme if scheme in ("socks5", "http", "https") else "socks5"
    items = list_proxies()
    have = {p["url"] for p in items}
    added, bad = 0, []
    for line in text.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        try:
            url = normalize_proxy(line, scheme)
        except Exception as e:
            bad.append(f"{mask(line.strip())[:60]} ({e})")
            continue
        if url in have:
            continue
        items.append({"id": uuid.uuid4().hex[:12], "url": url, "label": label.strip()[:60], "check": None})
        have.add(url)
        added += 1
    _save_proxies(items)
    return added, bad


def update_proxy(proxy_id: str, label: str) -> None:
    items = list_proxies()
    for p in items:
        if p["id"] == proxy_id:
            p["label"] = " ".join(label.split())[:60]
    _save_proxies(items)


def delete_proxy(proxy_id: str) -> list[str]:
    """Xoá proxy; hồ sơ đang dùng nó chuyển về không proxy. Trả tên các hồ sơ bị bỏ proxy."""
    _save_proxies([p for p in list_proxies() if p.get("id") != proxy_id])
    names = []
    for p in browsers.list_profiles():
        m = read_meta(p["id"])
        if m.get("proxy_id") == proxy_id:
            write_meta(p["id"], proxy_id="")
            names.append(p["name"])
    return names


def check_proxy(proxy_id: str) -> dict:
    """Kiểm tra qua proxy: IP ra ngoài, nước / thành phố / nhà mạng, UDP (QUIC + WebRTC). Lưu kết quả."""
    p = get_proxy(proxy_id)
    if not p:
        raise BrowserError("Không có proxy này")
    res = {"at": int(time.time())}
    t = time.time()
    try:
        r = browsers.sdk().check_proxy(p["url"])
        g = r["geo"]
        if not getattr(g, "ip", ""):
            raise RuntimeError("không lấy được IP qua proxy")
        res.update(ok=True, ip=g.ip, country=g.country, cc=g.country_code, city=g.city, isp=g.isp,
                   timezone=g.timezone, udp_ms=round(r["udp_ms"]) if r["udp_ms"] is not None else None,
                   ms=int((time.time() - t) * 1000))
    except Exception as e:
        res.update(ok=False, error=str(e)[:200])
    items = list_proxies()
    for x in items:
        if x["id"] == proxy_id:
            x["check"] = res
    _save_proxies(items)
    return res


def proxy_usage() -> dict[str, list[str]]:
    use: dict[str, list[str]] = {}
    for p in browsers.list_profiles():
        pid = read_meta(p["id"]).get("proxy_id")
        if pid:
            use.setdefault(pid, []).append(p["name"])
    return use


# ================= cài đặt hồ sơ =================

def save_general(pid: str, name: str, notes: str, tags: str) -> None:
    browsers.rename_profile(pid, name)
    tag_list = []
    for t in re.split(r"[,;\n]", tags):
        t = " ".join(t.split())[:30]
        if t and t.lower() not in {x.lower() for x in tag_list}:
            tag_list.append(t)
    write_meta(pid, notes=notes.strip()[:2000], tags=tag_list[:12])


def save_network(pid: str, proxy_id: str, webrtc: str) -> None:
    if proxy_id and not get_proxy(proxy_id):
        raise BrowserError("Không có proxy này")
    write_meta(pid, proxy_id=proxy_id, webrtc=webrtc if webrtc in WEBRTC else "auto")


def save_launch(pid: str, screen_mode: str, quic: str, hide_name: bool, android_media: bool, webrtc_ip: str,
                refresh_rate: str) -> None:
    """Tuỳ chọn khởi động của nhân ShardX: cách dựng màn hình, QUIC, ẩn tên hồ sơ, codec Android, IP công khai cho
    WebRTC, tần số quét màn hình (ghi vào dấu vân tay — trang web đo bằng requestAnimationFrame)."""
    import ipaddress
    ip = webrtc_ip.strip()
    if ip:
        try:
            ip = str(ipaddress.ip_address(ip))
        except ValueError:
            raise BrowserError("IP công khai cho WebRTC không hợp lệ (vd. 203.0.113.5) — để trống = tự lấy theo proxy")
    rr = refresh_rate.strip()
    if rr and not (rr.isdigit() and 24 <= int(rr) <= 480):
        raise BrowserError("Tần số quét phải từ 24 đến 480 Hz (để trống = 60 Hz như phần lớn máy)")
    write_meta(pid, screen_mode=screen_mode if screen_mode in browsers.SCREEN_MODES else None,
               quic=quic if quic in ("on", "off") else None, hide_name=True if hide_name else None,
               android_media=True if android_media else None, webrtc_ip=ip or None)
    s = browsers.sdk()
    prof = s.open_profile(browsers.profile_dir(pid).name)
    scr = prof.config.setdefault("screen", {})
    if rr:
        scr["refresh_rate"] = int(rr)
    else:
        scr.pop("refresh_rate", None)
    with _quiet():
        s.save_profile(prof)


def save_advanced(pid: str, extra_args: str) -> None:
    browsers.parse_extra_args(extra_args)                  # báo lỗi nếu có tham số bị chặn
    write_meta(pid, extra_args=extra_args.strip()[:1000])


def save_bookmarks(pid: str, text: str) -> int:
    """Mỗi dòng: `Tên | https://…` hoặc chỉ URL."""
    items = []
    for line in text.splitlines():
        if not line.strip():
            continue
        title, _, url = line.rpartition("|") if "|" in line else ("", "", line)
        url, title = url.strip(), title.strip()
        if not re.match(r"^https?://\S+$", url):
            raise BrowserError(f"Bookmark không hợp lệ (cần http:// hoặc https://): {line.strip()[:80]}")
        items.append({"title": title[:100] or url[:100], "url": url[:2000]})
    write_meta(pid, bookmarks=items[:100])
    return len(items)


# ================= dấu vân tay =================

_tpl_cache: dict = {}


def templates() -> dict[str, list[str]]:
    """Mẫu dấu vân tay có sẵn (220 mẫu máy thật) theo nền tảng. Lọc theo nền tảng phải đọc cả 220 file (~0.5 s) ->
    nhớ kết quả tới khi thư viện mẫu đổi (thời gian sửa thư mục)."""
    s = browsers.sdk()
    fp = s.runtime.fingerprints_dir
    key = (fp.stat().st_mtime, len(list(fp.glob("*.json"))))
    if _tpl_cache.get("key") != key:
        with _quiet():
            _tpl_cache["val"] = {plat: s.list_profiles(platform=plat) for plat, _l in browsers.PLATFORMS}
        _tpl_cache["key"] = key
    return _tpl_cache["val"]


def regenerate(pid: str, template: str = "", platform: str = "Windows") -> str:
    """Dấu vân tay mới (mẫu chọn / ngẫu nhiên) cho hồ sơ — giữ cookie, ngôn ngữ, nhiễu đang bật. Trả tên mẫu."""
    from shardx import randomize_hardware, randomize_platform_version
    s = browsers.sdk()
    old = s.open_profile(browsers.profile_dir(pid).name)
    with _quiet():
        cfg = dict(s.library.load(template).config if template else
                   s.random_profile(platform=platform if platform in dict(browsers.PLATFORMS) else "Windows").config)
    randomize_hardware(cfg, profile_id=uuid.uuid4().hex)    # hạt giống mới -> phần cứng khác lần trước
    randomize_platform_version(cfg)
    nav_old, nav = old.config.get("navigator") or {}, cfg.setdefault("navigator", {})
    cfg["timezone"] = old.config.get("timezone", "auto")
    for k in ("language", "accept_language", "languages"):
        if k in nav_old:
            nav[k] = nav_old[k]
    if str(nav_old.get("language", "")).startswith("en"):
        cfg["icu_locale"] = old.config.get("icu_locale", "en-US")
    rr = (old.config.get("screen") or {}).get("refresh_rate")
    if rr:                                                    # tần số quét người dùng đã chọn: giữ
        cfg.setdefault("screen", {})["refresh_rate"] = rr
    on = [v for v, b in (old.config.get("noise") or {}).items() if isinstance(b, dict) and b.get("enabled")]
    from shardx import Profile
    p = Profile(cfg, id=old.id)
    p.set_noise(*[v for v in on if v in dict(NOISE_VECTORS)])
    for b in (p.config.get("noise") or {}).values():         # hạt giống nhiễu tính lại theo hồ sơ lúc mở
        if isinstance(b, dict):
            b["seed"] = 0
    with _quiet():
        s.save_profile(p)
    return cfg.get("name") or template


def set_noise(pid: str, vectors: list[str]) -> None:
    s = browsers.sdk()
    p = s.open_profile(browsers.profile_dir(pid).name)
    p.set_noise(*[v for v in vectors if v in dict(NOISE_VECTORS)])
    with _quiet():
        s.save_profile(p)


def noise_on(pid: str) -> list[str]:
    try:
        cfg = json.loads((browsers.profile_dir(pid) / "profile.json").read_text("utf-8"))
    except (OSError, ValueError):
        return []
    return [v for v, b in (cfg.get("noise") or {}).items() if isinstance(b, dict) and b.get("enabled")]


# ================= nhân bản / thùng rác =================

def clone(pid: str) -> dict:
    """Hồ sơ mới cùng cài đặt (proxy, ngôn ngữ, nhiễu, tiện ích, bookmark, tham số) nhưng DẤU VÂN TAY MỚI cùng loại
    máy và CHƯA đăng nhập — 2 hồ sơ chung dấu vân tay / cookie thì nền tảng dễ nối 2 tài khoản với nhau."""
    src = browsers.profile_dir(pid)
    meta = read_meta(pid)
    cfg = json.loads((src / "profile.json").read_text("utf-8"))
    eng = str((cfg.get("navigator") or {}).get("language", "")).startswith("en")
    rr = (cfg.get("screen") or {}).get("refresh_rate")
    c = browsers.create_profile(f"{meta.get('name') or pid} (bản sao)"[:80], english=eng,
                                refresh_rate=int(rr) if rr else None)
    new = c["profile_id"]
    keep = {k: meta[k] for k in ("notes", "tags", "proxy_id", "webrtc", "extensions", "bookmarks", "extra_args",
                                 "screen_mode", "quic", "hide_name", "android_media", "webrtc_ip")
            if k in meta}
    write_meta(new, **keep)
    tpl = cfg.get("name") or ""
    try:
        regenerate(new, template=tpl)
    except Exception:
        pass                                              # mẫu không còn trong thư viện -> giữ mẫu ngẫu nhiên lúc tạo
    on = noise_on(pid)
    if on:
        set_noise(new, on)
    return c


def trash(pid: str) -> None:
    d = browsers.profile_dir(pid)
    if browsers.is_running(pid):
        raise BrowserError("Hồ sơ đang mở — đóng trước rồi xoá")
    TRASH.mkdir(parents=True, exist_ok=True)
    write_meta(pid, deleted_at=int(time.time()))
    shutil.move(str(d), str(TRASH / pid))


def list_trash() -> list[dict]:
    purge_old()
    out = []
    if TRASH.is_dir():
        for d in TRASH.iterdir():
            if not _valid_id(d.name):
                continue
            try:
                m = json.loads((d / browsers.META).read_text("utf-8"))
            except (OSError, ValueError):
                m = {}
            out.append({"id": d.name, "name": m.get("name") or d.name, "deleted_at": m.get("deleted_at") or 0,
                        "size_mb": round(browsers._dir_size(d) / 1e6, 1)})
    return sorted(out, key=lambda x: -x["deleted_at"])


def restore(pid: str) -> None:
    if not _valid_id(pid) or not (TRASH / pid).is_dir():
        raise BrowserError("Không có hồ sơ này trong thùng rác")
    if (PROFILES_DIR / pid).exists():
        raise BrowserError("Đã có hồ sơ cùng mã")
    shutil.move(str(TRASH / pid), str(PROFILES_DIR / pid))
    write_meta(pid, deleted_at=None)


def purge(pid: str) -> None:
    if not _valid_id(pid) or not (TRASH / pid).is_dir():
        raise BrowserError("Không có hồ sơ này trong thùng rác")
    shutil.rmtree(TRASH / pid)


def purge_old() -> None:
    if not TRASH.is_dir():
        return
    limit = time.time() - TRASH_DAYS * 86400
    for d in TRASH.iterdir():
        try:
            at = json.loads((d / browsers.META).read_text("utf-8")).get("deleted_at") or d.stat().st_mtime
        except (OSError, ValueError):
            at = d.stat().st_mtime
        if _valid_id(d.name) and at < limit:
            shutil.rmtree(d, ignore_errors=True)


# ================= cookie =================

_SAMESITE = {"no_restriction": "None", "none": "None", "lax": "Lax", "strict": "Strict"}


def _to_playwright(c: dict) -> dict | None:
    """Cookie từ file xuất của dytool / Playwright / EditThisCookie / Cookie-Editor -> dạng Playwright add_cookies."""
    name, value = c.get("name"), c.get("value")
    domain = c.get("domain") or c.get("host")
    if not name or value is None or not domain:
        return None
    out = {"name": str(name), "value": str(value), "domain": str(domain), "path": c.get("path") or "/",
           "httpOnly": bool(c.get("httpOnly", False)), "secure": bool(c.get("secure", False))}
    exp = c.get("expires", c.get("expirationDate", c.get("expiry")))
    if isinstance(exp, (int, float)) and exp > 0 and not c.get("session"):
        out["expires"] = float(exp)
    ss = _SAMESITE.get(str(c.get("sameSite", "")).lower())
    if ss:
        out["sameSite"] = ss
        if ss == "None":
            out["secure"] = True                           # Chrome bỏ cookie SameSite=None không Secure
    return out


def export_cookies(pid: str) -> list[dict]:
    from playwright.sync_api import sync_playwright
    holder = _holder(pid)
    with sync_playwright() as pw:
        ctx = browsers.attach(pw, holder, headless=True)
        try:
            return ctx.cookies()
        finally:
            ctx.close()


def import_cookies(pid: str, text: str) -> int:
    try:
        data = json.loads(text)
    except ValueError as e:
        raise BrowserError(f"File cookie không phải JSON: {e}") from e
    if isinstance(data, dict):
        data = data.get("cookies") or []
    if not isinstance(data, list):
        raise BrowserError("File cookie phải là danh sách cookie (JSON)")
    cookies = [x for x in (_to_playwright(c) for c in data if isinstance(c, dict)) if x]
    if not cookies:
        raise BrowserError("Không có cookie hợp lệ trong file")
    from playwright.sync_api import sync_playwright
    holder = _holder(pid)
    with sync_playwright() as pw:
        ctx = browsers.attach(pw, holder, headless=True)
        try:
            ctx.add_cookies(cookies)
        finally:
            ctx.close()
    return len(cookies)


def _holder(pid: str) -> Path:
    """attach() mở hồ sơ theo file browser.json của 1 thư mục -> tạo thư mục tạm trỏ tới hồ sơ."""
    import tempfile
    browsers.profile_dir(pid)
    d = Path(tempfile.mkdtemp(prefix="dy-prof-"))
    browsers.set_choice(d, pid, "")
    return d


# ================= tiện ích (extension) =================

def _ext_name(d: Path) -> tuple[str, str]:
    try:
        m = json.loads((d / "manifest.json").read_text("utf-8-sig"))
    except (OSError, ValueError):
        return d.name, ""
    name = str(m.get("name") or d.name)
    if name.startswith("__MSG_") and name.endswith("__"):            # tên theo ngôn ngữ: _locales/<mặc định>
        key = name[6:-2]
        for loc in (m.get("default_locale") or "en", "en", "en_US"):
            try:
                msgs = json.loads((d / "_locales" / loc / "messages.json").read_text("utf-8-sig"))
                hit = next((v for k, v in msgs.items() if k.lower() == key.lower()), None)
                if hit and hit.get("message"):
                    name = hit["message"]
                    break
            except (OSError, ValueError):
                continue
    return name, str(m.get("version") or "")


def list_extensions() -> list[dict]:
    out = []
    if EXT_DIR.is_dir():
        for d in sorted(EXT_DIR.iterdir()):
            if re.fullmatch(r"[a-p]{32}", d.name) and (d / "manifest.json").exists():
                name, ver = _ext_name(d)
                out.append({"id": d.name, "name": name, "version": ver})
    return out


def add_extension(src: str) -> dict:
    """Dán link Chrome Web Store hoặc mã tiện ích (32 chữ a–p). Tải .crx từ máy chủ cập nhật chính thức của Google,
    giải nén vào data/shardx/extensions/<mã>. Đã có -> tải lại (cập nhật)."""
    import io
    import struct
    import zipfile
    import httpx
    m = re.search(r"\b([a-p]{32})\b", src.strip())
    if not m:
        raise BrowserError("Không thấy mã tiện ích — dán link chromewebstore.google.com/detail/… hoặc mã 32 chữ")
    ext_id = m.group(1)
    url = ("https://clients2.google.com/service/update2/crx?response=redirect&prodversion=158.0.0.0"
           f"&acceptformat=crx2,crx3&x=id%3D{ext_id}%26installsource%3Dondemand%26uc")
    try:
        r = httpx.get(url, follow_redirects=True, timeout=httpx.Timeout(60, connect=15))
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise BrowserError(f"Không tải được tiện ích {ext_id}: {e}") from e
    b = r.content
    if b[:4] != b"Cr24":
        raise BrowserError("Google không trả về tiện ích (sai mã, tiện ích đã gỡ khỏi Web Store hoặc không cho tải)")
    ver = struct.unpack("<I", b[4:8])[0]
    if ver == 3:
        start = 12 + struct.unpack("<I", b[8:12])[0]
    elif ver == 2:
        pk, sig = struct.unpack("<II", b[8:16])
        start = 16 + pk + sig
    else:
        raise BrowserError(f"Định dạng .crx lạ (phiên bản {ver})")
    dest = EXT_DIR / ext_id
    tmp = EXT_DIR / f".{ext_id}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        with zipfile.ZipFile(io.BytesIO(b[start:])) as z:
            root = tmp.resolve()
            for info in z.infolist():                      # chặn đường dẫn thoát ra ngoài thư mục
                target = (tmp / info.filename).resolve()
                if root not in target.parents and target != root:
                    raise BrowserError("Tiện ích có đường dẫn lạ — không cài")
            z.extractall(tmp)
        if not (tmp / "manifest.json").exists():
            raise BrowserError("Tiện ích thiếu manifest.json")
        shutil.rmtree(dest, ignore_errors=True)
        tmp.replace(dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    name, version = _ext_name(dest)
    return {"id": ext_id, "name": name, "version": version}


def remove_extension(ext_id: str) -> None:
    if not re.fullmatch(r"[a-p]{32}", ext_id):
        raise BrowserError("Mã tiện ích không hợp lệ")
    shutil.rmtree(EXT_DIR / ext_id, ignore_errors=True)
    for p in browsers.list_profiles():
        ids = read_meta(p["id"]).get("extensions") or []
        if ext_id in ids:
            write_meta(p["id"], extensions=[x for x in ids if x != ext_id])


def save_extensions(pid: str, ids: list[str]) -> None:
    have = {e["id"] for e in list_extensions()}
    write_meta(pid, extensions=[i for i in ids if i in have])
