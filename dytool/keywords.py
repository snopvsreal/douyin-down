"""Từ khoá tìm Douyin: người dùng gõ tiếng Việt -> cụm từ tiếng Trung (giản thể) mà người Trung hay gõ để tìm.

Claude (có API key) viết cụm tìm kiếm tự nhiên + 2 gợi ý khác; không có key / lỗi -> dịch máy Google, rồi MyMemory.
Gõ sẵn tiếng Trung thì giữ nguyên. Nhớ kết quả trong bộ nhớ: xem trước lúc gõ và lúc bấm Thêm nguồn dùng chung 1 lần
dịch (không gọi lại, kết quả không đổi giữa 2 lần)."""

import json
import re
import threading

CJK = re.compile(r"[㐀-鿿豈-﫿]")
_cache: dict[str, dict] = {}
_lock = threading.Lock()


def is_chinese(text: str) -> bool:
    return bool(CJK.search(text or ""))


def _norm(text: str) -> str:
    return " ".join((text or "").split()).strip()[:120]


def translate(text: str, cfg: dict) -> dict:
    """-> {"zh": "…", "alt": ["…", …], "by": "claude"|"google"|"", "error": "…"}. Tiếng Trung sẵn -> giữ nguyên."""
    q = _norm(text)
    if not q:
        return {"zh": "", "alt": [], "by": "", "error": ""}
    if is_chinese(q):
        return {"zh": q, "alt": [], "by": "", "error": ""}
    key = q.lower()
    with _lock:
        if key in _cache:
            return _cache[key]
    out = None
    err = ""
    try:
        out = _claude(q, cfg)
    except Exception as e:                      # không có key / mạng / trả sai định dạng -> Google
        err = str(e)
    # dịch máy dự phòng: Google (cổng gtx — deep-translator hay bị Google chặn "too many requests"), rồi MyMemory
    for name, fn in (("google", _google), ("mymemory", _mymemory)):
        if out:
            break
        try:
            zh = _norm(fn(q))
            if is_chinese(zh):
                out = {"zh": zh, "alt": [], "by": name, "error": ""}
        except Exception as e:
            err = err or f"{name}: {e}"
    if not out:
        return {"zh": "", "alt": [], "by": "", "error": err or "không dịch được"}
    with _lock:
        if len(_cache) > 500:
            _cache.clear()
        _cache[key] = out
    return out


def _google(q: str) -> str:
    import httpx
    r = httpx.get("https://translate.googleapis.com/translate_a/single",
                  params={"client": "gtx", "sl": "vi", "tl": "zh-CN", "dt": "t", "q": q}, timeout=10)
    r.raise_for_status()
    return "".join(part[0] for part in r.json()[0] if part and part[0])


def _mymemory(q: str) -> str:
    import httpx
    r = httpx.get("https://api.mymemory.translated.net/get", params={"q": q, "langpair": "vi|zh-CN"}, timeout=10)
    r.raise_for_status()
    return str(r.json()["responseData"]["translatedText"])


def _claude(q: str, cfg: dict) -> dict | None:
    import os
    if not (cfg.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")):
        return None
    from .subtitles import _claude as ask
    prompt = (
        "Người dùng Việt muốn tìm video trên Douyin (TikTok Trung Quốc) theo ý sau:\n"
        f"«{q}»\n\n"
        "Viết cụm từ khoá tìm kiếm bằng TIẾNG TRUNG GIẢN THỂ mà người dùng Douyin thật sẽ gõ để ra đúng loại video đó "
        "(2–6 từ, cách nhau bằng dấu cách, dùng từ lóng / từ phổ biến trên Douyin nếu hợp, không dịch máy từng chữ, "
        "không dấu câu, không hashtag). Thêm 2 cụm khác cũng hợp (khác cách diễn đạt).\n"
        'Chỉ trả về JSON: {"zh": "…", "alt": ["…", "…"]}'
    )
    text = ask(cfg, prompt)
    data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    zh = _norm(str(data.get("zh", "")))
    if not is_chinese(zh):
        return None
    alt = [_norm(str(a)) for a in data.get("alt", []) if is_chinese(str(a)) and _norm(str(a)) != zh][:2]
    return {"zh": zh, "alt": alt, "by": "claude", "error": ""}
