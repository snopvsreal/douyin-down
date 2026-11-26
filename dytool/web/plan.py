"""Dữ liệu cho bố cục mới (Hôm nay, Lịch đăng, menu kênh) — tách khỏi app.py cho route gọn. Mọi giờ theo giờ VN
(store.vn_time / vn_ts), không theo múi giờ máy."""

import datetime as dt
import json
import time

from ..store import vn_fmt, vn_time, vn_ts

# màu từng kênh (theo thứ tự id) — dùng chung ở menu, Hôm nay, Lịch đăng
PALETTE = ["#0f766e", "#2563eb", "#e11d48", "#c2620a", "#7c3aed", "#0891b2", "#db2777", "#65a30d"]
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
        out.append({"ts": ts, "time": vn_fmt(ts, "%H:%M"), "id": r["id"], "page_id": r["page_id"], "page": r["page_name"],
                    "tt": r["platform"] == "tiktok", "title": (r["caption"] or r["desc"] or r["id"])[:90],
                    "state": state, "cls": cls, "color": cols.get(r["page_id"], PALETTE[0])})
    return sorted(out, key=lambda x: x["ts"] or 0)


def channel_list(store, page_settings, only: int | None = None) -> list[dict]:
    """Trang Tất cả kênh: mọi kênh (cả đang tắt) + tài khoản, số bài hôm nay / giới hạn, số video chờ duyệt."""
    t0 = day_start(vn_time(time.time()).date())
    accs = {r["id"]: r["name"] for r in store.q("SELECT id, name FROM accounts")}
    out = []
    for p in nav_pages(store):
        if only is not None and p["id"] != only:
            continue
        page = store.one("SELECT * FROM pages WHERE id=?", (p["id"],))
        n = store.one("SELECT COUNT(*) n FROM videos WHERE page_id=? AND ((uploaded_at>=? AND uploaded_at<?) OR "
                      "(status IN ('schedule_pending','scheduled','uploading') AND scheduled_at>=? AND scheduled_at<?))",
                      (p["id"], t0, t0 + 86400, t0, t0 + 86400))["n"]
        out.append({**p, "account": accs.get(page["account_id"], ""), "n": n,
                    "cap": int(page_settings(page)["max_posts_per_day"]), "review": store.counts(p["id"]).get("review", 0)})
    return out


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


# ---------------- Tổng quan 1 kênh ----------------

def _title(r) -> str:
    return ((r["caption"] or "").split("\n", 1)[0] or r["desc"] or r["id"])[:90]


def channel_overview(store, page_settings, pid: int) -> dict:
    """Trang tổng quan kênh (/pages/<id>): số video từng bước, nhịp đăng 14 ngày qua + 7 ngày tới, sắp đăng,
    mới đăng, lỗi, nguồn (mỗi nguồn đã tìm / đăng được bao nhiêu), nhật ký riêng của kênh."""
    page = store.one("SELECT * FROM pages WHERE id=?", (pid,))
    s = page_settings(page)
    now = time.time()
    today = vn_time(now).date()
    t_today = day_start(today)
    counts = store.counts(pid)

    # nhịp đăng: 14 ngày qua (đã đăng) + hôm nay + 6 ngày tới (đã / chờ hẹn giờ) — 1 cột / ngày giờ VN
    d0 = today - dt.timedelta(days=13)
    t0, t1 = day_start(d0), day_start(today + dt.timedelta(days=7))
    per_day: dict[int, list[int]] = {}
    for r in store.q("SELECT status, scheduled_at, uploaded_at FROM videos WHERE page_id=? AND ("
                     "(status='uploaded' AND COALESCE(uploaded_at, scheduled_at)>=? AND COALESCE(uploaded_at, scheduled_at)<?) OR "
                     "(status IN ('schedule_pending','scheduled','uploading') AND scheduled_at>=? AND scheduled_at<?))",
                     (pid, t0, t1, t0, t1)):
        done = r["status"] == "uploaded"
        ts = (r["uploaded_at"] or r["scheduled_at"]) if done else r["scheduled_at"]
        i = (vn_time(ts).date() - d0).days
        per_day.setdefault(i, [0, 0])[0 if done else 1] += 1
    cap = int(s["max_posts_per_day"]) or 1
    days = []
    for i in range(20):
        d = d0 + dt.timedelta(days=i)
        up, sch = per_day.get(i, [0, 0])
        days.append({"date": d.strftime("%d/%m"), "dow": ["T2", "T3", "T4", "T5", "T6", "T7", "CN"][d.weekday()],
                     "up": up, "sch": sch, "today": d == today, "future": d > today})
    top = max([cap] + [x["up"] + x["sch"] for x in days]) * 1.2      # chừa chỗ trên cùng cho nhãn giới hạn
    for x in days:
        x["h_up"], x["h_sch"] = round(100 * x["up"] / top), round(100 * x["sch"] / top)

    n_today = sum(per_day.get(13, [0, 0]))

    # nguồn: video.source = "<kind>:<value>" (worker._scan)
    by_src = {r["source"]: r for r in store.q(
        "SELECT source, COUNT(*) n, SUM(status='uploaded') up, SUM(status='skipped') skip, MAX(found_at) last "
        "FROM videos WHERE page_id=? GROUP BY source", (pid,))}
    sources = []
    for r in store.q("SELECT * FROM sources WHERE page_id=? ORDER BY enabled DESC, id", (pid,)):
        st = by_src.get(f"{r['kind']}:{r['value']}")
        try:
            vi = json.loads(r["params"] or "{}").get("vi")
        except ValueError:
            vi = None
        sources.append({"kind": r["kind"], "value": r["value"], "vi": vi, "enabled": bool(r["enabled"]),
                        "n": st["n"] if st else 0, "up": (st["up"] or 0) if st else 0,
                        "skip": (st["skip"] or 0) if st else 0, "last": st["last"] if st else None})

    return {
        "counts": counts,
        "queued": sum(counts.get(k, 0) for k in ("approved", "publish_now", "schedule_pending", "uploading")),
        "working": counts.get("new", 0) + counts.get("downloaded", 0),
        "month_n": store.one("SELECT COUNT(*) n FROM videos WHERE page_id=? AND uploaded_at>=?",
                             (pid, int(now) - 30 * 86400))["n"],
        "total_up": counts.get("uploaded", 0),
        "today_n": n_today, "cap": cap, "today_pct": min(100, round(100 * n_today / cap)),
        "days": days, "top": top, "cap_pct": round(100 * cap / top),
        "sources": sources,
        "logs": store.q("SELECT * FROM logs WHERE page_id=? ORDER BY id DESC LIMIT 15", (pid,)),
        "next_slot": vn_fmt(store.next_free_slot(pid, s["schedule_slots"], cap), "%H:%M %d/%m") if s["schedule_slots"] else "",
        "t_today": t_today,
    }


