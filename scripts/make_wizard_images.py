"""Hình cho trình cài dytool (Inno Setup, kiểu windows11): ảnh dọc bên trái trang Chào mừng / Hoàn tất + logo nhỏ
góc trên. Mỗi ảnh 2 bản sáng / tối, 100% và 200% DPI (Inno tự chọn cỡ hợp màn hình).
Chạy lại khi đổi icon: uv run python scripts/make_wizard_images.py  -> installer/wiz-*.png"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_icon import draw  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "installer"
LOGO = draw(1024)

# nền: dải màu đậm của icon (mực) chuyển sang xanh ngọc ở đáy, giống ô logo phóng to
THEMES = {
    "light": ((19, 47, 76), (10, 126, 164)),
    "dark": ((17, 24, 36), (12, 74, 104)),
}


def font(px: int, bold: bool = False) -> ImageFont.ImageFont:
    for name in (("segoeuib.ttf" if bold else "segoeui.ttf"), "arial.ttf"):
        try:
            return ImageFont.truetype(f"C:/Windows/Fonts/{name}", px)
        except OSError:
            continue
    return ImageFont.load_default()


def side(scale: int, theme: str) -> Image.Image:
    """Ảnh dọc 164x314 (x scale)."""
    w, h = 164 * scale, 314 * scale
    top, bot = THEMES[theme]
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = (y / (h - 1)) ** 1.6
        c = tuple(round(top[i] + (bot[i] - top[i]) * t) for i in range(3))
        for x in range(w):
            px[x, y] = c
    d = ImageDraw.Draw(img, "RGBA")
    # vài vệt chéo mờ: gợi "dây chuyền" video chạy qua
    for i, a in enumerate((26, 18, 12)):
        off = (60 + i * 70) * scale
        d.polygon([(0, off), (w, off - 90 * scale), (w, off - 60 * scale), (0, off + 30 * scale)], fill=(255, 255, 255, a))
    s = 88 * scale
    logo = LOGO.resize((s, s), Image.LANCZOS)
    img.paste(logo, ((w - s) // 2, 70 * scale), logo)
    f1, f2 = font(24 * scale, True), font(11 * scale)
    for text, f, y, col in (("dytool", f1, 172 * scale, (255, 255, 255)),
                            ("Douyin → Việt hoá", f2, 206 * scale, (200, 230, 245)),
                            ("→ Facebook · TikTok", f2, 222 * scale, (200, 230, 245))):
        tw = d.textlength(text, font=f)
        d.text(((w - tw) / 2, y), text, font=f, fill=col)
    return img


def small(scale: int) -> Image.Image:
    s = 58 * scale
    return LOGO.resize((s, s), Image.LANCZOS)


if __name__ == "__main__":
    for theme in THEMES:
        for sc in (1, 2):
            side(sc, theme).save(OUT / f"wiz-side-{theme}-{sc}x.png")
    for sc in (1, 2):
        small(sc).save(OUT / f"wiz-small-{sc}x.png")
    print("Đã ghi", ", ".join(p.name for p in sorted(OUT.glob("wiz-*.png"))))
