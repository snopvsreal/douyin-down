"""Douyin đòi xác minh (captcha): đọc CÂU HỎI đang hiện trong cửa sổ douyin.com (chỉ phần chữ — không đọc ảnh, không
bấm gì) và dịch sang tiếng Việt để người dùng tự giải. dytool KHÔNG tự giải / gợi ý đáp án captcha (người dùng hỏi
2026-11-26 — đã từ chối: đó là lách cơ chế chống bot).

Luồng nền `ensure_watch` chỉ chạy khi worker.captcha đang bật: 3 s / lần nối CDP vào hồ sơ trình duyệt Douyin, đọc
chữ trong khung `verifycenter/captcha` của từng tab douyin.com (dòng 1 = câu hỏi, dòng 2 = yêu cầu thao tác), dịch
(nhớ theo câu). Trang Hôm nay đọc `snapshot()` — có ngay, không chờ trình duyệt."""
from __future__ import annotations

import threading
import time
import urllib.parse
from pathlib import Path

# kiểu captcha nhận theo chữ trên khung -> cách thao tác (tiếng Việt)
KINDS = [
    (("拖拽到下方", "拖拽到这里"), "Kéo TẤT CẢ ảnh khớp với câu hỏi xuống ô 拖拽到这里 (kéo vào đây) ở dưới — giữ chuột "
                                   "trái vào ảnh, kéo xuống ô, thả ra; làm lần lượt từng ảnh — rồi bấm 提交 (gửi). "
                                   "Sót hay thừa 1 ảnh là Douyin ra câu khác."),
    (("旋转", "角度"), "Kéo thanh trượt bên dưới: ảnh tròn xoay theo — thả chuột khi ảnh đứng thẳng."),
    (("滑块", "拼图"), "Giữ chuột vào nút đầu trái thanh trượt, kéo sang phải tới khi mảnh ghép khớp vào chỗ trống "
                       "trên ảnh rồi thả."),
    (("依次点击",), "Bấm vào ảnh theo đúng thứ tự các chữ trong ngoặc 「」 — so hình dạng nét chữ, không cần biết đọc — "
                   "rồi bấm 确定 (xác nhận)."),
    (("点击", "选择"), "Bấm vào những chỗ trên ảnh khớp với câu hỏi, rồi bấm 确定 (xác nhận)."),
    (("短信", "发送"), "Bấm 发送 (gửi), nhập mã gửi về điện thoại của tài khoản Douyin, bấm 验证 (xác minh)."),
]
_SKIP = {"刷新", "反馈", "提交", "确定", "拖拽到这里", "验证码"}     # chữ nút, không phải câu hỏi

_lock = threading.Lock()
_state: dict = {"tabs": [], "at": 0.0, "err": ""}
_tr_cache: dict[str, str] = {}
_thread: threading.Thread | None = None

_JS = r"""() => document.body ? document.body.innerText.split(/\n/).map(s => s.trim()).filter(Boolean).slice(0, 10) : []"""


def to_vietnamese(text: str) -> str:
    """Dịch máy Trung -> Việt (Google, dự phòng MyMemory); nhớ theo câu. Lỗi -> ''."""
    import httpx
    text = " ".join((text or "").split())[:300]
    if not text:
        return ""
    with _lock:
        if text in _tr_cache:
            return _tr_cache[text]
    out = ""
    try:
        r = httpx.get("https://translate.googleapis.com/translate_a/single",
                      params={"client": "gtx", "sl": "zh-CN", "tl": "vi", "dt": "t", "q": text}, timeout=8)
        r.raise_for_status()
        out = "".join(part[0] for part in r.json()[0] if part and part[0])
    except Exception:
        try:
            r = httpx.get("https://api.mymemory.translated.net/get",
                          params={"q": text, "langpair": "zh-CN|vi"}, timeout=8)
            r.raise_for_status()
            out = str(r.json()["responseData"]["translatedText"])
        except Exception:
            out = ""
    if out:
        with _lock:
            _tr_cache[text] = out
            if len(_tr_cache) > 300:
                _tr_cache.clear()
    return out


# Dịch máy hay sai nghĩa câu chủ đề (vd. 具有旋转特性的东西 = "đồ vật có thể xoay / quay" -> Google: "có tính chất luân
# chuyển") -> nhờ AI của dytool dịch lại CHỈ CÂU CHỮ (không gửi ảnh), ở luồng riêng: hiện bản Google ngay, AI xong thì thay.
_ai_cache: dict[str, str] = {}
_ai_busy: set[str] = set()
_ai_failed: set[str] = set()     # AI lỗi với câu này -> dùng bản Google, không gọi lại mỗi 3 s

_AI_PROMPT = ("Dịch câu tiếng Trung dưới đây sang tiếng Việt tự nhiên, đúng nghĩa đời thường. Đây là câu chủ đề của 1 "
              "captcha chọn ảnh, người đọc không biết tiếng Trung. Chỉ trả về đúng 1 câu dịch, không giải thích, "
              "không thêm gì khác.\n\n{q}")


