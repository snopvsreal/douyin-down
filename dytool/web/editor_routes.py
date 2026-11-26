"""Mẫu edit của từng kênh (thử nghiệm, kiểu CapCut) — Cài đặt kênh › Edit & lồng tiếng › Mẫu edit.

Mẫu lưu trong cài đặt kênh (`tpl`, đã qua vtemplate.normalize); file tải lên (logo, ảnh / video intro, nhạc) ở
data/templates/assets. Xem trước gần đúng bằng CSS trong trình duyệt; "Render thử" = FFmpeg thật (vtemplate.apply)
trên 1 video đã edit của kênh. Worker áp mẫu sau bước edit chính (editor.edit)."""

from __future__ import annotations

import re
import secrets
import threading
import time
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response

from .. import vtemplate as vt

EDITED_RE = re.compile(r"p\d+_\d{6,30}\.mp4")
RAW_RE = re.compile(r"\d{6,30}")
MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
               "mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm", "mp3": "audio/mpeg",
               "m4a": "audio/mp4", "wav": "audio/wav", "aac": "audio/aac", "ogg": "audio/ogg"}
MAX_UPLOAD = 200 * 1024 * 1024


def samples(store, data: Path, pid: int) -> list[dict]:
    """Video để xem trước, theo thứ tự: đã edit của kênh này · đã tải (chưa edit) của kênh này · đã edit của kênh khác ·
    video mẫu dựng sẵn (vtemplate.demo_video — luôn có, kể cả app vừa cài). Bản đã tải là H.265 thì bỏ qua (WebView2 chỉ
    phát tiếng, không hình). `raw` = chưa qua bước edit chính -> xem trước / Render thử áp thêm lật / màu phủ / nghiêng."""
    from ..editor import video_codec
    names = {r["id"]: r["name"] for r in store.q("SELECT id, name FROM pages")}
    files = [p for p in (data / "edited").glob("p*_*.mp4") if EDITED_RE.fullmatch(p.name)]
    files.sort(key=lambda p: -p.stat().st_mtime)
    mine = [p for p in files if p.name.startswith(f"p{pid}_")][:10]
    others = [p for p in files if not p.name.startswith(f"p{pid}_")][:6]
    out = [{"key": "e:" + p.name, "url": f"/media/edited/{p.name}", "raw": False,
            "label": "Đã edit · " + p.stem.split("_", 1)[1][-6:]} for p in mine]
    n_raw = 0
    for r in store.q("SELECT id FROM videos WHERE page_id=? AND status NOT IN ('new','failed') ORDER BY rowid DESC LIMIT 40", (pid,)):
        p = data / "raw" / f"{r['id']}.mp4"
        if n_raw >= 8 or not RAW_RE.fullmatch(r["id"] or "") or not p.is_file():
            continue
        if video_codec(p) not in ("h264", ""):
            continue
        out.append({"key": "r:" + r["id"], "url": f"/media/raw/{r['id']}", "raw": True,
                    "label": "Chưa edit · " + r["id"][-6:]})
        n_raw += 1
    for p in others:
        ppid = int(p.name[1:p.name.index("_")])
        out.append({"key": "e:" + p.name, "url": f"/media/edited/{p.name}", "raw": False,
                    "label": names.get(ppid, "Kênh khác") + " · " + p.stem.split("_", 1)[1][-6:]})
    if True:         # tạo khi trình phát xin /tpl/demo.mp4 lần đầu (~5 s) — không làm chậm lúc mở cửa sổ
        out.append({"key": "d:demo", "url": "/tpl/demo.mp4", "raw": True, "label": "Video mẫu dựng sẵn (9:16)"})
    return out


def base_of(s: dict) -> dict:
    """Các chỉnh cũ của kênh (lật, màu phủ, nghiêng) — áp ở bước edit chính, sửa chung trong cửa sổ Mẫu edit."""
    return {"hflip": bool(s["hflip"]), "tint_color": s["tint_color"], "tint_opacity": float(s["tint_opacity"]),
            "rotate_deg": float(s["rotate_deg"])}


