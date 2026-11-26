"""Proxy xoay (rotating proxy): gọi API nhà cung cấp lấy proxy hiện tại / đổi IP, đọc ip:port trong kết quả.

Không có thư viện sẵn đáng dùng (gh search 2026-11-26: vài repo 0–3 sao, không license) -> tự viết, dạng chung:
mọi API trả JSON có chuỗi "host:port[:user:pass]" ở trường tên chứa http / socks (hoặc văn bản thuần) đều đọc được.

3 kiểu (người dùng muốn cả 3):
- "api"  : key / link API -> proxy hiện tại (ip:port đổi theo mỗi lần lấy). Có sẵn mẫu Proxy.vn, TMProxy; còn lại
           dán link (GET) lấy proxy mới + link lấy proxy hiện tại (không bắt buộc).
- "link" : proxy cố định (cổng không đổi) + 1 link "đổi IP" (gọi là đổi, nội dung trả về chỉ để báo).
- "auto" : proxy cố định, nhà cung cấp tự đổi IP sau mỗi N phút — không đổi tay được.

Tài liệu đã đọc:
- Proxy.vn (proxy.vn/?home=apixoay): GET/POST https://proxyxoay.shop/api/get.php?key=&nhamang=random|viettel|fpt|vnpt
  &tinhthanh=0..&whitelist= -> {"status":100,"message":"proxy nay se die sau 1777s","proxyhttp":"ip:port::",
  "proxysocks5":"ip:port::",...}; lỗi status 101 / 102. Không có API "proxy hiện tại": proxy sống tới hết giờ trong
  message -> lấy lại khi hết.
- TMProxy (docs.tmproxy.com, bản cộng đồng github.com/kleine-algo-paladin/tmproxy-api): POST JSON
  https://tmproxy.com/api/proxy/get-new-proxy {api_key,id_location,id_isp} / get-current-proxy {api_key}
  -> {code:0, message, data:{https:"ip:port", socks5:"ip:port", username, password, next_request,...}}; code 5 = chờ.
"""

from __future__ import annotations

import json
import re
import time
from urllib.parse import quote

import httpx

PROVIDERS = {
    "proxyvn": "Proxy.vn (key xoay)",
    "tmproxy": "TMProxy (API key)",
    "kiotproxy": "KiotProxy (key)",
    "custom": "Link API khác",
}
KINDS = {"api": "Lấy proxy qua API / key", "link": "Cổng cố định + link đổi IP", "auto": "Cổng cố định tự xoay"}
PROXYVN_ISP = ["random", "viettel", "fpt", "vnpt"]

_HOSTPORT = re.compile(r"^(?:(?P<scheme>socks5h?|https?)://)?(?:(?P<u1>[^:@/\s]*):(?P<p1>[^@/\s]*)@)?"
                       r"(?P<host>[A-Za-z0-9.\-]+):(?P<port>\d{2,5})(?::(?P<u2>[^:\s]*):(?P<p2>[^\s]*))?$")
_FIND = re.compile(r"(?:(?:socks5h?|https?)://)?(?:[^\s:@/\"']+:[^\s@/\"']+@)?"
                   r"(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}(?::[^\s:\"',]*:[^\s\"',]*)?")


class RotateError(RuntimeError):
    def __init__(self, msg: str, wait: int | None = None):
        super().__init__(msg)
        self.wait = wait            # nhà cung cấp bắt chờ N giây mới đổi được


def _url(scheme: str, host: str, port: str, user: str = "", pw: str = "") -> str:
    cred = f"{quote(user, safe='')}:{quote(pw, safe='')}@" if (user or pw) else ""
    return f"{scheme}://{cred}{host}:{port}"


def parse_proxy_str(s: str, scheme: str, user: str = "", pw: str = "") -> str | None:
    """'ip:port', 'ip:port:user:pass', 'ip:port::', 'user:pass@ip:port', 'socks5://…' -> URL chuẩn (None nếu không phải)."""
    m = _HOSTPORT.match(str(s).strip())
    if not m:
        return None
    sch = m["scheme"] or scheme
    u = m["u1"] or m["u2"] or user
    p = m["p1"] or m["p2"] or pw
    return _url(sch, m["host"], m["port"], u or "", p or "")


def _flat(obj, prefix: str = "") -> list[tuple[str, object]]:
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += _flat(v, f"{prefix}.{k}".lower() if prefix else str(k).lower())
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:5]):
            out += _flat(v, f"{prefix}[{i}]")
    else:
        out.append((prefix, obj))
    return out


def _wait_secs(msg: str) -> int | None:
    m = re.search(r"(\d+)\s*(?:s\b|sec|second|giây|giay)", msg or "", re.I)
    return int(m.group(1)) if m else None


