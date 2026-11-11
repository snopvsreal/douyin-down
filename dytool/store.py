"""Dữ liệu cho chế độ nhiều tài khoản / nhiều Page (SQLite: data/app.db).

accounts  : tài khoản Facebook, mỗi tài khoản có thư mục phiên riêng data/accounts/<id>/
pages     : Fanpage thuộc 1 tài khoản; settings JSON = mẫu edit/giọng/caption/giới hạn đăng riêng của Page
sources   : nguồn video Douyin của 1 Page (kênh hoặc từ khóa)
videos    : video theo từng Page: new -> downloaded -> review -> approved | scheduled -> uploaded
            (hoặc rejected / failed / skipped)
logs      : nhật ký hiển thị trên giao diện
"""

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    fb_user TEXT,
    status TEXT DEFAULT 'chua_dang_nhap',
    created_at INTEGER
);
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    profile_id TEXT,
    asset_id TEXT,
    enabled INTEGER DEFAULT 0,
    settings TEXT DEFAULT '{}',
    UNIQUE(account_id, asset_id)
);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,              -- channel | keyword
    value TEXT NOT NULL,
    params TEXT DEFAULT '{}',
    enabled INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS videos (
    id TEXT NOT NULL,
    page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    source TEXT,
    desc TEXT,
    create_ts INTEGER,
    likes INTEGER,
    duration REAL,
    url TEXT,
    meta TEXT,
    caption TEXT,
    status TEXT DEFAULT 'new',
    error TEXT,
    fb_url TEXT,
    found_at INTEGER,
    updated_at INTEGER,
    uploaded_at INTEGER,
    PRIMARY KEY (id, page_id)
);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER,
    level TEXT,
    page_id INTEGER,
    msg TEXT
);
"""

# Cài đặt mặc định của 1 Page (ghi đè lên config.yaml)
DEFAULT_PAGE_SETTINGS = {
    "auto_publish": False,          # False = video phải được duyệt trên giao diện mới đăng
    "auto_schedule": False,         # video đã duyệt -> tự lên lịch trên Facebook vào khung giờ trống tiếp theo
    "schedule_slots": "07:00, 11:30, 19:30, 21:00",   # khung giờ đăng mỗi ngày (giờ Việt Nam)
    "share_groups": "",             # tên nhóm (tối đa 3, cách nhau bằng dấu phẩy) mà Page đã tham gia
    "group_text": "",               # lời đăng riêng cho bài trong nhóm (trống = dùng caption)
    "caption_template": "{desc_vi}\n\n#reels #xuhuong",
    "max_posts_per_day": 6,
    "min_gap_minutes": 90,
    "poll_minutes": 20,
    "min_likes": 1000,
    "min_duration": 5,
    "max_duration": 180,
    "hflip": True,
    "tint_color": "#0066FF",
    "tint_opacity": 0.12,
    "rotate_deg": 0,
    "subs_enabled": True,
    "dub_enabled": True,
    "text_source": "auto",          # auto | ocr (phụ đề trên video) | asr (giọng nói)
    "dub_text_only": False,         # lồng tiếng cả video chỉ có chữ, không có lời
    "gender": "auto",
    "voice_male": "male, young adult, moderate pitch",
    "voice_female": "female, young adult, high pitch",
    "voice_seed": 7,
}


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        # nâng cấp CSDL cũ: thêm cột giờ lên lịch
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(videos)")}
        if "scheduled_at" not in cols:
            self.conn.execute("ALTER TABLE videos ADD COLUMN scheduled_at INTEGER")
            self.conn.commit()

    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple = ()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql: str, args: tuple = ()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    # ---- accounts / pages ----
    def account_dir(self, data: Path, account_id: int) -> Path:
        d = data / "accounts" / str(account_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def page_settings(self, page) -> dict:
        return {**DEFAULT_PAGE_SETTINGS, **json.loads(page["settings"] or "{}")}

    def save_page_settings(self, page_id: int, settings: dict) -> None:
        self.x("UPDATE pages SET settings=? WHERE id=?", (json.dumps(settings, ensure_ascii=False), page_id))

    def upsert_page(self, account_id: int, name: str, profile_id: str, asset_id: str) -> None:
        row = self.one("SELECT id FROM pages WHERE account_id=? AND asset_id=?", (account_id, asset_id))
        if row:
            self.x("UPDATE pages SET name=?, profile_id=? WHERE id=?", (name, profile_id, row["id"]))
        else:
            self.x("INSERT INTO pages(account_id, name, profile_id, asset_id) VALUES (?,?,?,?)",
                   (account_id, name, profile_id, asset_id))

    # ---- videos ----
    def add_video(self, page_id: int, v: dict, source: str) -> bool:
        if self.one("SELECT 1 FROM videos WHERE id=? AND page_id=?", (v["id"], page_id)):
            return False
        now = int(time.time())
        self.x("INSERT INTO videos(id, page_id, source, desc, create_ts, likes, duration, url, meta, found_at, updated_at)"
               " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (v["id"], page_id, source, v.get("desc", ""), v.get("create_ts"), v.get("likes"), v.get("duration"),
                v.get("url"), json.dumps(v.get("meta", {}), ensure_ascii=False), now, now))
        return True

    def set_video(self, vid: str, page_id: int, **fields) -> None:
        fields["updated_at"] = int(time.time())
        cols = ", ".join(f"{k}=?" for k in fields)
        self.x(f"UPDATE videos SET {cols} WHERE id=? AND page_id=?", (*fields.values(), vid, page_id))

    def counts(self, page_id: int | None = None) -> dict:
        sql = "SELECT status, COUNT(*) n FROM videos" + (" WHERE page_id=?" if page_id else "") + " GROUP BY status"
        return {r["status"]: r["n"] for r in self.q(sql, (page_id,) if page_id else ())}

    def uploads_since(self, page_id: int, ts: int) -> int:
        return self.one("SELECT COUNT(*) n FROM videos WHERE page_id=? AND uploaded_at>=?", (page_id, ts))["n"]

    def next_free_slot(self, page_id: int, slots: str, per_day: int, min_lead: int = 30 * 60) -> int | None:
        """Khung giờ trống tiếp theo của Page (unix ts): chưa có video nào xếp vào, ngày đó chưa đủ per_day bài,
        cách hiện tại ít nhất min_lead giây. Tìm trong 30 ngày tới."""
        import datetime as dt
        times = sorted({t.strip() for t in slots.replace(";", ",").split(",") if t.strip()})
        hm = []
        for t in times:
            try:
                h, m = t.split(":")
                hm.append((int(h), int(m)))
            except ValueError:
                continue
        if not hm:
            return None
        taken = [r["scheduled_at"] for r in self.q(
            "SELECT scheduled_at FROM videos WHERE page_id=? AND status IN ('schedule_pending','scheduled') "
            "AND scheduled_at IS NOT NULL", (page_id,))]
        taken_set = set(taken)
        today = dt.date.today()
        earliest = time.time() + min_lead
        for d in range(31):
            day = today + dt.timedelta(days=d)
            day_start = int(dt.datetime.combine(day, dt.time()).timestamp())
            used_today = sum(1 for t in taken if day_start <= t < day_start + 86400)
            for h, m in hm:
                if used_today >= per_day:
                    break
                ts = int(dt.datetime.combine(day, dt.time(h, m)).timestamp())
                if ts >= earliest and ts not in taken_set:
                    return ts
        return None

    def last_upload(self, page_id: int) -> int:
        return self.one("SELECT COALESCE(MAX(uploaded_at),0) t FROM videos WHERE page_id=?", (page_id,))["t"]

    # ---- logs ----
    def log(self, msg: str, level: str = "info", page_id: int | None = None) -> None:
        self.x("INSERT INTO logs(ts, level, page_id, msg) VALUES (?,?,?,?)", (int(time.time()), level, page_id, msg))
        print(time.strftime("%H:%M:%S"), level.upper(), msg, flush=True)
