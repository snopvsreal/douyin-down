"""Dữ liệu cho bố cục mới (Hôm nay, Lịch đăng, menu kênh) — tách khỏi app.py cho route gọn. Mọi giờ theo giờ VN
(store.vn_time / vn_ts), không theo múi giờ máy."""

import datetime as dt
import time

from ..store import vn_fmt, vn_time, vn_ts

# màu từng kênh (theo thứ tự id) — dùng chung ở menu, Hôm nay, Lịch đăng
PALETTE = ["#535efd", "#0e9f8e", "#e8164a", "#c66f01", "#7c3aed", "#0284c7", "#db2777", "#65a30d"]
LIVE = ("schedule_pending", "scheduled", "uploading", "approved", "publish_now")


def nav_pages(store) -> list[dict]:
    """Kênh cho menu trái: Page Facebook trước, kênh TikTok sau; màu cố định theo id."""
    rows = store.q("SELECT p.id, p.name, p.platform, p.enabled, a.status acc_status FROM pages p "
                   "JOIN accounts a ON a.id=p.account_id ORDER BY p.platform='tiktok', p.id")
    by_id = sorted(r["id"] for r in rows)
    return [{"id": r["id"], "name": r["name"], "tt": r["platform"] == "tiktok", "enabled": bool(r["enabled"]),
             "color": PALETTE[by_id.index(r["id"]) % len(PALETTE)]} for r in rows]


def colors(store) -> dict[int, str]:
    return {p["id"]: p["color"] for p in nav_pages(store)}


def day_start(d: dt.date) -> int:
    return vn_ts(dt.datetime.combine(d, dt.time()))


def today_items(store) -> list[dict]:
    """Bài của hôm nay theo giờ: đã đăng (uploaded_at hôm nay) + đã / chờ lên lịch hôm nay."""
    t0 = day_start(vn_time(time.time()).date())
    cols = colors(store)
    rows = store.q(
        "SELECT v.id, v.page_id, v.desc, v.caption, v.status, v.scheduled_at, v.uploaded_at, p.name page_name, "
        "p.platform FROM videos v JOIN pages p ON p.id=v.page_id WHERE "
        "(v.uploaded_at>=? AND v.uploaded_at<?) OR (v.status IN ('schedule_pending','scheduled','uploading') "
        "AND v.scheduled_at>=? AND v.scheduled_at<?)", (t0, t0 + 86400, t0, t0 + 86400))
    out = []
    for r in rows:
        ts = r["scheduled_at"] if r["status"] != "uploaded" and r["scheduled_at"] else (r["uploaded_at"] or r["scheduled_at"])
        state, cls = {"uploaded": ("Đã đăng", "ok"), "scheduled": ("Đã hẹn giờ", "info"),
                      "schedule_pending": ("Chờ gửi lịch", "warn"), "uploading": ("Đang đăng", "info")}.get(
            r["status"], (r["status"], ""))
        out.append({"ts": ts, "time": vn_fmt(ts, "%H:%M"), "page_id": r["page_id"], "page": r["page_name"],
                    "tt": r["platform"] == "tiktok", "title": (r["caption"] or r["desc"] or r["id"])[:90],
                    "state": state, "cls": cls, "color": cols.get(r["page_id"], PALETTE[0])})
    return sorted(out, key=lambda x: x["ts"] or 0)


def channel_today(store, page_settings) -> list[dict]:
    """Kênh đang bật: số bài hôm nay (đã đăng + đã / chờ hẹn giờ trong ngày) so với giới hạn/ngày."""
    t0 = day_start(vn_time(time.time()).date())
    out = []
    for p in nav_pages(store):
        if not p["enabled"]:
            continue
        page = store.one("SELECT * FROM pages WHERE id=?", (p["id"],))
        cap = int(page_settings(page)["max_posts_per_day"])
        n = store.one("SELECT COUNT(*) n FROM videos WHERE page_id=? AND ((uploaded_at>=? AND uploaded_at<?) OR "
                      "(status IN ('schedule_pending','scheduled','uploading') AND scheduled_at>=? AND scheduled_at<?))",
                      (p["id"], t0, t0 + 86400, t0, t0 + 86400))["n"]
        out.append({**p, "n": n, "cap": cap, "pct": min(100, round(100 * n / cap)) if cap else 0,
                    "review": store.counts(p["id"]).get("review", 0)})
    return out


# ---------------- Lịch đăng ----------------

def week_start(s: str | None) -> dt.date:
    """Ngày đầu của khung 7 ngày: s (YYYY-MM-DD) nếu có, mặc định HÔM NAY (giờ VN) — xem 7 ngày tới luôn có ích hơn
    tuần T2–CN (Chủ nhật thì cả tuần đã qua)."""
    try:
        return dt.date.fromisoformat(s) if s else vn_time(time.time()).date()
    except ValueError:
        return vn_time(time.time()).date()


def _slots(text: str) -> list[str]:
    out = []
    for t in (text or "").replace(";", ",").split(","):
        t = t.strip()
        try:
            h, m = t.split(":")
            out.append(f"{int(h):02d}:{int(m):02d}")
        except ValueError:
            continue
    return out