def register(app, store, data: Path, render) -> None:
    jobs: dict[str, dict] = {}
    jobs_lock = threading.Lock()

    def sample_path(key: str) -> Path | None:
        if key.startswith("e:") and EDITED_RE.fullmatch(key[2:]):
            p = data / "edited" / key[2:]
            return p if p.is_file() else None
        if key.startswith("a:"):
            return vt.asset_path(data, key[2:])
        if key.startswith("r:") and RAW_RE.fullmatch(key[2:]):
            p = data / "raw" / f"{key[2:]}.mp4"
            return p if p.is_file() else None
        if key == "d:demo":
            return vt.demo_video(data)
        return None

    def page_of(pid: int):
        return store.one("SELECT * FROM pages WHERE id=?", (pid,))

    @app.post("/pages/{pid}/tpl")
    async def tpl_save(request: Request, pid: int):
        page = page_of(pid)
        if not page:
            return PlainTextResponse("Không có kênh này", 404)
        try:
            body = await request.json()
        except ValueError:
            return PlainTextResponse("Dữ liệu mẫu không hợp lệ", 400)
        if not isinstance(body, dict):
            return PlainTextResponse("Dữ liệu mẫu không hợp lệ", 400)
        t = vt.normalize(body.get("tpl") or {})
        s = store.page_settings(page)
        s["tpl"] = t
        b = body.get("base") or {}
        if isinstance(b, dict):
            if "hflip" in b:
                s["hflip"] = bool(b["hflip"])
            if isinstance(b.get("tint_color"), str) and vt.COLOR_RE.fullmatch(b["tint_color"]):
                s["tint_color"] = b["tint_color"]
            if "tint_opacity" in b:
                s["tint_opacity"] = vt._num(b["tint_opacity"], 0, 0.4, s["tint_opacity"])
            if "rotate_deg" in b:
                s["rotate_deg"] = vt._num(b["rotate_deg"], -10, 10, s["rotate_deg"])
        store.save_page_settings(pid, s)
        return JSONResponse({"tpl": t, "base": base_of(s)})

    @app.get("/pages/{pid}/editor", response_class=HTMLResponse)
    def tpl_editor(request: Request, pid: int):
        """Cửa sổ Mẫu edit của kênh (trang đứng riêng, không có menu app)."""
        page = page_of(pid)
        if not page:
            return PlainTextResponse("Không có kênh này", 404)
        s = store.page_settings(page)
        others = [r for r in store.q("SELECT id, name, platform FROM pages ORDER BY id") if r["id"] != pid]
        sv = lambda n: int((Path(__file__).parent / "static" / n).stat().st_mtime)
        return render(request, "editor.html", page=page, tpl=vt.normalize(s.get("tpl") or {}), base=base_of(s),
                      samples=samples(store, data, pid), others=others, transitions=vt.TRANSITIONS,
                      anims=vt.TEXT_ANIMS, ratios=vt.RATIOS, fits=vt.FITS, default=vt.default_template(),
                      static_v=sv)

    @app.post("/pages/{pid}/editor/open")
    def tpl_editor_open(pid: int):
        """App desktop: mở cửa sổ riêng (204). Trình duyệt thường: 404 -> trang tự window.open."""
        page = page_of(pid)
        if not page or not app.state.open_window:
            return Response(status_code=404)
        try:
            app.state.open_window(f"tpl{pid}", f"/pages/{pid}/editor", f"Mẫu edit · {page['name']} · dytool")
        except Exception as e:      # noqa: BLE001
            print("Không mở được cửa sổ Mẫu edit:", e, flush=True)
            return Response(status_code=404)
        return Response(status_code=204)

    @app.get("/tpl/demo.mp4")
    def tpl_demo():
        p = vt.demo_video(data)
        if not p:
            return PlainTextResponse("Không tạo được video mẫu", 404)
        return FileResponse(p, media_type="video/mp4")

    @app.get("/tpl/font/{name}")
    def tpl_font(name: str):
        """Font đúng như bản render (Be Vietnam Pro ExtraBold / Bold) để xem trước chữ khớp FFmpeg."""
        from ..subtitles import FONTS_DIR
        if name not in vt.FONTS.values():
            return PlainTextResponse("", 404)
        return FileResponse(FONTS_DIR / name, media_type="font/ttf", headers={"Cache-Control": "max-age=604800"})

    @app.post("/pages/{pid}/tpl/copy")
    async def tpl_copy(request: Request, pid: int):
        """Chép mẫu edit sang kênh khác (chỉ mẫu, không đụng cài đặt khác)."""
        form = await request.form()
        src, dst = page_of(pid), page_of(int(form.get("to") or 0))
        if not src or not dst or dst["id"] == pid:
            return PlainTextResponse("Chọn kênh để chép sang", 400)
        s2 = store.page_settings(dst)
        s2["tpl"] = store.page_settings(src).get("tpl") or {}
        store.save_page_settings(dst["id"], s2)
        store.log(f"[{src['name']}] Chép mẫu edit sang {dst['name']}", page_id=pid)
        return PlainTextResponse(f"Đã chép mẫu sang {dst['name']}")

    @app.post("/tpl/asset")
    async def tpl_asset(request: Request):
        form = await request.form()
        f = form.get("file")
        if not hasattr(f, "read"):
            return PlainTextResponse("Chưa chọn file", 400)
        raw = await f.read()
        if not raw or len(raw) > MAX_UPLOAD:
            return PlainTextResponse("File rỗng hoặc lớn hơn 200 MB", 400)
        try:
            from fastapi.concurrency import run_in_threadpool
            name = await run_in_threadpool(vt.store_asset, data, raw, f.filename or "")
        except ValueError as e:
            return PlainTextResponse(str(e), 400)
        m = next((a for a in vt.list_assets(data) if a["name"] == name), None) or {}
        return JSONResponse({"name": name, "kind": vt.asset_kind(name), "label": m.get("label") or name,
                             "dur": m.get("dur") or 0.0})

    @app.get("/tpl/assets")
    def tpl_assets():
        """Thư viện file (ảnh, video, nhạc đã tải lên) — dùng chung mọi kênh."""
        return JSONResponse(vt.list_assets(data))

    @app.post("/tpl/assets/{name}/hide")
    def tpl_asset_hide(name: str):
        if not vt.asset_path(data, name):
            return PlainTextResponse("Không có file", 404)
        vt.hide_asset(data, name)
        return JSONResponse({"ok": True})

    @app.get("/tpl/thumb/{name}")
    def tpl_thumb(name: str):
        p = vt.asset_thumb(data, name)
        if not p:
            return PlainTextResponse("", 404)
        return FileResponse(p, media_type=MEDIA_TYPES.get(p.suffix[1:].lower(), "image/jpeg"),
                            headers={"Cache-Control": "max-age=604800"})

    @app.get("/tpl/asset/{name}")
    def tpl_asset_get(name: str):
        p = vt.asset_path(data, name)
        if not p:
            return PlainTextResponse("Không có file", 404)
        return FileResponse(p, media_type=MEDIA_TYPES.get(p.suffix[1:].lower(), "application/octet-stream"),
                            headers={"Cache-Control": "max-age=604800"})

    @app.post("/tpl/render")
    async def tpl_render(request: Request):
        """Render thử mẫu đang soạn lên video mẫu: luồng nền, cỡ ≤ 960 px (hoặc đủ cỡ), preset nhanh."""
        try:
            body = await request.json()
        except ValueError:
            return PlainTextResponse("Dữ liệu không hợp lệ", 400)
        src = sample_path(str(body.get("sample") or ""))
        if not src:
            return PlainTextResponse("Chưa có video mẫu — kênh cần ít nhất 1 video đã edit, hoặc tải 1 video lên", 400)
        with jobs_lock:
            if any(j["state"] == "running" for j in jobs.values()):
                return PlainTextResponse("Đang render 1 bản khác — chờ xong đã", 400)
            key = secrets.token_hex(6)
            jobs[key] = {"state": "running", "started": time.time(), "error": "", "dur": 0.0}
            for k in sorted(jobs, key=lambda k: jobs[k]["started"])[:-3]:      # giữ 3 bản gần nhất
                (data / "work" / f"tpl_render_{k}.mp4").unlink(missing_ok=True)
                jobs.pop(k, None)
        tpl, full = body.get("template") or {}, bool(body.get("full"))
        # video mẫu đã edit đã có sẵn lật / màu phủ / nghiêng; video tải lên (gốc) thì áp thêm để giống thật
        base = (body.get("base") if str(body.get("sample") or "")[:2] in ("a:", "r:", "d:")
                and isinstance(body.get("base"), dict) else None)

        def run():
            out = data / "work" / f"tpl_render_{key}.mp4"
            try:
                dur = vt.apply(src, out, data / "work" / "tpl", tpl, data, crf=23, preset="veryfast",
                               max_long=1920 if full else 960, base=base)
                jobs[key].update(state="done", dur=dur, took=time.time() - jobs[key]["started"])
            except Exception as e:      # noqa: BLE001 — lỗi gì cũng hiện cho người dùng
                jobs[key].update(state="error", error=str(e)[:500])

        threading.Thread(target=run, daemon=True, name="tpl-render").start()
        return JSONResponse({"key": key})

    @app.get("/tpl/render/{key}")
    def tpl_render_state(key: str):
        j = jobs.get(key)
        if not j:
            return PlainTextResponse("Không có bản render này", 404)
        return JSONResponse({**j, "elapsed": time.time() - j["started"],
                             "url": f"/tpl/out/{key}.mp4" if j["state"] == "done" else ""})

    @app.get("/tpl/out/{key}.mp4")
    def tpl_render_file(key: str):
        if not re.fullmatch(r"[0-9a-f]{12}", key) or key not in jobs:
            return PlainTextResponse("Không có", 404)
        p = data / "work" / f"tpl_render_{key}.mp4"
        return FileResponse(p, media_type="video/mp4") if p.is_file() else PlainTextResponse("Không có", 404)
