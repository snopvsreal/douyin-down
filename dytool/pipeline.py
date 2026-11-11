"""Ghép các bước: quét -> tải -> edit -> đăng, có lọc trùng và giới hạn tần suất đăng."""

import random
import time
import traceback

from rich.console import Console

from . import subtitles
from .db import DB
from .douyin import DouyinClient, DouyinError
from .editor import edit
from .facebook import FacebookPage

con = Console()


class Pipeline:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.data = cfg["_data"]
        self.db = DB(self.data / "dytool.db")
        self.dy = DouyinClient(cfg["douyin"])

    # ---------- 1. quét video mới ----------
    def _passes(self, v: dict) -> bool:
        f = self.cfg["douyin"].get("filters", {})
        return (v["likes"] >= f.get("min_likes", 0)
                and f.get("min_duration", 0) <= v["duration"] <= f.get("max_duration", 10_000))

    def _collect(self, items: list[dict], source: str) -> int:
        new = 0
        for v in items:
            if self.db.exists(v["id"]):
                continue
            self.db.add(v, source)
            if not self._passes(v):
                self.db.update(v["id"], status="skipped", error="không qua bộ lọc")
                continue
            new += 1
        return new

    def scan(self) -> int:
        dcfg = self.cfg["douyin"]
        total = 0
        for ch in dcfg.get("channels") or []:
            try:
                n = self._collect(self.dy.channel_videos(ch["url"], ch.get("max_age_days", 3)),
                                  f"channel:{ch.get('name', ch['url'])}")
                con.print(f"[cyan]Kênh {ch.get('name')}[/]: {n} video mới")
                total += n
            except DouyinError as e:
                con.print(f"[red]Kênh {ch.get('name')}: {e}[/]")
            time.sleep(random.uniform(3, 8))   # giãn cách để tránh bị chặn
        for kw in dcfg.get("keywords") or []:
            try:
                items = self.dy.search_videos(kw["keyword"], kw.get("max_results", 20),
                                              kw.get("publish_time", 0), kw.get("sort_type", 0))
                n = self._collect(items, f"keyword:{kw['keyword']}")
                con.print(f"[cyan]Từ khóa {kw['keyword']}[/]: {n} video mới")
                total += n
            except DouyinError as e:
                con.print(f"[red]Từ khóa {kw['keyword']}: {e}[/]")
            time.sleep(random.uniform(3, 8))
        return total

    # ---------- 2. tải ----------
    def download(self, limit: int = 50) -> None:
        for row in self.db.by_status("new", limit):
            dest = self.data / "raw" / f"{row['id']}.mp4"
            try:
                self.dy.download(row["url"], dest)
                self.db.update(row["id"], status="downloaded", raw_path=str(dest))
                con.print(f"[green]Đã tải[/] {row['id']}")
            except Exception as e:  # link CDN hết hạn -> lấy lại link qua API ở lần quét sau
                self.db.update(row["id"], status="failed", error=f"download: {e}")
                con.print(f"[red]Lỗi tải {row['id']}: {e}[/]")

    # ---------- 3. edit ----------
    def edit(self, limit: int = 50) -> None:
        ecfg = self.cfg["edit"]
        for row in self.db.by_status("downloaded", limit):
            src = self.data / "raw" / f"{row['id']}.mp4"
            dst = self.data / "edited" / f"{row['id']}.mp4"
            try:
                con.print(f"[yellow]Đang edit[/] {row['id']} ...")
                info = edit(src, dst, self.data / "work", ecfg)
                desc_vi = subtitles.translate_desc(row["desc"] or "", ecfg.get("subtitles", {}))
                self.db.update(row["id"], status="edited", edited_path=str(dst), desc_vi=desc_vi)
                con.print(f"[green]Xong edit[/] {row['id']} ({info['sub_lines']} câu sub)")
            except Exception as e:
                self.db.update(row["id"], status="failed", error=f"edit: {e}")
                con.print(f"[red]Lỗi edit {row['id']}: {e}[/]")
                traceback.print_exc()

    # ---------- 4. đăng ----------
    def can_post_now(self) -> bool:
        fb = self.cfg["facebook"]
        now = int(time.time())
        if self.db.uploads_since(now - 86400) >= fb.get("max_posts_per_day", 6):
            return False
        return now - self.db.last_upload() >= fb.get("min_gap_minutes", 90) * 60

    def upload(self, max_posts: int = 1) -> int:
        fb_cfg = self.cfg["facebook"]
        browser = fb_cfg.get("mode") == "browser"
        page = None if browser else FacebookPage(fb_cfg)
        posted = 0
        for row in self.db.by_status("edited", 50):
            if posted >= max_posts or not self.can_post_now():
                break
            caption = fb_cfg.get("caption", "{desc_vi}").format(desc_vi=row["desc_vi"] or "", desc=row["desc"] or "")
            try:
                video = self.data / "edited" / f"{row['id']}.mp4"
                if browser:
                    from . import fb_browser
                    res = fb_browser.publish(self.data, video, caption.strip(), fb_cfg)
                    fb_id = res.get("url", "browser")
                else:
                    fb_id = page.publish(video, caption.strip())
                self.db.update(row["id"], status="uploaded", fb_id=fb_id, uploaded_at=int(time.time()))
                con.print(f"[green]Đã đăng[/] {row['id']} -> FB {fb_id}")
                posted += 1
            except Exception as e:
                self.db.update(row["id"], status="failed", error=f"upload: {e}")
                con.print(f"[red]Lỗi đăng {row['id']}: {e}[/]")
        return posted

    def run_once(self, post: bool = True) -> None:
        self.scan()
        self.download()
        self.edit()
        fb = self.cfg.get("facebook", {})
        if post and (fb.get("page_token") or fb.get("mode") == "browser"):
            self.upload()

    def watch(self, post: bool = True) -> None:
        minutes = self.cfg["douyin"].get("poll_minutes", 20)
        while True:
            con.rule(time.strftime("%H:%M:%S"))
            try:
                self.run_once(post)
            except Exception as e:
                con.print(f"[red]Lỗi vòng lặp: {e}[/]")
            wait = minutes * 60 * random.uniform(0.85, 1.15)
            con.print(f"Nghỉ {wait / 60:.0f} phút ...")
            time.sleep(wait)
