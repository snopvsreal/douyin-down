import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

out = Path(sys.argv[1]); out.mkdir(exist_ok=True)
B = "http://127.0.0.1:8010"
PAGES = [("today", "/", "light"), ("today-dark", "/", "dark"), ("calendar", "/calendar", "light"),
         ("channel", "/pages/1", "light"), ("queue", "/queue", "light"), ("channels", "/channels", "light")]
with sync_playwright() as p:
    b = p.chromium.launch()
    for name, url, theme in PAGES + [("review", "/", "light")]:
        ctx = b.new_context(viewport={"width": 1440, "height": 860}, device_scale_factor=2)
        ctx.add_init_script(f"localStorage.setItem('dy-theme', '{theme}')")
        pg = ctx.new_page()
        pg.goto(B + url, wait_until="networkidle")
        pg.wait_for_timeout(4000)
        if name == "review":
            pg.click(".rq-item >> nth=0")
            pg.wait_for_timeout(2500)
        pg.screenshot(path=str(out / f"{name}.png"))
        ctx.close()
    b.close()
print("ok")