def parse_response(text: str, scheme: str = "http") -> dict:
    """Kết quả API -> {"url", "alts": {http, socks5}, "ttl", "msg"}. Không thấy proxy -> RotateError (kèm lời nhắn API)."""
    try:
        js = json.loads(text)
    except ValueError:
        js = None
    if js is None:                                      # văn bản thuần: dòng đầu có ip:port
        m = _FIND.search(text or "")
        url = parse_proxy_str(m.group(0), scheme) if m else None
        if not url:
            raise RotateError(f"API không trả proxy: {(text or '').strip()[:160] or '(trống)'}",
                              _wait_secs(text))
        return {"url": url, "alts": {scheme: url}, "ttl": None, "msg": ""}
    pairs = _flat(js)
    vals = {k.rsplit(".", 1)[-1]: v for k, v in pairs}
    msg = str(vals.get("message") or vals.get("msg") or vals.get("error") or "")
    code = vals.get("code")
    # TMProxy code != 0 / {"success": false} / Proxy.vn status 101, 102 = lỗi (dù data còn proxy cũ)
    bad = (vals.get("success") is False or (isinstance(code, int) and code != 0 and "code" in js)
           or (isinstance(vals.get("status"), int) and vals["status"] in (101, 102)))
    user = str(vals.get("username") or vals.get("user") or "")
    pw = str(vals.get("password") or vals.get("pass") or "")
    alts: dict[str, str] = {}
    for k, v in pairs:
        if not isinstance(v, str):
            continue
        leaf = k.rsplit(".", 1)[-1]
        kind = "socks5" if "socks" in leaf else ("http" if "http" in leaf or "proxy" in leaf else None)
        if not kind:
            continue
        u = parse_proxy_str(v, kind, user, pw)
        if u:
            alts.setdefault(kind, u)
    if not alts:                                        # dạng {"ip": "...", "port": 123}
        host = vals.get("ip") or vals.get("host")
        port = vals.get("port") or vals.get("httpport") or vals.get("http_port")
        if host and port and str(port).isdigit():
            alts["http" if scheme == "http" else scheme] = _url(scheme, str(host), str(port), user, pw)
    if bad or not alts:
        raise RotateError(f"API báo: {msg or json.dumps(js, ensure_ascii=False)[:160]}", _wait_secs(msg))
    url = alts.get(scheme) or next(iter(alts.values()))
    m = re.search(r"die\s+sau\s+(\d+)", msg, re.I)
    ttl = int(m.group(1)) if m else None
    return {"url": url, "alts": alts, "ttl": ttl, "msg": msg}


def _call(method: str, url: str, params: dict | None = None, body: dict | None = None) -> str:
    try:
        with httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 dytool"}) as c:
            r = c.post(url, json=body) if method == "POST" else c.get(url, params=params)
    except httpx.HTTPError as e:
        raise RotateError(f"không gọi được API proxy ({type(e).__name__}: {e})") from e
    if r.status_code >= 500:
        raise RotateError(f"API proxy lỗi HTTP {r.status_code}")
    return r.text


def fetch(rot: dict, new: bool) -> dict:
    """Kiểu "api": new=True -> xin proxy mới (đổi IP); False -> proxy hiện tại (không tốn lượt đổi; nhà cung cấp không có
    API này thì trả None để dùng proxy đã lưu)."""
    prov, key, scheme = rot.get("provider"), rot.get("key", ""), rot.get("scheme", "http")
    if prov == "proxyvn":
        if not new:
            return None
        isp = rot.get("isp") if rot.get("isp") in PROXYVN_ISP else "random"
        text = _call("GET", "https://proxyxoay.shop/api/get.php",
                     {"key": key, "nhamang": isp, "tinhthanh": str(int(rot.get("region") or 0)), "whitelist": ""})
    elif prov == "tmproxy":
        if new:
            text = _call("POST", "https://tmproxy.com/api/proxy/get-new-proxy",
                         body={"api_key": key, "id_location": int(rot.get("region") or 0), "id_isp": 0})
        else:
            text = _call("POST", "https://tmproxy.com/api/proxy/get-current-proxy", body={"api_key": key})
    elif prov == "kiotproxy":       # github.com/Qliti92/kiotproxy: GET …/proxies/new?key=&region= -> {success, data:{http, socks5}}
        if not new:
            return None
        text = _call("GET", "https://api.kiotproxy.com/api/v1/proxies/new", {"key": key, "region": "random"})
    else:
        link = rot.get("new_url") if new else rot.get("cur_url")
        if not link:
            return None
        text = _call("GET", link)
    return parse_response(text, scheme)


def change_ip(link: str) -> str:
    """Kiểu "link": gọi link đổi IP. Trả lời nhắn của nhà cung cấp (rút gọn). Lỗi rõ ràng -> RotateError."""
    text = _call("GET", link)
    t = (text or "").strip()
    try:
        js = json.loads(t)
        vals = {k.rsplit(".", 1)[-1]: v for k, v in _flat(js)}
        msg = str(vals.get("message") or vals.get("msg") or vals.get("error") or t)[:200]
        if vals.get("success") is False or vals.get("status") in ("error", "fail", False) or (
                isinstance(vals.get("code"), int) and vals["code"] not in (0, 200)):
            raise RotateError(f"Đổi IP lỗi: {msg}", _wait_secs(msg))
        return msg
    except ValueError:
        return re.sub(r"<[^>]+>", " ", t)[:200]


def redact(link: str, key: str = "") -> str:
    """Che key trong link khi hiện / ghi nhật ký."""
    s = re.sub(r"((?:key|api_key|token|apikey)=)[^&]+", r"\1•••", link or "", flags=re.I)
    return s.replace(key, "•••") if key and len(key) > 3 else s


def now() -> int:
    return int(time.time())
