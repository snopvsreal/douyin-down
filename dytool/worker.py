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

from .store import Store, vn_fmt, vn_time, vn_ts

TT_MAX_AHEAD = 10 * 86400 - 3600   # TikTok chỉ hẹn giờ trong 10 ngày tới (chừa 1 giờ)
TT_MIN_AHEAD = 25 * 60             # TikTok cần >= 20 phút (chừa 5 phút cho thời gian tải lên)


def _real_link(url: str | None, platform: str) -> bool:
    return (url or "").startswith("https://www.tiktok.com/@" if platform == "tiktok" else "https://www.facebook.com/")


def _hex_to_ffmpeg(color: str) -> str:
    c = color.strip().lstrip("#")
    return "0x" + c.upper() if len(c) == 6 else "0x0066FF"


MODEL_IDLE_SECONDS = 300


def release_models() -> bool:
    """Bỏ các model AI nạp trong tiến trình (Whisper ~0,5-1,5 GB, RapidOCR, ECAPA nhận nam/nữ) để trả RAM khi rảnh.
    Trước đây giữ mãi tới lúc tắt app. Chỉ đụng module đã nạp (không import thêm); lần edit sau tự nạp lại (vài giây)."""
    import gc
    import sys
    freed = False
    for mod, fn in (("dytool.subtitles", "_whisper"), ("dytool.hardsub", "_ocr"), ("dytool.gender", "_model")):
        f = getattr(sys.modules.get(mod), fn, None)
        if f is not None and f.cache_info().currsize:
            f.cache_clear()
            freed = True
    if freed:
        gc.collect()
        torch = sys.modules.get("torch")
        try:
            if torch is not None and torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
    return freed


