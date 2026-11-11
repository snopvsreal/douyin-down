"""Worker chạy nền cho giao diện web: xử lý tuần tự (1 việc nặng mỗi lúc) cho tất cả Page đang bật.

Thứ tự ưu tiên mỗi vòng:  đăng video đã duyệt  >  edit video đã tải  >  tải video mới  >  quét nguồn đến hạn.
Xử lý tuần tự để VoiceStudio không bị 2 việc tranh GPU (làm chậm 4-6 lần).
"""

import copy
import json
import random
import threading
import time
import traceback
from pathlib import Path

from .store import Store


def _hex_to_ffmpeg(color: str) -> str:
    c = color.strip().lstrip("#")
    return "0x" + c.upper() if len(c) == 6 else "0x0066FF"


class Worker:
    def __init__(self, cfg: dict, store: Store):
        self.cfg = cfg
        self.store = store
        self.data: Path = cfg["_data"]
        self.running = False
        self.current = "Đang dừng"
        self._thread: threading.Thread | None = None
        self._last_scan: dict[int, float] = {}
        self._dy = None

    # ---------- điều khiển ----------
    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.store.log("Đã bật chạy tự động")

    def stop(self):
        self.running = False
        self.current = "Đang dừng"
        self.store.log("Đã tắt chạy tự động")

    def scan_now(self, page_id: int):
        self._last_scan[page_id] = 0

    # ---------- cấu hình theo Page ----------
    def page_edit_cfg(self, page) -> dict:
        s = self.store.page_settings(page)
        e = copy.deepcopy(self.cfg["edit"])
        e["hflip"] = bool(s["hflip"])
        e["rotate_deg"] = float(s["rotate_deg"])
        e["tint"] = {"color": _hex_to_ffmpeg(s["tint_color"]), "opacity": float(s["tint_opacity"])}
        e.setdefault("subtitles", {})["enabled"] = bool(s["subs_enabled"])
        e["text_source"] = s["text_source"]
        dub = e.setdefault("dub", {})
        dub["enabled"] = bool(s["dub_enabled"])
        dub["text_only"] = bool(s["dub_text_only"])
        dub["gender"] = s["gender"]
        seed = int(s["voice_seed"])
        # tên profile giọng gắn với nội dung mô tả -> đổi mô tả/seed thì tự tạo giọng mới
        import hashlib
        tag = hashlib.md5(f"{s['voice_male']}|{s['voice_female']}|{seed}".encode()).hexdigest()[:6]
        dub["voices"] = {
            "male": {"name": f"dy-nam-{tag}", "instruct": s["voice_male"], "seed": seed},
            "female": {"name": f"dy-nu-{tag}", "instruct": s["voice_female"], "seed": seed + 4},
        }
        return e

    def _douyin(self):
        if self._dy is None:
            from .douyin import DouyinClient
            self._dy = DouyinClient(self.cfg["douyin"])
        return self._dy

    # ---------- vòng lặp ----------
    @staticmethod
    def _keep_awake(on: bool) -> None:
        """Windows: không cho máy ngủ khi đang chạy tự động (macOS dùng `caffeinate -i`)."""
        import sys
        if sys.platform == "win32":
            import ctypes
            ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))

    def _loop(self):
        self._keep_awake(True)
        try:
            self._run_loop()
        finally:
            self._keep_awake(False)

    def _run_loop(self):
        while self.running:
            from . import progress
            try:
                did = self._step()
            except Exception as e:
                self.store.log(f"Lỗi vòng lặp: {e}", "error")
                traceback.print_exc()
                did = False
            progress.end()
            if not did:
                self.current = "Đang chờ việc mới"
                for _ in range(15):
                    if not self.running:
                        break
                    time.sleep(1)

    def _pages(self):
        return self.store.q("SELECT p.*, a.id acc FROM pages p JOIN accounts a ON a.id=p.account_id "
                            "WHERE p.enabled=1 ORDER BY p.id")

    def _step(self) -> bool:
        pages = self._pages()
        by_id = {p["id"]: p for p in pages}
        # lên lịch / đăng ngay là thao tác người dùng bấm -> làm cả với Page đang tắt
        all_pages = {p["id"]: p for p in self.store.q("SELECT p.*, a.id acc FROM pages p "
                                                      "JOIN accounts a ON a.id=p.account_id")}
        # 0) Page bật "tự lên lịch": video đã duyệt -> xếp vào khung giờ trống tiếp theo
        self._auto_schedule(all_pages)
        # 1) gửi lịch lên Facebook / đăng ngay / video đã duyệt (nếu Page còn hạn mức)
        for row in self.store.q("SELECT * FROM videos WHERE status IN ('schedule_pending','publish_now','approved') "
                                "ORDER BY status='schedule_pending' DESC, status='publish_now' DESC, updated_at"):
            page = all_pages.get(row["page_id"]) if row["status"] != "approved" else by_id.get(row["page_id"])
            if page and (row["status"] != "approved" or self._can_post(page)):
                self._upload(page, row)
                return True
        # 2) edit
        for row in self.store.q("SELECT * FROM videos WHERE status='downloaded' ORDER BY likes DESC LIMIT 20"):
            if row["page_id"] in by_id:
                self._edit(by_id[row["page_id"]], row)
                return True
        # 3) tải
        for row in self.store.q("SELECT * FROM videos WHERE status='new' ORDER BY likes DESC LIMIT 20"):
            if row["page_id"] in by_id:
                self._download(by_id[row["page_id"]], row)
                return True
        # 4) quét nguồn đến hạn
        for page in pages:
            s = self.store.page_settings(page)
            if time.time() - self._last_scan.get(page["id"], 0) >= float(s["poll_minutes"]) * 60:
                self._scan(page)
                return True
        return False

    def upcoming(self, limit: int = 6) -> list[dict]:
        """Các việc worker sẽ làm tiếp theo, đúng thứ tự ưu tiên của _step()."""
        pages = {p["id"]: p for p in self._pages()}
        all_pages = {p["id"]: p for p in self.store.q("SELECT * FROM pages")}
        out = []
        for r in self.store.q("SELECT * FROM videos WHERE status IN ('schedule_pending','publish_now','approved') "
                              "ORDER BY status='schedule_pending' DESC, status='publish_now' DESC, updated_at"):
            p = all_pages.get(r["page_id"])
            if not p or (r["status"] == "approved" and r["page_id"] not in pages):
                continue
            act = {"schedule_pending": "Gửi lịch lên Facebook", "publish_now": "Đăng ngay",
                   "approved": "Đăng (khi tới lượt theo giới hạn)"}[r["status"]]
            out.append({"act": act, "page": p["name"], "video": r["id"], "desc": r["caption"] or r["desc"],
                        "when": r["scheduled_at"]})
        for st, act in (("downloaded", "Edit + lồng tiếng"), ("new", "Tải video")):
            for r in self.store.q(f"SELECT * FROM videos WHERE status='{st}' ORDER BY likes DESC LIMIT 20"):
                if r["page_id"] in pages:
                    out.append({"act": act, "page": pages[r["page_id"]]["name"], "video": r["id"],
                                "desc": r["desc"], "when": None})
        for p in pages.values():
            s = self.store.page_settings(p)
            due = self._last_scan.get(p["id"], 0) + float(s["poll_minutes"]) * 60
            out.append({"act": "Quét nguồn", "page": p["name"], "video": "", "desc": "", "when": due})
        return out[:limit]

    def backlog(self) -> dict:
        pages = {p["id"] for p in self._pages()}
        cnt = {"new": 0, "downloaded": 0}
        for r in self.store.q("SELECT page_id, status FROM videos WHERE status IN ('new','downloaded')"):
            if r["page_id"] in pages:
                cnt[r["status"]] += 1
        return cnt

    # ---------- các bước ----------
    def _auto_schedule(self, pages: dict) -> None:
        import datetime as dt
        for row in self.store.q("SELECT * FROM videos WHERE status='approved' ORDER BY likes DESC"):
            page = pages.get(row["page_id"])
            if not page:
                continue
            s = self.store.page_settings(page)
            if not s["auto_schedule"]:
                continue
            ts = self.store.next_free_slot(page["id"], s["schedule_slots"], int(s["max_posts_per_day"]))
            if ts is None:
                self.store.log(f"[{page['name']}] Không còn khung giờ trống trong 30 ngày tới", "error", page["id"])
                continue
            self.store.set_video(row["id"], page["id"], status="schedule_pending", scheduled_at=ts)
            self.store.log(f"[{page['name']}] Tự lên lịch {row['id']} lúc {dt.datetime.fromtimestamp(ts):%H:%M %d/%m}",
                           page_id=page["id"])

    def _can_post(self, page) -> bool:
        s = self.store.page_settings(page)
        now = int(time.time())
        if self.store.uploads_since(page["id"], now - 86400) >= int(s["max_posts_per_day"]):
            return False
        return now - self.store.last_upload(page["id"]) >= float(s["min_gap_minutes"]) * 60

    def _ensure_backend(self) -> bool:
        from .services import ensure_tdl, tdl_alive
        if tdl_alive(self.cfg):
            return True
        self.current = "Đang bật backend tải Douyin…"
        self.store.log("Backend tải Douyin chưa chạy -> tự bật")
        ok = ensure_tdl(self.cfg)
        if not ok:
            self.store.log("Không bật được backend tải Douyin (xem data/tdl_server.log)", "error")
        return ok

    def _scan(self, page):
        from .douyin import DouyinError
        self._last_scan[page["id"]] = time.time()
        if not self._ensure_backend():
            self._last_scan[page["id"]] = time.time() - float(self.store.page_settings(page)["poll_minutes"]) * 60 + 60
            return
        s = self.store.page_settings(page)
        self.current = f"Quét nguồn cho {page['name']}"
        from . import progress
        progress.begin("scan", f"Quét nguồn Douyin", page["name"])
        progress.step("Quét các kênh / từ khóa")
        total = 0
        for src in self.store.q("SELECT * FROM sources WHERE page_id=? AND enabled=1", (page["id"],)):
            params = json.loads(src["params"] or "{}")
            try:
                if src["kind"] == "channel":
                    items = self._douyin().channel_videos(src["value"], float(params.get("max_age_days", 3)))
                else:
                    items = self._douyin().search_videos(src["value"], int(params.get("max_results", 20)),
                                                         int(params.get("publish_time", 1)),
                                                         int(params.get("sort_type", 1)))
            except DouyinError as e:
                self.store.log(f"[{page['name']}] Nguồn '{src['value']}': {e}", "error", page["id"])
                if "Không kết nối được" in str(e):     # backend vừa tắt -> thử lại sau ~1 phút
                    self._last_scan[page["id"]] = time.time() - float(s["poll_minutes"]) * 60 + 60
                continue
            for v in items:
                if not self.store.add_video(page["id"], v, f"{src['kind']}:{src['value']}"):
                    continue
                ok = (v["likes"] >= int(s["min_likes"])
                      and float(s["min_duration"]) <= v["duration"] <= float(s["max_duration"]))
                if ok:
                    total += 1
                else:
                    self.store.set_video(v["id"], page["id"], status="skipped", error="không qua bộ lọc")
            time.sleep(random.uniform(3, 8))
        self.store.log(f"[{page['name']}] Quét xong: {total} video mới", page_id=page["id"])
        progress.end()

    def _download(self, page, row):
        self.current = f"Tải video {row['id']} ({page['name']})"
        from . import progress
        progress.begin("download", "Tải video", page["name"], row["id"], row["desc"] or "")
        progress.step("Tải video từ Douyin")
        dest = self.data / "raw" / f"{row['id']}.mp4"
        try:
            if not dest.exists():
                if not self._ensure_backend():
                    return
                try:
                    self._douyin().download(row["url"], dest)
                except Exception as e:
                    # link hết hạn (403/404) -> lấy link mới rồi tải lại 1 lần
                    if not any(c in str(e) for c in ("403", "404", "410")):
                        raise
                    url = self._douyin().fresh_url(row["id"])
                    self.store.set_video(row["id"], page["id"], url=url)
                    self._douyin().download(url, dest)
            if not dest.exists() or dest.stat().st_size < 50_000:
                raise RuntimeError("file tải về không hợp lệ")
            self.store.set_video(row["id"], page["id"], status="downloaded")
        except Exception as e:
            self.store.set_video(row["id"], page["id"], status="failed", error=f"tải: {e}")
            self.store.log(f"[{page['name']}] Lỗi tải {row['id']}: {e}", "error", page["id"])

    def _edit(self, page, row):
        from . import subtitles
        from .editor import edit
        self.current = f"Edit + lồng tiếng {row['id']} ({page['name']})"
        from . import progress
        progress.begin("edit", "Edit + lồng tiếng", page["name"], row["id"], row["desc"] or "")
        src = self.data / "raw" / f"{row['id']}.mp4"
        dst = self.data / "edited" / f"p{page['id']}_{row['id']}.mp4"
        s = self.store.page_settings(page)
        try:
            ecfg = self.page_edit_cfg(page)
            edit(src, dst, self.data / "work", ecfg)
            progress.step("Dịch caption")
            desc_vi = subtitles.translate_desc(row["desc"] or "", ecfg.get("subtitles", {}))
            caption = s["caption_template"].format(desc_vi=desc_vi, desc=row["desc"] or "").strip()
            status = "approved" if s["auto_publish"] else "review"
            self.store.set_video(row["id"], page["id"], status=status, caption=caption, error=None)
            self.store.log(f"[{page['name']}] Edit xong {row['id']} -> {'chờ đăng' if s['auto_publish'] else 'chờ duyệt'}",
                           page_id=page["id"])
        except Exception as e:
            if "SIGTERM" in str(e) or "SIGKILL" in str(e):
                # bị ngắt do tắt/khởi động lại tool -> làm lại sau, không tính là lỗi
                self.store.set_video(row["id"], page["id"], status="downloaded")
                return
            self.store.set_video(row["id"], page["id"], status="failed", error=f"edit: {e}")
            self.store.log(f"[{page['name']}] Lỗi edit {row['id']}: {e}", "error", page["id"])
            traceback.print_exc()

    def _upload(self, page, row):
        from . import fb_browser
        import datetime as dt
        when = (dt.datetime.fromtimestamp(row["scheduled_at"])
                if row["status"] == "schedule_pending" and row["scheduled_at"] else None)
        if when and row["scheduled_at"] < time.time() + 15 * 60:
            # lịch đã quá giờ (vd tool bị tắt lúc đó) -> không tự đăng, trả về chờ duyệt để chọn giờ mới
            self.store.set_video(row["id"], page["id"], status="review", scheduled_at=None,
                                 error=f"Lịch {when:%H:%M %d/%m} đã quá giờ khi tool chạy lại — hãy chọn giờ mới")
            self.store.log(f"[{page['name']}] Lịch {row['id']} lúc {when:%H:%M %d/%m} đã quá giờ -> trả về chờ duyệt",
                           "error", page["id"])
            return
        self.current = (f"Lên lịch {row['id']} trên {page['name']} lúc {when:%H:%M %d/%m}" if when
                        else f"Đăng {row['id']} lên {page['name']}")
        from . import progress
        progress.begin("upload", f"Lên lịch lúc {when:%H:%M %d/%m}" if when else "Đăng lên Facebook",
                       page["name"], row["id"], row["caption"] or "")
        video = self.data / "edited" / f"p{page['id']}_{row['id']}.mp4"
        acc_dir = self.store.account_dir(self.data, page["account_id"])
        try:
            ps = self.store.page_settings(page)
            groups = [g.strip() for g in ps["share_groups"].split(",") if g.strip()]
            res = fb_browser.publish(acc_dir, video, row["caption"] or "",
                                     {"asset_id": page["asset_id"], "page_name": page["name"],
                                      "groups": groups, "group_text": ps["group_text"],
                                      **{k: v for k, v in self.cfg.get("facebook", {}).items()
                                         if k in ("show_browser", "post_on_copyright_issue")}},
                                     schedule_at=when)
            note = None
            if res.get("group_error"):
                note = f"Đã đăng lên Page nhưng KHÔNG chia sẻ được vào nhóm: {res['group_error']}"
                self.store.log(f"[{page['name']}] {note}", "error", page["id"])
            elif res.get("groups"):
                self.store.log(f"[{page['name']}] Chia sẻ kèm vào nhóm: {', '.join(res['groups'])}", page_id=page["id"])
            if when:
                self.store.set_video(row["id"], page["id"], status="scheduled", error=note)
                self.store.log(f"[{page['name']}] Đã lên lịch trên Facebook {row['id']} lúc {when:%H:%M %d/%m}",
                               page_id=page["id"])
            else:
                self.store.set_video(row["id"], page["id"], status="uploaded", uploaded_at=int(time.time()),
                                     fb_url=res.get("url"), error=note)
                self.store.log(f"[{page['name']}] Đã đăng {row['id']}", page_id=page["id"])
        except Exception as e:
            self.store.set_video(row["id"], page["id"], status="failed", error=f"đăng: {e}")
            self.store.log(f"[{page['name']}] Lỗi đăng {row['id']}: {e}", "error", page["id"])