def week(store, page_settings, start: dt.date, page_id: int | None = None) -> dict:
    """Lưới tuần: cột = 7 ngày, hàng = khung giờ đăng của các kênh + giờ lẻ của bài trong tuần."""
    cols = colors(store)
    pages = {p["id"]: p for p in nav_pages(store)}
    t0, t1 = day_start(start), day_start(start + dt.timedelta(days=7))
    args = [t0, t1, t0, t1]
    sql = ("SELECT v.id, v.page_id, v.desc, v.caption, v.status, v.scheduled_at, v.uploaded_at FROM videos v WHERE "
           "((v.status IN ('schedule_pending','scheduled','uploading') AND v.scheduled_at>=? AND v.scheduled_at<?) OR "
           "(v.status='uploaded' AND COALESCE(v.scheduled_at, v.uploaded_at)>=? AND COALESCE(v.scheduled_at, v.uploaded_at)<?))")
    if page_id:
        sql += " AND v.page_id=?"
        args.append(page_id)
    rows = store.q(sql, tuple(args))
    times = set()
    for pid, p in pages.items():
        if (not page_id or pid == page_id) and p["enabled"]:
            page = store.one("SELECT * FROM pages WHERE id=?", (pid,))
            times.update(_slots(page_settings(page)["schedule_slots"]))
    cells: dict[tuple[int, str], list] = {}
    now = time.time()
    for r in rows:
        ts = r["scheduled_at"] if r["status"] != "uploaded" or not r["uploaded_at"] else (r["scheduled_at"] or r["uploaded_at"])
        lt = vn_time(ts)
        hm = f"{lt.hour:02d}:{lt.minute - lt.minute % 5:02d}"
        times.add(hm)
        p = pages.get(r["page_id"], {})
        cells.setdefault(((lt.date() - start).days, hm), []).append({
            "id": r["id"], "page_id": r["page_id"], "page": p.get("name", ""), "tt": p.get("tt", False),
            "color": cols.get(r["page_id"], PALETTE[0]), "title": (r["caption"] or r["desc"] or r["id"])[:70],
            "status": r["status"], "done": r["status"] in ("uploaded", "uploading"),
            "movable": r["status"] == "schedule_pending" and ts > now + 20 * 60,
            "when": vn_fmt(ts, "%H:%M %d/%m")})
    today = vn_time(now).date()
    days = []
    for i in range(7):
        d = start + dt.timedelta(days=i)
        days.append({"i": i, "date": d.isoformat(), "dow": "Hôm nay" if d == today else ["T2", "T3", "T4", "T5", "T6", "T7", "CN"][d.weekday()],
                     "day": d.strftime("%d/%m"), "today": d == today, "past": d < today})
    rows_out = []
    for hm in sorted(times):
        rows_out.append({"time": hm, "cells": [
            {"date": days[i]["date"], "time": hm, "posts": cells.get((i, hm), []), "today": days[i]["today"],
             "past": days[i]["past"] or (days[i]["today"] and hm < vn_fmt(now, "%H:%M"))} for i in range(7)]})
    return {"days": days, "rows": rows_out, "count": len(rows),
            "prev": (start - dt.timedelta(days=7)).isoformat(), "next": (start + dt.timedelta(days=7)).isoformat(),
            "label": f"{start.strftime('%d/%m')} – {(start + dt.timedelta(days=6)).strftime('%d/%m/%Y')}"}


def unscheduled(store, page_id: int | None = None) -> list[dict]:
    """Video đã duyệt nhưng chưa có giờ đăng (status approved) — cột phải của Lịch đăng."""
    cols = colors(store)
    rows = store.q("SELECT v.*, p.name page_name, p.platform FROM videos v JOIN pages p ON p.id=v.page_id "
                   "WHERE v.status='approved'" + (" AND v.page_id=?" if page_id else "") + " ORDER BY v.likes DESC",
                   (page_id,) if page_id else ())
    return [{"id": r["id"], "page_id": r["page_id"], "page": r["page_name"], "tt": r["platform"] == "tiktok",
             "title": (r["caption"] or r["desc"] or r["id"])[:70], "color": cols.get(r["page_id"], PALETTE[0])}
            for r in rows]


def auto_place(store, page_settings, page_id: int | None = None) -> tuple[int, int]:
    """Nút "Tự xếp": mọi video đã duyệt chưa có giờ -> khung giờ trống gần nhất của kênh (như Tự lên lịch của worker,
    nhưng chạy ngay và cho cả kênh không bật Tự lên lịch). Trả (đã xếp, không còn chỗ)."""
    done = full = 0
    for r in store.q("SELECT * FROM videos WHERE status='approved'" + (" AND page_id=?" if page_id else "")
                     + " ORDER BY likes DESC", (page_id,) if page_id else ()):
        page = store.one("SELECT * FROM pages WHERE id=?", (r["page_id"],))
        s = page_settings(page)
        ts = store.next_free_slot(page["id"], s["schedule_slots"], int(s["max_posts_per_day"]))
        if ts is None:
            full += 1
            continue
        if store.set_video_if(r["id"], page["id"], ("approved",), status="schedule_pending", scheduled_at=ts):
            store.log(f"[{page['name']}] Xếp lịch {r['id']} lúc {vn_fmt(ts)}", page_id=page["id"])
            done += 1
    return done, full