class Worker:
    def __init__(self, cfg: dict, store: Store):
        self.cfg = cfg
        self.store = store
        self.data: Path = cfg["_data"]
        self.running = False
        self.current = "Đang dừng"
        self._thread: threading.Thread | None = None
        self._last_scan: dict[int, float] = {}
        self._src_scanned: dict[int, float] = {}    # nguồn id -> lần quét được gần nhất (quét nguồn lâu chưa quét trước)
        # Douyin đòi xác minh (captcha): {"since", "checked"} — tìm kiếm tạm ngừng tới khi người dùng giải xong
        self.captcha: dict | None = None
        self.on_captcha = None      # app (web/app.py) gán: mở cửa sổ nổi hướng dẫn captcha khi Douyin đòi xác minh
        self._canary_ok_at = 0.0                    # lần tìm thử "猫咪" ra video gần nhất (tin 10 phút)
        self._captcha_rotated_at = 0.0              # lần đổi IP proxy Douyin vì captcha gần nhất
        self._dy = None
        self._vs_warned = 0.0
        self._vs_starting = False       # đã ghi log "đang bật VoiceStudio" cho lần bật này
        self._tdl_retry_at = 0.0
        self._last_cleanup = 0.0
        self._last_work = time.time()     # lần cuối làm 1 việc (để trả RAM model AI khi rảnh lâu)
        # chỉ 1 việc nặng / lần trên cả máy (VoiceStudio 1 GPU): vòng chạy tự động và việc bấm tay cùng giữ khoá này
        self._busy = threading.Lock()
        self._manual: list[tuple[int, str]] = []      # (page_id, video_id) người dùng bấm "Edit ngay"
        self._scans: list[int] = []                   # page_id người dùng bấm "Quét nguồn ngay" lúc chạy tự động tắt
        self._manual_lock = threading.Lock()
        self._manual_thread: threading.Thread | None = None
        # bài hẹn giờ qua giờ -> Đã đăng + lấy link bài (start_sync)
        self._sync_thread: threading.Thread | None = None
        self._link_tries: dict[tuple[int, str], int] = {}     # (page_id, video_id) -> số lần đọc chưa thấy bài
        self._link_next: dict[tuple[int, str], float] = {}

    # ---------- điều khiển ----------
    _mutex = None      # khoá toàn máy: chỉ 1 tiến trình dytool được chạy worker (mở app 2 lần, thêm run_ui.bat…)

    @classmethod
    def _claim_machine_lock(cls) -> bool:
        import sys
        if cls._mutex is not None or sys.platform != "win32":
            return True
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.CreateMutexW.restype = ctypes.c_void_p
        h = k32.CreateMutexW(None, True, "Local\\dytool-worker")
        if not h:
            return True                     # không tạo được khoá -> không chặn
        if k32.GetLastError() == 183:       # ERROR_ALREADY_EXISTS: tiến trình dytool khác đang giữ
            k32.CloseHandle(ctypes.c_void_p(h))
            return False
        cls._mutex = h                      # giữ tới khi tiến trình thoát (Windows tự nhả)
        return True

    def start(self):
        if self.running:
            return
        if not self._claim_machine_lock():
            self.store.log("Không bật chạy tự động: đang có 1 dytool khác chạy trên máy này "
                           "(2 worker cùng lúc sẽ đăng trùng). Đóng bản kia rồi bật lại.", "error")
            return
        self.running = True
        if self._thread and self._thread.is_alive():
            # vừa Tạm dừng khi đang làm 1 việc dài: vòng cũ chưa thoát -> dùng tiếp, KHÔNG tạo vòng thứ 2
            self.store.log("Đã bật chạy tự động")
            return
        self._recover_uploading()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        threading.Thread(target=self._warm_backends, daemon=True, name="warm-backends").start()
        self.store.log("Đã bật chạy tự động")

    def _dub_work_pending(self) -> bool:
        """Có video Mới tìm thấy / Đã tải của kênh đang bật + bật lồng tiếng (sắp cần VoiceStudio)."""
        ids = [p["id"] for p in self._pages() if self.store.page_settings(p).get("dub_enabled")]
        if not ids:
            return False
        marks = ",".join("?" * len(ids))
        return self.store.one(f"SELECT 1 FROM videos WHERE status IN ('new', 'downloaded') AND page_id IN ({marks}) "
                              "LIMIT 1", tuple(ids)) is not None

    def _warm_backends(self) -> None:
        """Bắt đầu chạy tự động -> bật sẵn backend ở nền (mở app không bật gì — màn hình khởi động nhanh, máy nhẹ khi
        chưa chạy): backend tải Douyin khi có kênh đang bật; VoiceStudio chỉ khi có video sắp cần lồng tiếng
        (~800 MB RAM + VRAM, nạp model ~2 phút). Không chờ: việc nào cần mà backend chưa sẵn sàng thì tự chờ / để sau."""
        from . import services
        try:
            if self._pages() and not services.tdl_alive(self.cfg):
                threading.Thread(target=services.ensure_tdl, args=(self.cfg,), daemon=True, name="tdl-start").start()
            if self._dub_work_pending():
                services.start_voicestudio(self.cfg)
        except Exception as e:
            self.store.log(f"Bật sẵn backend lỗi: {e}", "error")

    UNSURE = ("dytool bị tắt / mất kết nối khi đang đăng — KIỂM TRA trên {where} xem bài đã lên chưa, "
              "chưa có thì duyệt lại (tool không tự đăng lại để tránh đăng trùng)")

    def _recover_uploading(self) -> None:
        """Lần chạy trước bị tắt giữa lúc đăng (app đóng, cập nhật, mất điện): không biết bài đã lên chưa ->
        trả về Chờ duyệt kèm lời nhắc, KHÔNG tự đăng lại."""
        for v in self.store.q("SELECT v.id, v.page_id, p.platform, p.name FROM videos v "
                              "JOIN pages p ON p.id=v.page_id WHERE v.status='uploading'"):
            where = "TikTok" if v["platform"] == "tiktok" else "Facebook"
            self.store.set_video(v["id"], v["page_id"], status="review", scheduled_at=None,
                                 error=self.UNSURE.format(where=where))
            self.store.log(f"[{v['name']}] {v['id']}: đang đăng thì dytool bị tắt -> trả về chờ duyệt, "
                           f"hãy kiểm tra trên {where}", "error", v["page_id"])
        for cp in self.store.q("SELECT * FROM crossposts WHERE status='uploading'"):
            self.store.set_crosspost(cp["video_id"], cp["src_page_id"], cp["dst_page_id"], status="failed",
                                     error=self.UNSURE.format(where="TikTok"))

    def stop(self):
        self.running = False
        self.current = "Đang dừng"
        self.store.log("Đã tắt chạy tự động")

    # ---------- edit thủ công (nút "Edit ngay") ----------
    def _loop_alive(self) -> bool:
        return self.running and bool(self._thread and self._thread.is_alive())

    def manual_queue(self) -> list[tuple[int, str]]:
        with self._manual_lock:
            return list(self._manual)

    def _ensure_manual_thread(self) -> None:
        """Gọi khi đang giữ _manual_lock: chạy tự động tắt -> mở luồng làm việc bấm tay (nếu chưa có)."""
        if self._loop_alive() or (self._manual_thread and self._manual_thread.is_alive()):
            return
        if not self._claim_machine_lock():
            raise RuntimeError("Đang có 1 dytool khác chạy trên máy này — làm ở bản đó, hoặc đóng bản kia")
        self._manual_thread = threading.Thread(target=self._manual_loop, daemon=True)
        self._manual_thread.start()

    def request_edit(self, page_id: int, video_id: str) -> str:
        """Người dùng bấm Edit 1 video "Đã tải" / "Tìm thấy" (tải về trước rồi edit): đưa vào hàng chờ edit thủ
        công (làm trước video tự chọn, cả khi kênh đang tắt). Chạy tự động đang bật -> vòng lặp nhặt ở lượt kế;
        đang tắt -> mở luồng riêng làm cho xong hàng chờ rồi thôi. Trả 'queued' hoặc 'started'."""
        with self._manual_lock:
            if (page_id, video_id) not in self._manual:
                self._manual.append((page_id, video_id))
            busy = len(self._manual) + len(self._scans) > 1 or self._busy.locked()
            try:
                self._ensure_manual_thread()
            except RuntimeError:
                self._manual.remove((page_id, video_id))
                raise
        return "queued" if busy else "started"

    def edit_state(self, page_id: int, video_id: str) -> str:
        """'running' (đang tải / edit video này) / 'queued' (trong hàng chờ Edit ngay) / ''."""
        from . import progress
        t = progress.snapshot()
        if t and t.get("kind") in ("edit", "download") and t.get("video") == video_id and not t.get("stopping"):
            return "running"
        with self._manual_lock:
            return "queued" if (page_id, video_id) in self._manual else ""

    def clear_manual(self) -> None:
        with self._manual_lock:
            self._manual.clear()
            self._scans.clear()

    def cancel_manual(self, page_id: int, video_id: str) -> bool:
        with self._manual_lock:
            if (page_id, video_id) in self._manual:
                self._manual.remove((page_id, video_id))
                return True
        return False

    def _take_manual(self):
        """Lấy việc edit thủ công kế tiếp còn hợp lệ (video vẫn "Đã tải" còn file gốc, hoặc "Tìm thấy" = tải trước)
        -> (page, row) hoặc None."""
        while True:
            with self._manual_lock:
                if not self._manual:
                    return None
                pid, vid = self._manual.pop(0)
            page = self.store.one("SELECT p.*, a.id acc FROM pages p JOIN accounts a ON a.id=p.account_id "
                                  "WHERE p.id=?", (pid,))
            row = self.store.one("SELECT * FROM videos WHERE id=? AND page_id=?", (vid, pid))
            if page and row and (row["status"] == "new" or (
                    row["status"] == "downloaded" and (self.data / "raw" / f"{vid}.mp4").exists())):
                return page, row

    def _take_scan(self):
        """Kênh người dùng bấm "Quét nguồn ngay" lúc chạy tự động tắt -> page hoặc None."""
        while True:
            with self._manual_lock:
                if not self._scans:
                    return None
                pid = self._scans.pop(0)
            page = self.store.one("SELECT p.*, a.id acc FROM pages p JOIN accounts a ON a.id=p.account_id "
                                  "WHERE p.id=?", (pid,))
            if page:
                return page

    def _manual_loop(self):
        """Luồng edit thủ công khi chạy tự động đang tắt. Chạy tự động bật lên giữa chừng -> để vòng lặp làm tiếp."""
        from . import progress
        self._keep_awake(True)
        try:
            while not self._loop_alive():
                with self._busy:
                    job = self._take_manual()
                    scan = None if job else self._take_scan()
                    if not job and not scan:
                        break
                    try:
                        if job:
                            self._run_manual(*job)
                        else:
                            self.store.log(f"[{scan['name']}] Quét nguồn thủ công", page_id=scan["id"])
                            self._tdl_retry_at = 0      # người dùng bấm: thử bật backend tải lại ngay
                            self._scan(scan)
                    except progress.Cancelled:
                        pass
                    except Exception as e:
                        what = "edit" if job else "quét nguồn"
                        self.store.log(f"Lỗi {what} thủ công: {e}", "error")
                        traceback.print_exc()
                    finally:
                        stopped = progress.cancelled()
                        progress.end()
                    if stopped:             # bấm Dừng -> bỏ luôn các việc còn trong hàng chờ bấm tay
                        with self._manual_lock:
                            self._manual.clear()
                            self._scans.clear()
                        break
        finally:
            self._keep_awake(False)
            if not self._loop_alive():
                self.current = "Đang dừng"
                release_models()          # hết việc bấm tay, chạy tự động đang tắt -> trả RAM model AI ngay

    def _run_manual(self, page, row):
        """Edit 1 video người dùng bấm. Kênh bật lồng tiếng: VoiceStudio chưa chạy thì bật và CHỜ (tối đa 5 phút) —
        khác vòng tự động (không chờ, làm việc khác trước) vì đây là việc người dùng đang đợi."""
        from . import progress, services
        if row["status"] == "new":          # video "Tìm thấy": tải về trước rồi edit luôn
            self.store.log(f"[{page['name']}] Edit ngay {row['id']}: tải video trước", page_id=page["id"])
            self._tdl_retry_at = 0              # người dùng bấm: thử bật backend tải lại ngay
            self._download(page, row)
            if progress.cancelled():
                return
            row = self.store.one("SELECT * FROM videos WHERE id=? AND page_id=?", (row["id"], page["id"]))
            if not row or row["status"] != "downloaded":
                if row and row["status"] == "new":     # backend tải Douyin chưa bật được
                    self.store.log(f"[{page['name']}] Không tải được {row['id']}: backend tải Douyin chưa chạy "
                                   f"(xem data/tdl_server.log) — video vẫn ở Tìm thấy", "error", page["id"])
                return
        if self.store.page_settings(page)["dub_enabled"]:
            progress.begin("edit", "Edit + lồng tiếng", page["name"], row["id"], row["desc"] or "")
            self.current = f"Đang bật giọng nói để edit {row['id']}"
            deadline = time.time() + 300
            while True:
                r = services.start_voicestudio(self.cfg)
                if r == "ok":
                    break
                if r != "starting" or time.time() > deadline:
                    why = {"missing": "chưa có model giọng nói (đang tải ở nền — xem trang Giọng nói)",
                           "crashed": "máy chủ giọng nói bật lên rồi tắt ngay (xem Nhật ký ở trang Giọng nói)",
                           "starting": "máy chủ giọng nói bật quá 5 phút chưa xong"}.get(r, "máy chủ giọng nói không trả lời")
                    self.store.log(f"[{page['name']}] Không edit được {row['id']}: {why} — video vẫn ở Đã tải",
                                   "error", page["id"])
                    return
                progress.step("Đang bật giọng nói (nạp model)…")
                for _ in range(5):
                    progress.check()
                    time.sleep(1)
        self.store.log(f"[{page['name']}] Edit thủ công {row['id']}", page_id=page["id"])
        self._edit(page, row)

    def edit_file(self, page_id: int, src: Path, dst: Path, label: str = "", should_stop=None) -> None:
        """Trang Giọng nói › Lồng tiếng video: edit + lồng tiếng 1 file người dùng tải lên theo Cài đặt kênh `page_id`
        (không thêm vào hàng video của kênh, không đăng). Chờ việc nặng đang chạy xong (khoá _busy) rồi mới làm; bấm
        Dừng = ném progress.Cancelled. Kênh bật lồng tiếng: bật giọng nói và chờ tối đa 5 phút."""
        from . import progress, services
        from .editor import edit
        page = self.store.one("SELECT * FROM pages WHERE id=?", (page_id,))
        if not page:
            raise RuntimeError("Không có kênh này")
        with self._busy:
            if should_stop and should_stop():        # bấm Dừng lúc còn chờ việc khác xong
                raise progress.Cancelled()
            self._keep_awake(True)
            progress.begin("edit", "Lồng tiếng file", page["name"], "", label or src.name)
            self.current = f"Lồng tiếng file {label or src.name}"
            try:
                ecfg = self.page_edit_cfg(page)
                if ecfg["dub"].get("enabled"):
                    deadline = time.time() + 300
                    while (r := services.start_voicestudio(self.cfg)) != "ok":
                        if r != "starting" or time.time() > deadline:
                            raise RuntimeError({"missing": "Chưa có model giọng nói (đang tải ở nền)",
                                                "crashed": "Máy chủ giọng nói bật lên rồi tắt ngay — xem Nhật ký",
                                                "starting": "Máy chủ giọng nói bật quá 5 phút chưa xong"}.get(
                                r, "Máy chủ giọng nói không trả lời"))
                        progress.step("Đang bật giọng nói (nạp model)…")
                        for _ in range(5):
                            progress.check()
                            time.sleep(1)
                edit(src, dst, self.data / "work", ecfg)
                services.vs_touch()
            finally:
                progress.end()
                self._keep_awake(self.running)
                if not self.running:
                    self.current = "Đang dừng"

    def scan_now(self, page_id: int) -> str:
        """Nút "Quét nguồn ngay": chạy tự động bật -> vòng lặp quét ở lượt kế; tắt -> luồng việc bấm tay quét kênh
        này (cả kênh đang tắt). Trả 'queued' (chờ việc đang chạy) hoặc 'started'."""
        self._last_scan[page_id] = 0
        with self._manual_lock:
            if self._loop_alive():
                return "queued" if self._busy.locked() else "started"
            if page_id not in self._scans:
                self._scans.append(page_id)
            busy = len(self._manual) + len(self._scans) > 1 or self._busy.locked()
            try:
                self._ensure_manual_thread()
            except RuntimeError:
                self._scans.remove(page_id)
                raise
        return "queued" if busy else "started"

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
        from .dub import page_voices
        dub["voices"] = page_voices(s)
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
            release_models()

    def _run_loop(self):
        while self.running:
            from . import progress
            try:
                with self._busy:                    # chờ việc edit thủ công đang chạy (nếu có) xong
                    if not self.running:
                        break
                    did = self._step()
            except progress.Cancelled:              # Dừng lúc đang quét nguồn
                self.store.log("Đã dừng việc đang chạy (quét nguồn)")
                did = False
            except Exception as e:
                self.store.log(f"Lỗi vòng lặp: {e}", "error")
                traceback.print_exc()
                did = False
            progress.end()
            if did:
                self._last_work = time.time()
            elif time.time() - self._last_work > MODEL_IDLE_SECONDS:
                release_models()              # rảnh > 5 phút: trả RAM Whisper / OCR / nhận giọng (nạp lại vài giây)
            if not did:
                from . import services
                try:
                    if services.vs_idle_stop(self.cfg):
                        self.store.log("Rảnh lâu -> tắt máy chủ giọng nói (tự bật lại khi có video cần lồng tiếng)")
                except Exception as e:
                    self.store.log(f"Tắt máy chủ giọng nói lỗi: {e}", "error")
            if not did and time.time() - self._last_cleanup > 6 * 3600:
                self._last_cleanup = time.time()
                try:
                    self._cleanup()
                except Exception as e:
                    self.store.log(f"Dọn file cũ lỗi: {e}", "error")
            if not did:
                self.current = "Đang chờ việc mới"
                for _ in range(15):
                    if not self.running:
                        break
                    time.sleep(1)

    FINAL = ("uploaded", "scheduled", "rejected", "skipped")

    def _cleanup(self) -> None:
        """Dọn file không còn cần (trước không xoá gì -> ổ đĩa đầy dần: WAV tách giọng ~10 MB/phút/video):
        - work/ (tách giọng, TTS, file tạm) cũ hơn 1 ngày
        - raw/<id>.mp4: mọi Page dùng video đó đã xong (đăng / hẹn giờ / bỏ / lọc) từ 3 ngày trước
        - edited/p<page>_<id>.mp4: đã đăng / bỏ 3 ngày trước, hoặc giờ hẹn đã qua 1 ngày; không còn đăng kèm chờ
        - ảnh chụp fb_logs / tt_logs cũ hơn 14 ngày, nhật ký cũ hơn 30 ngày"""
        import shutil
        now = time.time()
        freed = 0

        def rm(p: Path) -> None:
            nonlocal freed
            try:
                if p.is_dir():
                    size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                    shutil.rmtree(p)
                else:
                    size = p.stat().st_size
                    p.unlink()
                freed += size
            except OSError:
                pass        # đang bị dùng -> để lần sau

        work = self.data / "work"
        if work.exists():
            for p in work.iterdir():
                if now - p.stat().st_mtime > 86400:
                    rm(p)
        final = ",".join(f"'{s}'" for s in self.FINAL)
        old = int(now - 3 * 86400)
        for r in self.store.q(f"SELECT id FROM videos GROUP BY id HAVING SUM(status NOT IN ({final}))=0 "
                              "AND MAX(updated_at)<?", (old,)):
            p = self.data / "raw" / f"{r['id']}.mp4"
            if p.exists():
                rm(p)
        for r in self.store.q(
                f"SELECT v.id, v.page_id FROM videos v WHERE ((v.status IN ('uploaded','rejected','skipped') "
                f"AND v.updated_at<?) OR (v.status='scheduled' AND v.scheduled_at<?)) AND NOT EXISTS (SELECT 1 FROM "
                "crossposts c WHERE c.video_id=v.id AND c.src_page_id=v.page_id AND c.status IN ('pending','uploading'))",
                (old, int(now - 86400))):
            p = self.data / "edited" / f"p{r['page_id']}_{r['id']}.mp4"
            if p.exists():
                rm(p)
        for p in (self.data / "thumbs").glob("*.jpg") if (self.data / "thumbs").is_dir() else ():
            if not (self.data / "edited" / f"{p.stem}.mp4").exists() or now - p.stat().st_mtime > 30 * 86400:
                rm(p)          # ảnh thu nhỏ (/media/thumb) của bản edit đã xoá
        for logs in list((self.data / "accounts").glob("*/fb_logs")) + list((self.data / "accounts").glob("*/tt_logs")):
            for p in logs.iterdir():
                if now - p.stat().st_mtime > 14 * 86400:
                    rm(p)
        self.store.x("DELETE FROM logs WHERE ts<?", (int(now - 30 * 86400),))
        if freed > 50 * 1024 * 1024:
            self.store.log(f"Đã dọn file cũ: giải phóng {freed / 1024 ** 3:.2f} GB")

    def _top_video(self, status: str, page_ids: list[int]):
        if not page_ids:
            return None
        marks = ",".join("?" * len(page_ids))
        return self.store.one(f"SELECT * FROM videos WHERE status=? AND page_id IN ({marks}) "
                              "ORDER BY likes DESC LIMIT 1", (status, *page_ids))

    def _has_video(self, status: str, page_ids: list[int]) -> bool:
        return self._top_video(status, page_ids) is not None

    def _pages(self):
        return self.store.q("SELECT p.*, a.id acc FROM pages p JOIN accounts a ON a.id=p.account_id "
                            "WHERE p.enabled=1 ORDER BY p.id")

    def _step(self) -> bool:
        pages = self._pages()
        by_id = {p["id"]: p for p in pages}
        # lên lịch / đăng ngay là thao tác người dùng bấm -> làm cả với Page đang tắt
        all_pages = {p["id"]: p for p in self.store.q("SELECT p.*, a.id acc FROM pages p "
                                                      "JOIN accounts a ON a.id=p.account_id")}
        # 0) Page bật "tự lên lịch": video đã duyệt -> xếp vào khung giờ trống tiếp theo. Chỉ Page ĐANG BẬT
        #    (trước dùng all_pages: tắt Page rồi mà video đã duyệt vẫn bị tự xếp lịch và đăng)
        self._auto_schedule(by_id)
        # 1) gửi lịch lên Facebook/TikTok / đăng ngay / video đã duyệt (nếu kênh còn hạn mức)
        for row in self.store.q("SELECT * FROM videos WHERE status IN ('schedule_pending','publish_now','approved') "
                                "ORDER BY status='schedule_pending' DESC, status='publish_now' DESC, updated_at"):
            page = all_pages.get(row["page_id"]) if row["status"] != "approved" else by_id.get(row["page_id"])
            if page and page["platform"] == "tiktok" and row["status"] == "schedule_pending" \
                    and (row["scheduled_at"] or 0) > time.time() + TT_MAX_AHEAD:
                continue    # TikTok chỉ hẹn giờ tối đa 10 ngày -> chờ tới gần ngày rồi mới gửi lịch
            if page and self._no_profile(page["account_id"]):
                continue    # tài khoản chưa có hồ sơ trình duyệt (vd. vừa chuyển sang trình duyệt nhúng) -> chờ đăng nhập
            if page and (row["status"] != "approved" or self._can_post(page)):
                self._upload(page, row)
                return True
        # 1b) đăng kèm lên TikTok các video Page Facebook đã đăng / đã lên lịch
        cp = self._due_crosspost()
        if cp:
            self._crosspost(cp, all_pages)
            return True
        # 1c) video người dùng bấm "Edit ngay" (cả kênh đang tắt) — trước video tự chọn
        job = self._take_manual()
        if job:
            self._run_manual(*job)
            return True
        scan = self._take_scan()        # bấm "Quét nguồn ngay" lúc chạy tự động còn tắt, rồi bật lên
        if scan:
            self._scan(scan)
            return True
        # 2) edit (Page bật lồng tiếng mà VoiceStudio đang tắt -> để video chờ, không tính là lỗi).
        #    Lọc Page ngay trong SQL: trước lấy 20 video nhiều like nhất rồi mới lọc -> 20 video của Page đã tắt /
        #    đang chờ VoiceStudio chắn mất, video của Page đang bật không bao giờ tới lượt
        edit_ids = list(by_id)
        dub_ids = [i for i in edit_ids if self.store.page_settings(by_id[i])["dub_enabled"]]
        if dub_ids and self._has_video("downloaded", dub_ids) and not self._voicestudio_up():
            edit_ids = [i for i in edit_ids if i not in dub_ids]
        row = self._top_video("downloaded", edit_ids)
        if row:
            self._edit(by_id[row["page_id"]], row)
            return True
        # 3) tải (backend tải hỏng -> chờ rồi mới thử lại, không bật lại liên tục)
        if time.time() >= self._tdl_retry_at:
            row = self._top_video("new", list(by_id))
            if row:
                self._download(by_id[row["page_id"]], row)
                return True
        # 4) quét nguồn đến hạn. Douyin đang đòi xác minh -> thử lại 5 phút / lần (check_captcha tự giãn cách):
        #    người dùng giải xong mà không bấm "Đã giải xong" thì vẫn tự quét tiếp
        if self.captcha:
            self.check_captcha()
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
        for pid, vid in self.manual_queue():
            p = all_pages.get(pid)
            r = self.store.one("SELECT desc FROM videos WHERE id=? AND page_id=?", (vid, pid))
            if p and r:
                out.append({"act": "Edit ngay (bạn bấm)", "page": p["name"], "video": vid, "desc": r["desc"],
                            "when": None})
        for r in self.store.q("SELECT * FROM videos WHERE status IN ('schedule_pending','publish_now','approved') "
                              "ORDER BY status='schedule_pending' DESC, status='publish_now' DESC, updated_at"):
            p = all_pages.get(r["page_id"])
            if not p or (r["status"] == "approved" and r["page_id"] not in pages):
                continue
            act = {"schedule_pending": "Gửi lịch lên " + ("TikTok" if p["platform"] == "tiktok" else "Facebook"),
                   "publish_now": "Đăng ngay", "approved": "Đăng (khi tới lượt theo giới hạn)"}[r["status"]]
            out.append({"act": act, "page": p["name"], "video": r["id"], "desc": r["caption"] or r["desc"],
                        "when": r["scheduled_at"]})
        for cp in self.store.q("SELECT * FROM crossposts WHERE status='pending' ORDER BY COALESCE(scheduled_at,0)"):
            dst = all_pages.get(cp["dst_page_id"])
            if dst:
                out.append({"act": "Đăng kèm lên TikTok", "page": dst["name"], "video": cp["video_id"], "desc": "",
                            "when": cp["scheduled_at"]})
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
            if not self.store.set_video_if(row["id"], page["id"], ("approved",), status="schedule_pending",
                                           scheduled_at=ts):
                continue
            self.store.log(f"[{page['name']}] Tự lên lịch {row['id']} lúc {vn_fmt(ts)}",
                           page_id=page["id"])

    def _can_post(self, page) -> bool:
        """Đăng ngay video đã duyệt nếu Page còn hạn mức: tính theo NGÀY giờ VN, gồm cả bài đã đăng lẫn bài đã hẹn
        giờ trong ngày (trước chỉ đếm bài đã đăng -> trộn đăng ngay + hẹn giờ thì vượt max_posts_per_day), và cách
        bài gần nhất (đã đăng hoặc hẹn giờ) ít nhất min_gap_minutes."""
        import datetime as dt
        s = self.store.page_settings(page)
        now = int(time.time())
        day_start = vn_ts(dt.datetime.combine(vn_time(now).date(), dt.time()))
        n = self.store.one(
            "SELECT COUNT(*) n FROM videos WHERE page_id=? AND ((uploaded_at>=? AND uploaded_at<?) OR "
            "(status IN ('schedule_pending','scheduled') AND scheduled_at>=? AND scheduled_at<?))",
            (page["id"], day_start, day_start + 86400, day_start, day_start + 86400))["n"]
        if n >= int(s["max_posts_per_day"]):
            return False
        gap = float(s["min_gap_minutes"]) * 60
        near = self.store.one(
            "SELECT COUNT(*) n FROM videos WHERE page_id=? AND status IN ('schedule_pending','scheduled') "
            "AND scheduled_at BETWEEN ? AND ?", (page["id"], now - gap, now + gap))["n"]
        return not near and now - self.store.last_upload(page["id"]) >= gap

    def _ensure_backend(self) -> bool:
        from .services import ensure_tdl, tdl_alive
        if tdl_alive(self.cfg):
            return True
        if time.time() < self._tdl_retry_at:      # vừa bật hỏng: chờ hết 10 phút (cả bước tải lẫn quét nguồn)
            return False
        self.current = "Đang bật backend tải Douyin…"
        self.store.log("Backend tải Douyin chưa chạy -> tự bật")
        ok = ensure_tdl(self.cfg)
        if not ok:
            self._tdl_retry_at = time.time() + 600     # chờ 10 phút rồi mới thử lại (trước: bật lại liên tục)
            self.store.log("Không bật được backend tải Douyin (xem data/tdl_server.log) — thử lại sau 10 phút",
                           "error")
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

        def pause(sec: float):          # chờ giữa các lần tìm, bấm Dừng vẫn dừng ngay
            end = time.time() + sec
            while time.time() < end:
                progress.check()
                time.sleep(min(1.0, max(0.0, end - time.time())))

        def fetch(src, params):
            if src["kind"] == "channel":
                return self._douyin().channel_videos(src["value"])      # mọi thời gian
            # mọi thời gian; nguồn cũ còn publish_time / max_age_days trong params thì bỏ qua
            return self._douyin().search_videos(src["value"], int(params.get("max_results", 50)),
                                                int(params.get("sort_type", 1)))

        # Douyin đòi xác minh (captcha) khi tìm dồn dập (đo 2026-11-25: sau ~8–10 lần tìm liền nhau mọi từ khoá trả
        # "搜索结果为空"). Từ khoá rỗng -> tìm thử 1 lần từ chắc có video (`_search_blocked`): cũng rỗng = captcha ->
        # mở cửa sổ douyin.com cho người dùng giải, dừng lượt; có video = từ khoá đó không có video -> quét tiếp.
        # Quét nguồn lâu chưa quét TRƯỚC (nguồn cuối danh sách không bị bỏ đói), giãn cách 8–15 s.
        if self.captcha and not self.check_captcha():
            return                  # vẫn đang đòi xác minh: không tìm (tìm thêm chỉ làm bị chặn lâu hơn)
        srcs = sorted(self.store.q("SELECT * FROM sources WHERE page_id=? AND enabled=1", (page["id"],)),
                      key=lambda r: (self._src_scanned.get(r["id"], 0), r["id"]))
        progress.begin("scan", f"Quét nguồn Douyin", page["name"])
        total, done, empty, stopped_at = 0, 0, [], None
        try:
            for i, src in enumerate(srcs):
                params = json.loads(src["params"] or "{}")
                name = params.get("vi") or src["value"]
                progress.step(f"Quét nguồn {i + 1}/{len(srcs)}: {name}")
                items, err = None, None
                try:
                    items = fetch(src, params)
                except DouyinError as e:
                    err = str(e)
                    if "Không kết nối được" in err:     # backend vừa tắt -> thử lại sau ~1 phút
                        self._last_scan[page["id"]] = time.time() - float(s["poll_minutes"]) * 60 + 60
                        self.store.log(f"[{page['name']}] Nguồn '{name}': {err}", "error", page["id"])
                        stopped_at = i
                        break
                if not items:
                    progress.detail("Douyin trả về rỗng — tìm thử 1 từ chắc có video xem có bị đòi xác minh không")
                    pause(random.uniform(4, 8))
                    if self._search_blocked() and not self._captcha_rotate(pause):
                        self._need_captcha()
                        stopped_at = i
                        break
                    empty.append(name)              # Douyin không có video nào cho từ khoá này
                    self._src_scanned[src["id"]] = time.time()
                    pause(random.uniform(8, 15))
                    continue
                done += 1
                self._src_scanned[src["id"]] = time.time()
                for v in items:
                    if not self.store.add_video(page["id"], v, f"{src['kind']}:{src['value']}"):
                        continue
                    why = self._filter_reason(v, s)
                    if why:
                        self.store.set_video(v["id"], page["id"], status="skipped", error=f"bị lọc: {why}")
                    else:
                        total += 1
                if i + 1 < len(srcs):
                    pause(random.uniform(8, 15))
        finally:
            progress.end()
        msg = f"[{page['name']}] Quét xong {done}/{len(srcs)} nguồn: {total} video mới"
        if empty:
            msg += f" — không có video nào trên Douyin cho: {', '.join(empty)[:300]} (thử từ khoá ngắn hơn)"
        if stopped_at is not None and self.captcha:
            msg += (f" — Douyin đòi xác minh (captcha), dừng; {len(srcs) - stopped_at} nguồn còn lại quét đầu tiên "
                    f"khi giải xong")
        self.store.log(msg, "error" if stopped_at is not None else ("warn" if empty else "info"), page["id"])

    # ---------- Douyin đòi xác minh (captcha) ----------
    CANARY = "猫咪"                  # từ khoá lúc nào cũng có hàng nghìn video: rỗng = bị chặn, không phải từ khoá dở
    CAPTCHA_URL = "https://www.douyin.com/search/%E7%8C%AB%E5%92%AA?type=video"   # trang tìm "猫咪": captcha hiện ở đây

    def _search_blocked(self) -> bool:
        """Tìm thử 1 lần từ chắc có video. True = Douyin đang đòi xác minh (tin kết quả "không chặn" 10 phút)."""
        from .douyin import DouyinError
        if time.time() - self._canary_ok_at < 600:
            return False
        try:
            ok = bool(self._douyin().search_videos(self.CANARY, 10, 1))
        except DouyinError as e:
            if "Không kết nối được" in str(e):
                return False                    # backend tắt, không phải captcha
            ok = False
        if ok:
            self._canary_ok_at = time.time()
        return not ok

    def _captcha_rotate(self, pause) -> bool:
        """Douyin đòi captcha + proxy Douyin là proxy xoay đổi được + bật "đổi IP khi captcha" -> đổi IP rồi tìm thử lại.
        True = hết bị chặn (quét tiếp). Tối đa 1 lần / 10 phút (captcha có thể gắn với cookie chứ không phải IP —
        đổi liên tục chỉ tốn lượt). Nhà cung cấp bắt chờ ≤ 150 s thì chờ rồi đổi."""
        from . import browser_mgr, progress, proxy_rotate
        if time.time() - self._captcha_rotated_at < 600:
            return False
        for attempt in range(2):
            try:
                p = browser_mgr.rotate_douyin_for_captcha()
                break
            except proxy_rotate.RotateError as e:
                if attempt == 0 and e.wait and e.wait <= 150:
                    progress.detail(f"Douyin đòi xác minh — chờ {e.wait} s để đổi IP proxy")
                    pause(e.wait + 2)
                    continue
                self.store.log(f"Douyin đòi xác minh — đổi IP proxy không được: {e}", "warn")
                return False
            except Exception as e:
                self.store.log(f"Douyin đòi xác minh — đổi IP proxy không được: {e}", "warn")
                return False
        if not p:
            return False
        self._captcha_rotated_at = time.time()
        progress.detail("Đã đổi IP proxy — tìm thử lại")
        pause(5)
        self._canary_ok_at = 0.0
        if not self._search_blocked():
            self.store.log(f"Douyin đòi xác minh — đã đổi IP proxy '{p.get('label')}', hết bị chặn, quét tiếp")
            return True
        self.store.log("Douyin đòi xác minh — đã đổi IP proxy nhưng vẫn bị chặn (captcha có thể gắn với tài khoản "
                       "Douyin, không phải IP)", "warn")
        return False

    def _need_captcha(self) -> None:
        if self.captcha:
            return
        now = time.time()
        self.captcha = {"since": now, "checked": now}
        self.store.log("Douyin đòi xác minh (captcha) — đã mở cửa sổ douyin.com, bạn giải xong thì dytool tự quét tiếp "
                       "(hướng dẫn tiếng Việt ở trang Hôm nay)", "error")
        self.open_captcha_window()
        if self.on_captcha:         # app desktop: cửa sổ nổi hướng dẫn + câu hỏi đã dịch cạnh cửa sổ douyin.com
            try:
                self.on_captcha()
            except Exception as e:
                print("Không mở được cửa sổ nổi captcha:", e, flush=True)
        from . import captcha       # đọc + dịch câu hỏi captcha đang hiện (chỉ chữ) cho trang Hôm nay
        captcha.ensure_watch(self.data, lambda: self.captcha is not None)

    def open_captcha_window(self) -> str | None:
        """Mở douyin.com (trang tìm kiếm — captcha hiện ở đây) trong hồ sơ trình duyệt Douyin để người dùng giải.
        Trả lời lỗi (None = đã mở)."""
        from . import browsers
        import urllib.parse
        import urllib.request
        choice = browsers.get_choice(self.data)
        if not choice:
            return "Chưa có hồ sơ trình duyệt Douyin — vào Cài đặt › Douyin, bấm Đăng nhập Douyin"
        # cửa sổ đang mở sẵn tab douyin.com -> đưa tab đó lên trước, không mở thêm tab (mỗi tab 1 captcha riêng, rối)
        ws = browsers._running_cdp(browsers.PROFILES_DIR / choice["profile_id"])
        if ws:
            try:
                port = urllib.parse.urlparse(ws).port
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as r:
                    tabs = [t for t in json.loads(r.read()) if t.get("type") == "page" and "douyin.com" in t.get("url", "")]
                if tabs:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/json/activate/{tabs[-1]['id']}", timeout=3).close()
                    return None
            except (OSError, ValueError, KeyError):
                pass                # không đọc được danh sách tab -> mở tab mới như thường
        try:
            browsers.open_url(choice["profile_id"], self.CAPTCHA_URL)
        except Exception as e:
            self.store.log(f"Không mở được cửa sổ Douyin để xác minh: {e}", "error")
            return f"Không mở được cửa sổ Douyin: {e}"
        return None

    def check_captcha(self, force: bool = False) -> bool:
        """Đang đòi xác minh -> tìm thử lại (tối đa 1 lần / 5 phút, bấm "Đã giải xong" thì ngay). True = đã hết."""
        if not self.captcha:
            return True
        if not force and time.time() - self.captcha["checked"] < 300:
            return False
        self.captcha["checked"] = time.time()
        self._canary_ok_at = 0.0
        if self._search_blocked():
            return False
        self.captcha = None
        self._last_scan.clear()                 # quét tiếp ngay (nguồn chưa quét được đứng đầu)
        self.store.log("Douyin đã hết đòi xác minh — quét nguồn tiếp")
        return True

    @staticmethod
    def _filter_reason(v: dict, s: dict) -> str:
        """Lý do video không qua bộ lọc của kênh ('' = qua) — ghi vào video để biết vì sao bị lọc."""
        why = []
        if v["likes"] < int(s["min_likes"]):
            why.append(f"ít like ({v['likes']:,} < {int(s['min_likes']):,})".replace(",", "."))
        if v["duration"] < float(s["min_duration"]):
            why.append(f"quá ngắn ({v['duration']:.0f} s < {float(s['min_duration']):.0f} s)")
        if v["duration"] > float(s["max_duration"]):
            why.append(f"quá dài ({v['duration']:.0f} s > {float(s['max_duration']):.0f} s)")
        return ", ".join(why)

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
                dest.unlink(missing_ok=True)       # file hỏng: xoá để lần Thử lại tải lại từ đầu (trước: kẹt mãi)
                raise RuntimeError("file tải về không hợp lệ")
            self.store.set_video(row["id"], page["id"], status="downloaded")
        except Exception as e:
            if self._stopped(e, page, row, "tải"):
                dest.with_suffix(".part").unlink(missing_ok=True)   # vẫn "Tìm thấy": bật lại thì tải lại từ đầu
                return
            self.store.set_video(row["id"], page["id"], status="failed", error=f"tải: {e}")
            self.store.log(f"[{page['name']}] Lỗi tải {row['id']}: {e}", "error", page["id"])

    def _voicestudio_up(self) -> bool:
        """VoiceStudio sẵn sàng chưa; chưa thì tự bật backend chạy ngầm (không chờ: nạp mất 15 s – 1 phút, trong lúc
        đó worker làm việc khác, video lồng tiếng chờ vòng sau)."""
        from . import services
        r = services.start_voicestudio(self.cfg)
        if r == "ok":
            self._vs_starting = False
            return True
        if r == "starting":
            if not self._vs_starting:
                self.store.log("Đang bật máy chủ giọng nói để lồng tiếng…")
            self._vs_starting = True
            return False
        self._vs_starting = False
        if time.time() - self._vs_warned > 600:     # nhắc 10 phút/lần, không spam log
            msg = ("Chưa có model giọng nói -> tạm dừng edit các kênh bật lồng tiếng. Đang tải ở nền (3,3 GB) — xem tiến độ "
                   "ở trang Giọng nói." if r == "missing" else
                   "Máy chủ giọng nói bật lên rồi tắt ngay -> 10 phút sau thử lại. Xem Nhật ký ở trang Giọng nói."
                   if r == "crashed" else
                   f"Máy chủ giọng nói ở {services.vs_api(self.cfg)} không trả lời -> tạm dừng edit các kênh bật lồng tiếng.")
            self.store.log(msg, "error")
            self._vs_warned = time.time()
        return False

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
            progress.check()        # bấm Dừng lúc đang dịch (lỗi dịch có thể bị nuốt) -> không đẩy sang chờ đăng
            # chỉ thay đúng {desc_vi} / {desc}: dấu {} khác trong mẫu (hashtag, emoji…) không được làm lỗi .format()
            # — lỗi lúc này là sau khi đã tốn công lồng tiếng xong
            caption = (s["caption_template"].replace("{desc_vi}", desc_vi)
                       .replace("{desc}", row["desc"] or "").strip())
            from . import services
            services.vs_touch()     # vừa lồng tiếng xong: tính giờ rảnh từ lúc này (edit dài hơn hạn tắt khi rảnh)
            status = "approved" if s["auto_publish"] else "review"
            # chỉ ghi khi video vẫn "Đã tải": người dùng bấm Bỏ trong lúc edit thì không được kéo nó về chờ đăng
            if self.store.set_video_if(row["id"], page["id"], ("downloaded",), status=status, caption=caption,
                                       error=None):
                self.store.log(f"[{page['name']}] Edit xong {row['id']} -> "
                               f"{'chờ đăng' if s['auto_publish'] else 'chờ duyệt'}", page_id=page["id"])
            else:
                self.store.log(f"[{page['name']}] {row['id']} đã bị đổi trạng thái trong lúc edit -> bỏ kết quả edit",
                               page_id=page["id"])
        except Exception as e:
            if self._stopped(e, page, row, "edit"):
                dst.unlink(missing_ok=True)          # file xuất dở; video vẫn "Đã tải" -> bật lại thì edit lại
                return
            if "SIGTERM" in str(e) or "SIGKILL" in str(e):
                # bị ngắt do tắt/khởi động lại tool -> làm lại sau, không tính là lỗi
                return                                  # vẫn đang "Đã tải"
            import httpx
            from .dub import DubError
            # tắt VoiceStudio giữa lúc đang đọc thường ra ReadError / RemoteProtocolError, không phải ConnectError
            if isinstance(e, (httpx.HTTPError, DubError)) and not self._voicestudio_up():
                # VoiceStudio bị tắt giữa chừng -> để video chờ (vẫn "Đã tải"), edit lại khi app mở lại
                self.store.log(f"[{page['name']}] máy chủ giọng nói bị tắt khi đang lồng tiếng {row['id']} -> sẽ làm lại",
                               "error", page["id"])
                return
            if not self.store.set_video_if(row["id"], page["id"], ("downloaded",), status="failed", error=f"edit: {e}"):
                return
            self.store.log(f"[{page['name']}] Lỗi edit {row['id']}: {e}", "error", page["id"])
            traceback.print_exc()

    def _upload(self, page, row):
        import datetime as dt
        when = (vn_time(row["scheduled_at"])
                if row["status"] == "schedule_pending" and row["scheduled_at"] else None)
        if when and row["scheduled_at"] < time.time() + 15 * 60:
            # lịch đã quá giờ (vd tool bị tắt lúc đó) -> không tự đăng, trả về chờ duyệt để chọn giờ mới
            if self.store.set_video_if(row["id"], page["id"], (row["status"],), status="review", scheduled_at=None,
                                       error=f"Lịch {when:%H:%M %d/%m} đã quá giờ khi tool chạy lại — hãy chọn giờ mới"):
                self.store.log(f"[{page['name']}] Lịch {row['id']} lúc {when:%H:%M %d/%m} đã quá giờ -> trả về chờ duyệt",
                               "error", page["id"])
            return
        # nhận việc nguyên tử: chuyển sang "Đang đăng" chỉ khi video còn đúng trạng thái lúc đọc (người dùng vừa
        # bấm Huỷ lịch / Bỏ thì thôi). Bị tắt giữa chừng -> lần sau _recover_uploading trả về chờ duyệt
        if not self.store.set_video_if(row["id"], page["id"], (row["status"],), status="uploading"):
            return
        if page["platform"] == "tiktok":
            return self._upload_tiktok(page, row, when)
        self._upload_fb(page, row, when)

    def _tt_headless(self) -> bool:
        # mặc định hiện cửa sổ: TikTok hay chặn trình duyệt chạy ẩn (đổi bằng tiktok.show_browser: false)
        return not self.cfg.get("tiktok", {}).get("show_browser", True)

    def _upload_tiktok(self, page, row, when):
        """Kênh TikTok riêng: đăng / hẹn giờ video đã duyệt của chính kênh đó."""
        from . import progress, tt_browser
        self.current = (f"Hẹn giờ {row['id']} trên TikTok {page['name']} lúc {when:%H:%M %d/%m}" if when
                        else f"Đăng {row['id']} lên TikTok {page['name']}")
        progress.begin("upload", f"Hẹn giờ TikTok lúc {when:%H:%M %d/%m}" if when else "Đăng lên TikTok",
                       page["name"], row["id"], row["caption"] or "")
        video = self.data / "edited" / f"p{page['id']}_{row['id']}.mp4"
        acc_dir = self.store.account_dir(self.data, page["account_id"])
        try:
            tt_browser.publish(acc_dir, video, row["caption"] or "", schedule_at=when, headless=self._tt_headless(),
                               expect_user=page["profile_id"] or "")
            if when:
                self.store.set_video(row["id"], page["id"], status="scheduled", error=None)
                self.store.log(f"[{page['name']}] Đã hẹn giờ trên TikTok {row['id']} lúc {when:%H:%M %d/%m}",
                               page_id=page["id"])
            else:
                self.store.set_video(row["id"], page["id"], status="uploaded", uploaded_at=int(time.time()), error=None)
                self.store.log(f"[{page['name']}] Đã đăng lên TikTok {row['id']}", page_id=page["id"])
        except Exception as e:
            self._upload_failed(page, row, e, "TikTok")

    # ---------- đăng kèm TikTok theo Page Facebook ----------
    def _queue_crosspost(self, page, row, when) -> None:
        dst = int(self.store.page_settings(page).get("tiktok_mirror") or 0)
        if not dst or not self.store.one("SELECT 1 FROM pages WHERE id=? AND platform='tiktok'", (dst,)):
            return
        self.store.add_crosspost(row["id"], page["id"], dst, vn_ts(when) if when else None)
        self.store.log(f"[{page['name']}] Sẽ đăng kèm {row['id']} lên TikTok"
                       + (f" lúc {when:%H:%M %d/%m}" if when else ""), page_id=page["id"])

    def _due_crosspost(self):
        """Lượt đăng kèm tới hạn: đăng ngay (không có giờ / đã tới giờ) hoặc hẹn giờ khi lịch nằm trong 10 ngày.
        Lịch chỉ còn < 25 phút thì chờ tới đúng giờ rồi đăng ngay (TikTok không cho hẹn sát giờ)."""
        now = time.time()
        for cp in self.store.q("SELECT * FROM crossposts WHERE status='pending' ORDER BY COALESCE(scheduled_at,0)"):
            t = cp["scheduled_at"]
            if t is not None and t < now - 15 * 60:
                # quá giờ hẹn lâu (tool tắt lúc đó): không tự đăng muộn — giống quy tắc lịch Facebook quá giờ
                self.store.set_crosspost(cp["video_id"], cp["src_page_id"], cp["dst_page_id"], status="failed",
                                         error=f"Quá giờ hẹn {vn_fmt(t)} khi tool chạy lại — bấm Đăng lại nếu vẫn muốn "
                                               "đăng lên TikTok bây giờ")
                continue
            if t is None or t <= now or TT_MIN_AHEAD <= t - now <= TT_MAX_AHEAD:
                dst = self.store.one("SELECT account_id FROM pages WHERE id=?", (cp["dst_page_id"],))
                if dst and self._no_profile(dst["account_id"]):
                    continue        # kênh TikTok chưa có hồ sơ trình duyệt -> chờ đăng nhập
                return cp
        return None

    def _no_profile(self, account_id: int) -> bool:
        from . import browsers
        return browsers.missing_profile(self.store.account_dir(self.data, account_id))

    def _crosspost(self, cp, pages: dict) -> None:
        import datetime as dt
        from . import progress, tt_browser
        src, dst = pages.get(cp["src_page_id"]), pages.get(cp["dst_page_id"])
        key = (cp["video_id"], cp["src_page_id"], cp["dst_page_id"])
        if not src or not dst:
            self.store.set_crosspost(*key, status="failed", error="Page hoặc kênh TikTok đã bị xoá")
            return
        row = self.store.one("SELECT * FROM videos WHERE id=? AND page_id=?", (cp["video_id"], src["id"]))
        t = cp["scheduled_at"]
        when = vn_time(t) if t and t - time.time() >= TT_MIN_AHEAD else None
        caption = (row["caption"] if row else "") or ""
        self.current = f"Đăng kèm {cp['video_id']} lên TikTok {dst['name']}"
        progress.begin("upload", f"Đăng kèm TikTok — hẹn {when:%H:%M %d/%m}" if when else "Đăng kèm lên TikTok",
                       f"{src['name']} → {dst['name']}", cp["video_id"], caption)
        video = self.data / "edited" / f"p{src['id']}_{cp['video_id']}.mp4"
        acc_dir = self.store.account_dir(self.data, dst["account_id"])
        self.store.set_crosspost(*key, status="uploading")    # bị tắt giữa chừng -> _recover_uploading, không đăng lại
        try:
            tt_browser.publish(acc_dir, video, caption, schedule_at=when, headless=self._tt_headless(),
                               expect_user=dst["profile_id"] or "")
            self.store.set_crosspost(*key, status="scheduled" if when else "uploaded", error=None)
            self.store.log(f"[{dst['name']}] Đăng kèm từ {src['name']}: {cp['video_id']} "
                           + (f"hẹn giờ {when:%H:%M %d/%m}" if when else "đã đăng"), page_id=dst["id"])
        except Exception as e:
            if self._stopped(e, dst, {"id": cp["video_id"]}, "đăng kèm TikTok"):
                self.store.set_crosspost(*key, status="pending", error=None)   # chưa bấm Post: chưa đăng gì
                return
            self.store.set_crosspost(*key, status="failed", error=str(e)[:500])
            self.store.log(f"[{dst['name']}] Lỗi đăng kèm {cp['video_id']} từ {src['name']}: {e}", "error", dst["id"])

    def _upload_fb(self, page, row, when):
        from . import fb_browser
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
            self._upload_failed(page, row, e, "Facebook")
            return
        # ngoài try: lỗi xếp lượt đăng kèm TikTok không được biến bài Facebook ĐÃ ĐĂNG thành "lỗi" (dễ đăng lại)
        try:
            self._queue_crosspost(page, row, when)
        except Exception as e:
            self.store.log(f"[{page['name']}] Không xếp được lượt đăng kèm TikTok cho {row['id']}: {e}",
                           "error", page["id"])

    # ---------- bài hẹn giờ tới giờ -> "Đã đăng" (theo đồng hồ) + lấy link bài ----------
    LINK_DELAY = 180                       # đăng xong 3 phút mới đọc link (Facebook còn xử lý thước phim)
    LINK_RETRY = (30 * 60, 3 * 3600)       # không thấy bài: thử lại sau 30 phút, 3 giờ — rồi thôi
    LINK_DAYS = 3
    _LINK_SQL = ("SELECT v.id, v.page_id, v.caption, v.uploaded_at, v.fb_url, p.platform, p.asset_id, p.profile_id, "
                 "p.name page_name, p.account_id FROM videos v JOIN pages p ON p.id=v.page_id "
                 "WHERE v.status='uploaded' AND v.uploaded_at>? AND v.uploaded_at<=? ")

    def _flip_due(self) -> int:
        """Theo đồng hồ, không mở trình duyệt: bài đã hẹn giờ (Facebook / TikTok / đăng kèm) qua giờ -> Đã đăng.
        Nền tảng tự đăng đúng giờ; link bài lấy sau (fetch_links)."""
        now = int(time.time())
        n = 0
        for v in self.store.q("SELECT v.id, v.page_id, v.scheduled_at, p.name, p.platform FROM videos v "
                              "JOIN pages p ON p.id=v.page_id WHERE v.status='scheduled' AND v.scheduled_at<=?", (now,)):
            if self.store.set_video_if(v["id"], v["page_id"], expect=("scheduled",), status="uploaded",
                                       uploaded_at=v["scheduled_at"]):
                n += 1
                where = "TikTok" if v["platform"] == "tiktok" else "Facebook"
                self.store.log(f"[{v['name']}] Tới giờ hẹn {vn_time(v['scheduled_at']):%H:%M %d/%m}: {v['id']} "
                               f"đã đăng trên {where}", page_id=v["page_id"])
        for cp in self.store.q("SELECT * FROM crossposts WHERE status='scheduled' AND scheduled_at<=?", (now,)):
            self.store.set_crosspost(cp["video_id"], cp["src_page_id"], cp["dst_page_id"], status="uploaded")
            n += 1
        return n

    def _link_candidates(self) -> dict[int, list]:
        """Video đã đăng (3 ngày qua, đăng được 3 phút) chưa có link bài thật, tới lượt đọc: {page_id: [video]}."""
        now = time.time()
        due: dict[int, list] = {}
        for v in self.store.q(self._LINK_SQL, (now - self.LINK_DAYS * 86400, now - self.LINK_DELAY)):
            if _real_link(v["fb_url"], v["platform"]):
                continue
            key = (v["page_id"], v["id"])
            tries = self._link_tries.get(key, 0)
            if tries > len(self.LINK_RETRY) or (tries and now < self._link_next.get(key, 0)):
                continue                                    # đã thử đủ / chưa tới lượt thử lại
            due.setdefault(v["page_id"], []).append(v)
        return due

    def fetch_links(self, page_id: int | None = None) -> int:
        """Mở trình duyệt ẩn (CHỈ ĐỌC) 1 lần / kênh có video cần link: Facebook = bảng Đã đăng trong Business Suite,
        TikTok = TikTok Studio › Nội dung; ghép theo caption + giờ đăng -> lưu link (Facebook: cả giờ đăng thật).
        page_id: chỉ kênh đó, mọi video đã đăng 30 ngày qua chưa có link, bỏ qua lượt chờ (nút Lấy link bài).
        Trả về số video có link mới."""
        from . import fb_browser, tt_browser
        if page_id is None:
            due = self._link_candidates()
        else:
            now = time.time()
            due = {page_id: [v for v in self.store.q(self._LINK_SQL + "AND v.page_id=?",
                                                     (now - 30 * 86400, now, page_id))
                             if not _real_link(v["fb_url"], v["platform"])]}
        n = 0
        for pid, vids in due.items():
            if not vids:
                continue
            v0 = vids[0]
            tt = v0["platform"] == "tiktok"
            where = "TikTok" if tt else "Facebook"
            if self._no_profile(v0["account_id"]):
                continue
            acc_dir = self.store.account_dir(self.data, v0["account_id"])
            # lần đọc cuối của 1 video (hoặc bấm tay): đọc thêm chữ trên dòng để biết nền tảng báo gì nếu không khớp
            last = page_id is not None or any(self._link_tries.get((pid, v["id"]), 0) >= len(self.LINK_RETRY)
                                              for v in vids)
            caps = [(v["caption"] or "").strip() for v in vids] if last else None
            rows: dict = {}
            tt_items: list = []
            try:
                if tt:
                    tt_items = tt_browser.content_items(acc_dir, v0["profile_id"] or "", find=caps, rows=rows)
                    posts = [{"post_id": p["item_id"], "title": p["title"], "created_at": p["post_time"],
                              "state": "SCHEDULED" if p["scheduled"] else "", "url": p["url"]} for p in tt_items]
                else:
                    r = fb_browser.read_tab(acc_dir, v0["asset_id"], v0["page_name"], find=caps)
                    posts, rows = r["posts"], r["rows"]
            except Exception as e:
                self.store.log(f"[{v0['page_name']}] Không đọc được danh sách bài trên {where} để lấy link: {e}",
                               "error", pid)
                posts = None
            found = {} if posts is None else fb_browser.match_posts(
                [{"id": v["id"], "caption": v["caption"] or "", "ref_ts": v["uploaded_at"],
                  "window": (30 * 60, 12 * 3600)} for v in vids], posts)
            for v in vids:
                key = (pid, v["id"])
                post = found.get(v["id"])
                if post and post.get("url"):
                    extra = {} if tt else {"uploaded_at": post["created_at"]}   # giờ Facebook đăng thật
                    if self.store.set_video_if(v["id"], pid, expect=("uploaded",), fb_url=post["url"], error=None,
                                               **extra):
                        n += 1
                    self._link_tries.pop(key, None)
                    continue
                tries = self._link_tries[key] = self._link_tries.get(key, 0) + 1
                if tries <= len(self.LINK_RETRY) and page_id is None:
                    self._link_next[key] = time.time() + self.LINK_RETRY[tries - 1]
                elif posts is not None:
                    self._link_tries[key] = len(self.LINK_RETRY) + 1        # đã báo, không tự thử nữa
                    reason = self._why_missing(v, tt, acc_dir, rows, tt_items)
                    msg = f"Không thấy bài trên {where} sau giờ đăng: {reason}"
                    self.store.set_video_if(v["id"], pid, expect=("uploaded",),
                                            error=msg + f" — kiểm tra trên {where}; bài đã lên thì bấm Lấy link bài")
                    self.store.log(f"[{v['page_name']}] {v['id']}: {msg}", "error", pid)
        return n

    def _why_missing(self, v, tt: bool, acc_dir, rows: dict, tt_items: list) -> str:
        """Bài không có trong danh sách đã đăng: kiểm tra lại theo cách cũ (bài còn nằm ở mục hẹn giờ không) và lấy
        lý do đúng như nền tảng hiện (chữ trên dòng của bài)."""
        from . import fb_browser
        cap = (v["caption"] or "").strip()
        key = fb_browser._norm(cap)[:40]
        if tt:
            it = next((i for i in tt_items if key and fb_browser._norm(i["title"]).startswith(key[:30])), None)
            row = rows.get(cap, "")
            if not it:
                return "TikTok Studio không có video này (TikTok không đăng hoặc đã gỡ bài)"
            bits = []
            if it["scheduled"]:
                bits.append("TikTok vẫn để bài ở trạng thái hẹn giờ, chưa đăng")
            if it["in_review"]:
                bits.append("TikTok đang xét duyệt bài")
            if not it.get("public", True):
                bits.append("bài không ở chế độ công khai")
            return "; ".join(bits or ["TikTok Studio có bài nhưng giờ đăng khác giờ hẹn"]) + \
                (f" (TikTok Studio ghi: {row})" if row else "")
        # Facebook: bài có còn trong mục Đã lên lịch không
        try:
            r = fb_browser.read_tab(acc_dir, v["asset_id"], v["page_name"], tab="scheduled", find=[cap])
        except Exception as e:
            return f"không đọc được mục Đã lên lịch ({e})"
        row = r["rows"].get(cap, "")
        if any(key and fb_browser._norm(p["title"]).startswith(key[:30]) for p in r["posts"]) or row:
            return "Facebook vẫn để bài trong mục Đã lên lịch, chưa đăng" + (f" (Business Suite ghi: {row})" if row else "")
        if rows.get(cap):
            return f"bài có trong mục Đã đăng nhưng giờ đăng khác giờ hẹn (Business Suite ghi: {rows[cap]})"
        return ("không có trong mục Đã đăng lẫn Đã lên lịch của Page — Facebook không đăng bài này "
                "(có thể vướng bản quyền / bị gỡ) hoặc bài đã bị xoá")

    def start_sync(self) -> None:
        """Luồng nền nhẹ (chạy cả khi tắt Chạy tự động). Mỗi phút: đổi bài hẹn giờ đã qua giờ sang Đã đăng (chỉ SQL);
        có video cần link thì mở trình duyệt ẩn lấy link — chỉ khi không có việc nặng nào đang chạy (khoá _busy)."""
        if self._sync_thread and self._sync_thread.is_alive():
            return

        def loop():
            time.sleep(20)
            while True:
                try:
                    self._flip_due()
                    if self._link_candidates() and self._claim_machine_lock() \
                            and self._busy.acquire(blocking=False):
                        try:
                            self.fetch_links()
                        finally:
                            self._busy.release()
                except Exception as e:
                    self.store.log(f"Cập nhật bài đã đăng lỗi: {e}", "error")
                    traceback.print_exc()
                time.sleep(60)
        self._sync_thread = threading.Thread(target=loop, daemon=True, name="post-sync")
        self._sync_thread.start()

    def _stopped(self, e: Exception, page, row, what: str) -> bool:
        """Bấm Dừng (progress.Cancelled, hoặc chương trình con bị nút Dừng tắt nên báo lỗi khác) -> ghi nhật ký, True."""
        from . import progress
        if not (isinstance(e, progress.Cancelled) or progress.cancelled()):
            return False
        self.store.log(f"[{page['name']}] Đã dừng {what} {row['id']}", page_id=page["id"])
        return True

    def _upload_failed(self, page, row, e: Exception, where: str) -> None:
        """Đăng lỗi: có thể lỗi xảy ra SAU khi đã bấm Chia sẻ / Post (mạng chậm, không thấy xác nhận) -> không
        đánh "Lỗi" để bấm Thử lại (edit lại + đăng lại = trùng) mà trả về Chờ duyệt kèm lời nhắc kiểm tra."""
        from . import progress
        if isinstance(e, progress.Cancelled):
            # dừng TRƯỚC khi bấm nút đăng / lên lịch (sau đó progress không cho dừng) -> chắc chắn chưa đăng gì
            self.store.set_video(row["id"], page["id"], status="review", scheduled_at=None,
                                 error=f"Bạn đã bấm Dừng trước khi đăng — chưa có gì lên {where}. Duyệt lại khi muốn đăng")
            self.store.log(f"[{page['name']}] Đã dừng đăng {row['id']} (chưa đăng gì) -> trả về chờ duyệt",
                           page_id=page["id"])
            return
        self.store.set_video(row["id"], page["id"], status="review", scheduled_at=None,
                             error=f"Đăng {where} chưa xong: {str(e)[:300]} — kiểm tra trên {where} xem bài đã lên "
                                   f"chưa; chưa có thì sửa lỗi rồi duyệt lại")
        self.store.log(f"[{page['name']}] Lỗi đăng {where} {row['id']}: {e}", "error", page["id"])