def _ai_translate(q: str) -> None:
    from . import ai
    try:
        out = ai.ask(_AI_PROMPT.format(q=q), timeout=90)
        out = " ".join(out.strip().strip('"“”').split())[:200]
    except Exception:
        out = ""
    with _lock:
        _ai_busy.discard(q)
        if out:
            _ai_cache[q] = out
        else:
            _ai_failed.add(q)


def better_vietnamese(q: str) -> tuple[str, str]:
    """(bản dịch, nguồn 'ai' | 'google') — bản AI nếu đã có, chưa có thì Google và bắt đầu nhờ AI (nếu AI sẵn sàng)."""
    with _lock:
        if q in _ai_cache:
            return _ai_cache[q], "ai"
    try:
        from . import ai
        ready = ai.ready()
    except Exception:
        ready = False
    if ready:
        with _lock:
            start = q not in _ai_busy and q not in _ai_failed
            if start:
                _ai_busy.add(q)
        if start:
            threading.Thread(target=_ai_translate, args=(q,), daemon=True).start()
    return to_vietnamese(q), "google"


def how_to(lines: list[str]) -> str:
    joined = " ".join(lines)
    for keys, how in KINDS:
        if any(k in joined for k in keys):
            return how
    return ""


def _tab_label(url: str) -> str:
    """Tab douyin.com/search/<từ> -> từ đang tìm (để người dùng biết tab nào)."""
    path = urllib.parse.urlsplit(url).path
    if path.startswith("/search/"):
        return urllib.parse.unquote(path[len("/search/"):])
    return path or "douyin.com"


def _pump(b, ms: int) -> None:
    """Chờ mà vẫn cho Playwright (bản đồng bộ) xử lý sự kiện: time.sleep chặn luồng -> khung captcha mới (iframe tiến
    trình riêng, đổi câu sau 刷新 / trả lời sai) không bao giờ hiện trong b.frames."""
    pages = [p for c in b.contexts for p in c.pages] if b is not None else []
    if pages:
        try:
            pages[0].wait_for_timeout(ms)
            return
        except Exception:
            pass
    time.sleep(ms / 1000)


def _read(b) -> list[dict]:
    """Chữ của khung captcha trên từng tab douyin.com (chỉ đọc)."""
    out = []
    for p in [p for c in b.contexts for p in c.pages if "douyin.com" in p.url]:
        for f in p.frames:
            if "verifycenter/captcha" not in f.url:
                continue
            try:            # dòng có chữ Hán, trừ chữ trên nút (bỏ cả mã số "20261126…" ở góc)
                lines = [s for s in f.evaluate(_JS)
                         if s not in _SKIP and any("一" <= ch <= "鿿" for ch in s)]
            except Exception:
                continue
            if lines:
                out.append({"tab": _tab_label(p.url), "lines": lines[:2]})
            break
    return out


def _translate(tabs: list[dict]) -> list[dict]:
    res = []
    for t in tabs:
        q = t["lines"][0]
        ask = t["lines"][1] if len(t["lines"]) > 1 else ""
        q_vi, by = better_vietnamese(q)
        res.append({"tab": t["tab"], "q": q, "q_vi": q_vi, "by": by, "ask": ask,
                    "ask_vi": to_vietnamese(ask) if ask else "", "how": how_to(t["lines"])})
    return res


def snapshot() -> dict:
    with _lock:
        return {"tabs": list(_state["tabs"]), "at": _state["at"], "err": _state["err"]}


def ensure_watch(data_dir: Path, active) -> None:
    """Bật luồng đọc câu hỏi captcha (nếu chưa chạy). `active()` False -> luồng tự thoát."""
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return
        _thread = threading.Thread(target=_watch, args=(Path(data_dir), active), daemon=True, name="captcha-watch")
        _thread.start()


def _watch(data_dir: Path, active) -> None:
    from . import browsers
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:           # thoát khối = ngắt kết nối CDP (KHÔNG đóng cửa sổ của người dùng)
            b, b_ws = None, None
            while active():
                err, tabs = "", []
                try:
                    choice = browsers.get_choice(data_dir)
                    ws = browsers._running_cdp(browsers.PROFILES_DIR / choice["profile_id"]) if choice else None
                    if not ws:
                        b = None
                        err = "Cửa sổ Douyin đang đóng — bấm Mở cửa sổ Douyin"
                    else:
                        if b is None or b_ws != ws or not b.is_connected():
                            b, b_ws = pw.chromium.connect_over_cdp(ws), ws      # 1 kết nối dùng lại, không mở mới mỗi 3 s
                            _pump(b, 1000)      # vừa nối: khung captcha (iframe tiến trình riêng) chưa kịp hiện trong b
                        tabs = _translate(_read(b))
                        if not tabs:
                            err = ("Chưa thấy captcha trong cửa sổ Douyin — tìm thử 1 từ trên douyin.com "
                                   "(captcha hiện khi tìm kiếm), hoặc bạn đã giải xong: bấm Đã giải xong")
                except Exception as e:      # trình duyệt vừa đóng / đang tải trang
                    b = None
                    err = f"Chưa đọc được captcha ({type(e).__name__}) — thử lại sau vài giây"
                with _lock:
                    _state.update(tabs=tabs, at=time.time(), err=err)
                _pump(b, 3000)
    finally:
        with _lock:
            _state.update(tabs=[], err="")
