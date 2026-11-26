"""Vẽ ảnh cho README: docs/images/banner.png + docs/images/pipeline.png.

Chạy: uv run --no-sync python scripts/make_readme_images.py
Font Be Vietnam Pro (OFL) lấy từ installer/fonts, icon từ dytool/web/static/icon.png.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"
FONTS = ROOT / "installer" / "fonts"
ICON = ROOT / "dytool" / "web" / "static" / "icon.png"

S = 2  # vẽ 2x cho màn hình nét

BG = (17, 24, 36)
BG2 = (22, 33, 48)
LINE = (44, 58, 78)
T1 = (240, 244, 247)
T2 = (160, 174, 190)
T3 = (110, 126, 146)
TEAL = (63, 184, 168)
TEAL_D = (15, 118, 110)
SKY = (78, 196, 236)


def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / f"BeVietnamPro-{weight}.ttf"), size * S)


def glow(img: Image.Image, xy: tuple[int, int], r: int, color: tuple[int, int, int], alpha: int) -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x, y = xy
    d.ellipse((x - r, y - r, x + r, y + r), fill=(*color, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(r // 2))
    img.alpha_composite(layer)


def grid(img: Image.Image, step: int) -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    w, h = img.size
    for x in range(0, w, step):
        d.line((x, 0, x, h), fill=(255, 255, 255, 10), width=S)
    for y in range(0, h, step):
        d.line((0, y, w, y), fill=(255, 255, 255, 10), width=S)
    img.alpha_composite(layer)


def chip(d: ImageDraw.ImageDraw, x: int, y: int, text: str, dot: tuple[int, int, int]) -> int:
    f = font("Medium", 15)
    tw = d.textlength(text, font=f)
    w = int(tw + 46 * S)
    h = 34 * S
    d.rectangle((x, y, x + w, y + h), fill=(*BG2, 255), outline=(*LINE, 255), width=S)
    cy = y + h // 2
    d.ellipse((x + 14 * S, cy - 4 * S, x + 22 * S, cy + 4 * S), fill=dot)
    d.text((x + 32 * S, cy), text, font=f, fill=T1, anchor="lm")
    return w


def banner() -> None:
    W, H = 1280 * S, 400 * S
    img = Image.new("RGBA", (W, H), (*BG, 255))
    grid(img, 40 * S)
    glow(img, (W - 260 * S, 60 * S), 300 * S, TEAL_D, 120)
    glow(img, (W - 80 * S, H), 220 * S, (30, 90, 140), 110)
    d = ImageDraw.Draw(img)

    icon = Image.open(ICON).convert("RGBA").resize((132 * S, 132 * S), Image.LANCZOS)
    img.alpha_composite(icon, (72 * S, 70 * S))

    x0 = 232 * S
    d.text((x0, 66 * S), "dytool", font=font("Bold", 64), fill=T1)
    d.text((x0 + 4 * S, 158 * S), "Douyin  ›  edit  ›  lồng tiếng Việt  ›  Facebook / TikTok",
           font=font("SemiBold", 22), fill=TEAL)
    d.text((x0 + 4 * S, 198 * S), "Tìm video hot, tự edit, phụ đề và đọc lồng tiếng Việt bằng AI,",
           font=font("Regular", 17), fill=T2)
    d.text((x0 + 4 * S, 226 * S), "duyệt rồi đăng theo lịch lên nhiều Page, nhiều kênh — chạy ngay trên máy bạn.",
           font=font("Regular", 17), fill=T2)

    x, y = 76 * S, 300 * S
    for text, dot in (("App Windows 10 / 11", SKY), ("AI chạy trên máy", TEAL), ("Nhiều tài khoản", (240, 180, 80)),
                      ("Lịch đăng tự động", (170, 140, 250)), ("Tự cập nhật", (120, 200, 120))):
        x += chip(d, x, y, text, dot) + 12 * S

    d.line((0, H - S, W, H - S), fill=(*TEAL_D, 255), width=3 * S)
    OUT.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(OUT / "banner.png", optimize=True)


STEPS = [
    ("Quét", "Từ khoá tiếng Việt\ntự dịch sang tiếng Trung,\nkênh Douyin, lọc like"),
    ("Tải", "Bản nét nhất\nqua TikTokDownloader,\nxem trước khi tải"),
    ("Edit", "Che sub Trung, lật,\nnghiêng, phủ màu,\nthu về 1080p"),
    ("Lồng tiếng", "Whisper nghe, AI dịch,\nOmniVoice đọc giọng\nnam / nữ tiếng Việt"),
    ("Duyệt", "Xem video, sửa caption,\nđổi giờ, giữ lại, bỏ\n(có hoàn tác)"),
    ("Đăng", "Reels Facebook, TikTok\nhẹn giờ, đăng kèm,\ntự lấy link bài"),
]


def pipeline() -> None:
    W, H = 1280 * S, 260 * S
    img = Image.new("RGBA", (W, H), (*BG, 255))
    grid(img, 40 * S)
    glow(img, (W // 2, H // 2), 420 * S, TEAL_D, 50)
    d = ImageDraw.Draw(img)

    n = len(STEPS)
    pad, gap = 40 * S, 28 * S
    cw = (W - 2 * pad - gap * (n - 1)) // n
    top, ch = 36 * S, 170 * S
    for i, (title, body) in enumerate(STEPS):
        x = pad + i * (cw + gap)
        accent = TEAL if i < n - 1 else SKY
        d.rectangle((x, top, x + cw, top + ch), fill=(*BG2, 255), outline=(*LINE, 255), width=S)
        d.rectangle((x, top, x + cw, top + 3 * S), fill=accent)
        cx, cy, r = x + 30 * S, top + 40 * S, 15 * S
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=accent)
        d.text((cx, cy), str(i + 1), font=font("Bold", 15), fill=BG, anchor="mm")
        d.text((x + 54 * S, cy), title, font=font("Bold", 19), fill=T1, anchor="lm")
        d.multiline_text((x + 16 * S, top + 72 * S), body, font=font("Regular", 12), fill=T2, spacing=6 * S)
        if i < n - 1:
            ax = x + cw + gap // 2
            ay = top + ch // 2
            d.polygon([(ax - 5 * S, ay - 7 * S), (ax + 5 * S, ay), (ax - 5 * S, ay + 7 * S)], fill=T3)

    d.text((W // 2, H - 26 * S), "Mỗi bước chạy tuần tự trong nền — bạn chỉ cần duyệt video.",
           font=font("Medium", 13), fill=T3, anchor="mm")
    img.convert("RGB").save(OUT / "pipeline.png", optimize=True)


if __name__ == "__main__":
    banner()
    pipeline()
    print("->", OUT)
