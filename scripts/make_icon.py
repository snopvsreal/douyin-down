"""Vẽ icon dytool (ô bo góc màu mực + dấu » xanh ngọc = dây chuyền video) -> installer/dytool.ico + web/static/icon.png.
Chạy lại khi đổi màu: uv run python scripts/make_icon.py"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
INK, ACCENT, ACCENT_2 = (23, 32, 48, 255), (76, 195, 232, 255), (10, 126, 164, 255)


def draw(size: int = 1024) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = size * 0.04
    d.rounded_rectangle([pad, pad, size - pad, size - pad], radius=size * 0.22, fill=INK)
    # 2 mũi tên » : mũi sau đậm hơn mũi trước -> cảm giác chuyển động sang phải
    w = size * 0.11            # độ dày nét
    h = size * 0.25            # nửa chiều cao mũi tên
    cy = size / 2
    for x0, col in ((size * 0.26, ACCENT_2), (size * 0.46, ACCENT)):
        tip = x0 + size * 0.26
        d.line([(x0, cy - h), (tip, cy), (x0, cy + h)], fill=col, width=int(w), joint="curve")
        r = w / 2              # bo tròn 2 đầu nét
        for px, py in ((x0, cy - h), (x0, cy + h)):
            d.ellipse([px - r, py - r, px + r, py + r], fill=col)
    return img


big = draw()
sizes = [16, 24, 32, 48, 64, 128, 256]
(ROOT / "installer").mkdir(exist_ok=True)
big.save(ROOT / "installer" / "dytool.ico", sizes=[(s, s) for s in sizes])
big.resize((256, 256), Image.LANCZOS).save(ROOT / "dytool" / "web" / "static" / "icon.png")
big.resize((64, 64), Image.LANCZOS).save(ROOT / "dytool" / "web" / "static" / "favicon.png")
print("Đã ghi installer/dytool.ico, dytool/web/static/icon.png, favicon.png")