# nhóm trạng thái cho bộ lọc video của trang kênh: (khoá, nhãn, trạng thái, sắp xếp)
VIDEO_GROUPS = [
    ("work", "Đang xử lý", ("new", "downloaded"), "v.updated_at DESC"),
    ("review", "Chờ duyệt", ("review",), "v.updated_at DESC"),
    ("queued", "Chờ đăng", ("approved", "publish_now", "schedule_pending", "uploading"), "COALESCE(v.scheduled_at, 0), v.updated_at DESC"),
    ("scheduled", "Đã hẹn giờ", ("scheduled",), "v.scheduled_at"),
    ("uploaded", "Đã đăng", ("uploaded",), "v.uploaded_at DESC"),
    ("failed", "Lỗi", ("failed",), "v.updated_at DESC"),
    ("dropped", "Đã bỏ / lọc", ("rejected", "skipped"), "v.updated_at DESC"),
]


def channel_videos(store, pid: int, st: str = "", q: str = "", pg: int = 1, per: int = 50) -> dict:
    """Danh sách video của 1 kênh (phần chính của trang kênh): lọc theo nhóm trạng thái + tìm, chia trang."""
    counts = store.counts(pid)
    groups = [{"key": k, "label": l, "n": sum(counts.get(x, 0) for x in sts)} for k, l, sts, _ in VIDEO_GROUPS]
    g = next((x for x in VIDEO_GROUPS if x[0] == st), None)
    conds, args = ["v.page_id=?"], [pid]
    if g:
        conds.append(f"v.status IN ({','.join('?' * len(g[2]))})"); args += list(g[2])
    else:   # "Tất cả" = trừ video bị bỏ / lọc (thường rất nhiều, ít khi cần xem)
        conds.append("v.status NOT IN ('rejected','skipped')")
    if q.strip():
        like = f"%{q.strip()}%"
        conds.append("(v.id LIKE ? OR v.desc LIKE ? OR v.caption LIKE ?)"); args += [like, like, like]
    where = " WHERE " + " AND ".join(conds)
    total = store.one("SELECT COUNT(*) n FROM videos v" + where, tuple(args))["n"]
    pages = max(1, -(-total // per))
    pg = min(max(1, pg), pages)
    rows = store.q("SELECT v.* FROM videos v" + where + " ORDER BY " + (g[3] if g else "v.updated_at DESC")
                   + " LIMIT ? OFFSET ?", (*args, per, (pg - 1) * per))
    return {"groups": groups, "all_n": sum(n for s, n in counts.items() if s not in ("rejected", "skipped")),
            "st": g[0] if g else "", "q": q.strip(), "pg": pg, "pages": pages, "total": total,
            "videos": [{**dict(r), "title": _title(r)} for r in rows]}
