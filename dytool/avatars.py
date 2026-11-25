"""Ảnh đại diện của kênh (Page Facebook / kênh TikTok) hiện trong app: menu trái, đầu trang kênh, trang Tài khoản.

Lưu ở data/avatars/p<page_id>.png (đã thu về 128×128, mở lại bằng Pillow -> chắc chắn là ảnh). Phục vụ qua
/avatars/<page_id> (app.py) — không mount cả data/.
- Facebook: ảnh công khai của Page qua graph.facebook.com/<id>/picture (không cần đăng nhập / token); thử asset_id
  (Page ID trong Business Suite) rồi profile_id, bỏ ảnh mặc định (is_silhouette).
- TikTok: tt_browser đọc link ảnh lúc đăng nhập (link có chữ ký, hết hạn) -> tải ngay bằng phiên trình duyệt.
"""

import io
import time
from pathlib import Path

SIZE = 128
MAX_BYTES = 5 * 1024 * 1024


def path(data: Path, page_id: int) -> Path:
    return Path(data) / "avatars" / f"p{int(page_id)}.png"


def url(data: Path, page_id: int) -> str:
    """Đường dẫn ảnh cho template (kèm ?v=thời điểm sửa để đổi ảnh là trình duyệt lấy lại); chưa có ảnh -> ''."""
    p = path(data, page_id)
    try:
        return f"/avatars/{int(page_id)}?v={int(p.stat().st_mtime)}"
    except OSError:
        return ""


def save(data: Path, page_id: int, raw: bytes) -> bool:
    """Lưu ảnh (bytes bất kỳ định dạng Pillow đọc được). Không phải ảnh / quá lớn -> False, không ghi gì."""
    if not raw or len(raw) > MAX_BYTES:
        return False
    try:
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(raw))
        im.load()
        im = ImageOps.fit(im.convert("RGBA"), (SIZE, SIZE), Image.LANCZOS)
    except Exception:
        return False
    out = path(data, page_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    im.save(tmp, "PNG", optimize=True)
    tmp.replace(out)
    return True


def fetch_fb(data: Path, page_id: int, ids: list[str]) -> bool:
    """Ảnh Page Facebook công khai. ids: [asset_id, profile_id] — chỉ số."""
    import httpx
    for fid in ids:
        if not (fid or "").isdigit():
            continue
        try:
            r = httpx.get(f"https://graph.facebook.com/{fid}/picture",
                          params={"type": "large", "redirect": "false"}, timeout=15)
            info = r.json().get("data") or {}
            if info.get("is_silhouette") or not str(info.get("url", "")).startswith("https://"):
                continue
            img = httpx.get(info["url"], timeout=20, follow_redirects=True)
            if img.status_code == 200 and save(data, page_id, img.content):
                return True
        except Exception as e:
            print(f"Ảnh Page {fid}: {e}", flush=True)
    return False


def refresh_fb_pages(store, data: Path, only_missing: bool = True, max_age_days: float = 7) -> int:
    """Lấy ảnh cho các Page Facebook (chưa có, hoặc cũ hơn max_age_days). Trả về số ảnh mới lưu."""
    n = 0
    for p in store.q("SELECT id, profile_id, asset_id FROM pages WHERE platform='facebook'"):
        f = path(data, p["id"])
        if f.exists() and (only_missing or time.time() - f.stat().st_mtime < max_age_days * 86400):
            continue
        if fetch_fb(data, p["id"], [p["asset_id"], p["profile_id"]]):
            n += 1
    return n


def refresh_fb_background(store, data: Path, **kw) -> None:
    import threading
    threading.Thread(target=lambda: refresh_fb_pages(store, data, **kw), daemon=True).start()
