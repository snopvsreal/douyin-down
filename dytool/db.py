"""SQLite lưu trạng thái từng video: new -> downloaded -> edited -> uploaded (hoặc failed/skipped)."""

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id           TEXT PRIMARY KEY,
    source       TEXT,          -- channel:<name> | keyword:<kw>
    desc         TEXT,
    create_ts    INTEGER,
    likes        INTEGER,
    duration     REAL,
    url          TEXT,          -- link tải video gốc
    raw_path     TEXT,
    edited_path  TEXT,
    desc_vi      TEXT,
    fb_id        TEXT,
    status       TEXT DEFAULT 'new',
    error        TEXT,
    meta         TEXT,
    found_at     INTEGER,
    uploaded_at  INTEGER
);
"""


class DB:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def exists(self, vid: str) -> bool:
        return self.conn.execute("SELECT 1 FROM videos WHERE id=?", (vid,)).fetchone() is not None

    def add(self, item: dict, source: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO videos(id, source, desc, create_ts, likes, duration, url, meta, found_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (item["id"], source, item.get("desc", ""), item.get("create_ts"), item.get("likes"),
             item.get("duration"), item.get("url"), json.dumps(item.get("meta", {}), ensure_ascii=False),
             int(time.time())),
        )
        self.conn.commit()

    def update(self, vid: str, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE videos SET {cols} WHERE id=?", (*fields.values(), vid))
        self.conn.commit()

    def by_status(self, status: str, limit: int = 1000) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM videos WHERE status=? ORDER BY likes DESC, found_at LIMIT ?", (status, limit)
        ).fetchall()

    def uploads_since(self, ts: int) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM videos WHERE uploaded_at>=?", (ts,)).fetchone()[0]

    def last_upload(self) -> int:
        return self.conn.execute("SELECT COALESCE(MAX(uploaded_at),0) FROM videos").fetchone()[0]

    def stats(self) -> list[tuple[str, int]]:
        return self.conn.execute("SELECT status, COUNT(*) FROM videos GROUP BY status").fetchall()
