"""Mẫu edit kiểu CapCut (thử nghiệm, nhánh exp/editor-template): intro / outro, hiệu ứng, chữ, logo, chuyển cảnh,
nhạc nền, khung hình — soạn ở trang /editor, gắn vào kênh (Cài đặt kênh › Edit, `edit_template`), áp SAU bước edit
chính (che sub Trung, lồng tiếng, phụ đề) bằng 1 lần FFmpeg nữa.

Mẫu = JSON ở data/templates/<id>.json; file người dùng tải lên (ảnh, video, nhạc) ở data/templates/assets/<sha>.<đuôi>.
Mọi giá trị đi qua `normalize` (kẹp số, màu #rrggbb, tên file theo regex) trước khi ghép vào lệnh FFmpeg.
Thời gian lớp chữ / ảnh tính theo đoạn video chính (0 = đầu video chính, sau intro).
Chữ: drawtext đọc `textfile=` (không escape nội dung), font Be Vietnam Pro trong work/fonts (chạy FFmpeg trong work/)."""

from __future__ import annotations

import json
import re
import secrets
import subprocess
import time
from pathlib import Path

from . import progress

COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")
ASSET_RE = re.compile(r"[0-9a-f]{16}\.(png|jpg|jpeg|webp|mp4|mov|webm|mp3|m4a|wav|aac|ogg)")
ID_RE = re.compile(r"[0-9a-f]{10}")
IMAGE_EXT = {"png", "jpg", "jpeg", "webp"}
VIDEO_EXT = {"mp4", "mov", "webm"}
AUDIO_EXT = {"mp3", "m4a", "wav", "aac", "ogg"}

# chuyển cảnh = tên xfade của FFmpeg (+ "none" = cắt thẳng)
TRANSITIONS = {
    "none": "Cắt thẳng", "fade": "Mờ dần", "fadeblack": "Qua màu đen", "fadewhite": "Qua màu trắng",
    "dissolve": "Tan", "wipeleft": "Gạt trái", "wiperight": "Gạt phải", "wipeup": "Gạt lên",
    "slideleft": "Trượt trái", "slideup": "Trượt lên", "circleopen": "Mở tròn", "circleclose": "Đóng tròn",
    "radial": "Quét tròn", "pixelize": "Vỡ điểm ảnh", "zoomin": "Phóng to", "smoothleft": "Lướt trái",
}
TEXT_ANIMS = {"none": "Không", "fade": "Hiện dần", "slide_up": "Trượt lên", "slide_left": "Trượt từ trái"}
RATIOS = {"keep": "Giữ như video", "9:16": "9:16 (Reels, TikTok)", "1:1": "1:1 (vuông)", "4:5": "4:5",
          "16:9": "16:9 (ngang)"}
FITS = {"blur": "Nền mờ", "black": "Nền đen", "crop": "Cắt cho đầy khung"}
FONTS = {"extrabold": "BeVietnamPro-ExtraBold.ttf", "bold": "BeVietnamPro-Bold.ttf"}


# ---------------- lưu / đọc ----------------
def tdir(data: Path) -> Path:
    d = Path(data) / "templates"
    (d / "assets").mkdir(parents=True, exist_ok=True)
    return d


def asset_path(data: Path, name: str) -> Path | None:
    if not name or not ASSET_RE.fullmatch(name):
        return None
    p = tdir(data) / "assets" / name
    return p if p.is_file() else None


def _section(kind: str) -> dict:
    return {"enabled": False, "duration": 2.0, "bg": "#101418", "file": "", "title": "", "sub": "",
            "color": "#ffffff", "accent": "#0f766e", "size": 0.045, "anim": "fade", "transition": "fade",
            "trans_dur": 0.5, "audio": True, "volume": 1.0, "fit": "crop"}


def default_template(name: str = "Mẫu mới") -> dict:
    intro, outro = _section("intro"), _section("outro")
    intro["title"] = "Tiêu đề video"
    outro["title"] = "Cảm ơn đã xem"
    outro["sub"] = "Theo dõi kênh để xem thêm"
    return {
        "id": "", "name": name, "updated": 0,
        "canvas": {"ratio": "keep", "fit": "blur"},
        "main": {"trim_start": 0.0, "trim_end": 0.0, "speed": 1.0, "volume": 1.0, "brightness": 0.0,
                 "contrast": 1.0, "saturation": 1.0, "temperature": 6500, "hue": 0, "sharpen": 0.0, "grain": 0,
                 "vignette": False, "zoom": 0.0, "fade_in": 0.0, "fade_out": 0.0},
        "intro": intro, "outro": outro,
        "layers": [],
        "music": {"file": "", "volume": 0.25, "fade_out": 1.5},
        "progress": {"enabled": False, "color": "#ff2d55", "height": 0.006, "pos": "bottom"},
    }


def _num(v, lo, hi, default):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f != f:
        return default
    return max(lo, min(hi, f))


def _col(v, default):
    return v if isinstance(v, str) and COLOR_RE.fullmatch(v) else default


def _pick(v, options, default):
    return v if v in options else default


def _txt(v, n=300):
    return str(v or "").replace("\r", "")[:n]


def _file(v):
    return v if isinstance(v, str) and ASSET_RE.fullmatch(v) else ""


def normalize(t: dict) -> dict:
    """Mẫu từ trình duyệt -> mẫu sạch (mọi trường có mặt, đúng kiểu, nằm trong giới hạn)."""
    d = default_template()
    t = t if isinstance(t, dict) else {}
    out = {"id": t.get("id") if isinstance(t.get("id"), str) and ID_RE.fullmatch(t.get("id")) else "",
           "name": _txt(t.get("name"), 60).strip() or "Mẫu không tên", "updated": int(time.time())}
    c = t.get("canvas") or {}
    out["canvas"] = {"ratio": _pick(c.get("ratio"), RATIOS, "keep"), "fit": _pick(c.get("fit"), FITS, "blur")}
    m, dm = t.get("main") or {}, d["main"]
    out["main"] = {
        "trim_start": _num(m.get("trim_start"), 0, 60, 0.0), "trim_end": _num(m.get("trim_end"), 0, 60, 0.0),
        "speed": _num(m.get("speed"), 0.5, 2.0, 1.0), "volume": _num(m.get("volume"), 0, 2, 1.0),
        "brightness": _num(m.get("brightness"), -0.3, 0.3, 0.0), "contrast": _num(m.get("contrast"), 0.5, 1.8, 1.0),
        "saturation": _num(m.get("saturation"), 0, 2.5, 1.0),
        "temperature": int(_num(m.get("temperature"), 3000, 10000, 6500)), "hue": int(_num(m.get("hue"), -180, 180, 0)),
        "sharpen": _num(m.get("sharpen"), 0, 1.5, 0.0), "grain": int(_num(m.get("grain"), 0, 40, 0)),
        "vignette": bool(m.get("vignette")), "zoom": _num(m.get("zoom"), 0, 0.3, 0.0),
        "fade_in": _num(m.get("fade_in"), 0, 3, 0.0), "fade_out": _num(m.get("fade_out"), 0, 3, 0.0),
    }
    for k in ("intro", "outro"):
        s, ds = t.get(k) or {}, d[k]
        out[k] = {
            "enabled": bool(s.get("enabled")), "duration": _num(s.get("duration"), 0.5, 30, 2.0),
            "audio": bool(s.get("audio", True)), "volume": _num(s.get("volume"), 0, 2, 1.0),
            "fit": _pick(s.get("fit"), ("crop", "pad", "blur"), "crop"),
            "bg": _col(s.get("bg"), ds["bg"]), "file": _file(s.get("file")),
            "title": _txt(s.get("title", ds["title"]), 120), "sub": _txt(s.get("sub", ds["sub"]), 160),
            "color": _col(s.get("color"), "#ffffff"), "accent": _col(s.get("accent"), ds["accent"]),
            "size": _num(s.get("size"), 0.02, 0.15, 0.045), "anim": _pick(s.get("anim"), TEXT_ANIMS, "fade"),
            "transition": _pick(s.get("transition"), TRANSITIONS, "fade"),
            "trans_dur": _num(s.get("trans_dur"), 0.1, 2.0, 0.5),
        }
    layers = []
    for L in (t.get("layers") or [])[:20]:
        if not isinstance(L, dict) or L.get("type") not in ("text", "image"):
            continue
        end = L.get("end")
        x = {"id": re.sub(r"[^a-z0-9]", "", str(L.get("id") or ""))[:12] or secrets.token_hex(4),
             "type": L["type"], "x": _num(L.get("x"), 0, 1, 0.5), "y": _num(L.get("y"), 0, 1, 0.5),
             "size": _num(L.get("size"), 0.01, 1, 0.05), "opacity": _num(L.get("opacity"), 0, 1, 1.0),
             "start": _num(L.get("start"), 0, 3600, 0.0),
             "end": None if end in (None, "", -1) else _num(end, 0, 3600, None),
             "anim": _pick(L.get("anim"), TEXT_ANIMS, "none")}
        if L["type"] == "text":
            x.update(text=_txt(L.get("text"), 300), font=_pick(L.get("font"), FONTS, "extrabold"),
                     color=_col(L.get("color"), "#ffffff"), stroke=_col(L.get("stroke"), "#000000"),
                     stroke_w=_num(L.get("stroke_w"), 0, 0.2, 0.08), box=bool(L.get("box")),
                     box_color=_col(L.get("box_color"), "#000000"), box_opacity=_num(L.get("box_opacity"), 0, 1, 0.5))
            if not x["text"].strip():
                continue
        else:
            x["file"] = _file(L.get("file"))
            if not x["file"]:
                continue
        if x["end"] is not None and x["end"] <= x["start"]:
            x["end"] = None
        layers.append(x)
    out["layers"] = layers
    mu = t.get("music") or {}
    out["music"] = {"file": _file(mu.get("file")), "volume": _num(mu.get("volume"), 0, 1.5, 0.25),
                    "fade_out": _num(mu.get("fade_out"), 0, 5, 1.5)}
    pg = t.get("progress") or {}
    out["progress"] = {"enabled": bool(pg.get("enabled")), "color": _col(pg.get("color"), "#ff2d55"),
                       "height": _num(pg.get("height"), 0.002, 0.03, 0.006), "pos": _pick(pg.get("pos"), ("top", "bottom"), "bottom")}
    return out


def list_templates(data: Path) -> list[dict]:
    out = []
    for p in sorted(tdir(data).glob("*.json")):
        if ID_RE.fullmatch(p.stem):
            try:
                t = json.loads(p.read_text("utf-8-sig"))
                out.append({"id": p.stem, "name": t.get("name") or p.stem, "updated": t.get("updated", 0)})
            except (OSError, ValueError):
                pass
    return sorted(out, key=lambda x: -x["updated"])


def load(data: Path, tid: str) -> dict | None:
    if not tid or not ID_RE.fullmatch(tid):
        return None
    p = tdir(data) / f"{tid}.json"
    try:
        t = normalize(json.loads(p.read_text("utf-8-sig")))
    except (OSError, ValueError):
        return None
    t["id"] = tid
    return t


def save(data: Path, t: dict) -> dict:
    t = normalize(t)
    if not t["id"]:
        t["id"] = secrets.token_hex(5)
    p = tdir(data) / f"{t['id']}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(t, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(p)
    return t


def delete(data: Path, tid: str) -> None:
    if tid and ID_RE.fullmatch(tid):
        (tdir(data) / f"{tid}.json").unlink(missing_ok=True)


def store_asset(data: Path, raw: bytes, filename: str) -> str:
    """Lưu file tải lên (ảnh / video / nhạc) theo SHA256 nội dung -> tên file; đuôi lạ -> ValueError."""
    import hashlib
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in IMAGE_EXT | VIDEO_EXT | AUDIO_EXT:
        raise ValueError("Chỉ nhận ảnh (png, jpg, webp), video (mp4, mov, webm) hoặc nhạc (mp3, m4a, wav, aac, ogg)")
    name = f"{hashlib.sha256(raw).hexdigest()[:16]}.{ext}"
    p = tdir(data) / "assets" / name
    if not p.exists():
        p.write_bytes(raw)
    meta = _meta(data)
    if name not in meta or filename:
        meta[name] = {"label": (filename or name)[:80], "kind": asset_kind(name),
                      "dur": round(_probe(p)["dur"], 2) if asset_kind(name) != "image" else 0.0,
                      "added": int(time.time()), "hidden": False}
        _meta_save(data, meta)
    return name


def asset_kind(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower()
    return "image" if ext in IMAGE_EXT else "video" if ext in VIDEO_EXT else "audio"


def _meta(data: Path) -> dict:
    try:
        m = json.loads((tdir(data) / "assets.json").read_text("utf-8-sig"))
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


def _meta_save(data: Path, m: dict) -> None:
    p = tdir(data) / "assets.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(p)


def list_assets(data: Path) -> list[dict]:
    """Thư viện file của trình Mẫu edit (mọi kênh dùng chung), mới nhất trước. File cũ chưa có tên thì bổ sung."""
    meta, dirty = _meta(data), False
    out = []
    for p in (tdir(data) / "assets").iterdir():
        if not ASSET_RE.fullmatch(p.name):
            continue
        m = meta.get(p.name)
        if not m:
            k = asset_kind(p.name)
            m = meta[p.name] = {"label": p.name, "kind": k, "dur": round(_probe(p)["dur"], 2) if k != "image" else 0.0,
                                "added": int(p.stat().st_mtime), "hidden": False}
            dirty = True
        if not m.get("hidden"):
            out.append({"name": p.name, **{k: m.get(k) for k in ("label", "kind", "dur", "added")}})
    if dirty:
        _meta_save(data, meta)
    return sorted(out, key=lambda x: -(x["added"] or 0))


def hide_asset(data: Path, name: str) -> None:
    """Xoá khỏi thư viện = chỉ ẩn (file vẫn giữ: mẫu của kênh khác có thể đang dùng)."""
    meta = _meta(data)
    if name in meta:
        meta[name]["hidden"] = True
        _meta_save(data, meta)


def asset_thumb(data: Path, name: str) -> Path | None:
    """Ảnh nhỏ cho thư viện: ảnh -> chính nó; video -> 1 khung JPG (tạo 1 lần ở templates/thumbs)."""
    p = asset_path(data, name)
    if not p:
        return None
    k = asset_kind(name)
    if k == "image":
        return p
    if k != "video":
        return None
    out = tdir(data) / "thumbs" / (p.stem + ".jpg")
    if not out.exists():
        out.parent.mkdir(exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.5", "-i", str(p), "-frames:v", "1",
                        "-vf", "scale=240:-2", str(out)], capture_output=True, timeout=30)
        if not out.exists():
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(p), "-frames:v", "1",
                            "-vf", "scale=240:-2", str(out)], capture_output=True, timeout=30)
    return out if out.exists() else None


# ---------------- render ----------------
def _probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True).stdout
    try:
        j = json.loads(out)
    except ValueError:
        j = {}
    st = j.get("streams") or []
    v = next((s for s in st if s.get("codec_type") == "video"), {})
    return {"w": int(v.get("width") or 0), "h": int(v.get("height") or 0),
            "audio": any(s.get("codec_type") == "audio" for s in st),
            "dur": float((j.get("format") or {}).get("duration") or 0)}


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def canvas_size(w: int, h: int, ratio: str, max_long: int = 1920) -> tuple[int, int]:
    if ratio == "keep" or ratio not in RATIOS:
        k = min(1.0, max_long / max(w, h))
        return _even(w * k), _even(h * k)
    a, b = (int(x) for x in ratio.split(":"))
    long_ = min(max_long, max(w, h))
    return (_even(long_ * a / b), _even(long_)) if a < b else (_even(long_), _even(long_ * b / a))


def _ff(c: str) -> str:
    return "0x" + c[1:]


def _q(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") or "0"


class _G:
    """Gom input + filter_complex."""

    def __init__(self, work: Path):
        self.inputs: list[list[str]] = []
        self.parts: list[str] = []
        self.work = work
        self.n = 0
        self.tf = 0

    def input(self, *args: str) -> int:
        self.inputs.append(list(args))
        return len(self.inputs) - 1

    def lab(self, p="s") -> str:
        self.n += 1
        return f"{p}{self.n}"

    def add(self, s: str) -> None:
        self.parts.append(s)

    def textfile(self, text: str) -> str:
        self.tf += 1
        name = f"tpl_text_{self.tf}.txt"
        (self.work / name).write_text(text, "utf-8")
        return name


def _alpha_expr(anim: str, a: float, b: float, base: float) -> str:
    """Độ trong suốt drawtext: hiện / ẩn dần 0,35 s ở 2 đầu (anim khác 'none')."""
    if anim == "none":
        return _q(base)
    d = 0.35
    return f"'{_q(base)}*min(1,max(0,min((t-{_q(a)})/{d},({_q(b)}-t)/{d})))'"


def _drawtext(g: _G, text: str, *, x: float, y: float, size_px: int, color: str, font: str = "extrabold",
              stroke: str = "#000000", stroke_px: int = 0, box: bool = False, box_color: str = "#000000",
              box_opacity: float = 0.5, opacity: float = 1.0, a: float = 0.0, b: float = 1e6,
              anim: str = "none") -> str:
    tf = g.textfile(text)
    xe, ye = f"{_q(x)}*w-text_w/2", f"{_q(y)}*h-text_h/2"
    d = 0.4
    if anim == "slide_up":
        ye += f"+h*0.05*max(0,1-(t-{_q(a)})/{d})"
    elif anim == "slide_left":
        xe += f"-w*0.25*max(0,1-(t-{_q(a)})/{d})^2"
    parts = [f"fontfile=fonts/{FONTS.get(font, FONTS['extrabold'])}", f"textfile={tf}", "expansion=none",
             f"fontsize={size_px}", f"fontcolor={_ff(color)}", "text_align=C", f"line_spacing={int(size_px * 0.18)}",
             f"x='{xe}'", f"y='{ye}'", f"alpha={_alpha_expr(anim, a, b, opacity)}",
             f"enable='between(t,{_q(a)},{_q(b)})'"]
    if stroke_px:
        parts += [f"borderw={stroke_px}", f"bordercolor={_ff(stroke)}"]
    if box:
        parts += ["box=1", f"boxcolor={_ff(box_color)}@{_q(box_opacity)}", f"boxborderw={int(size_px * 0.35)}"]
    return "drawtext=" + ":".join(parts)


def _segment(g: _G, sec: dict, data: Path, W: int, H: int, fps: str, label: str) -> tuple[str, str]:
    """Đoạn intro / outro -> (nhãn hình, nhãn tiếng) đúng W×H, fps, độ dài."""
    dur = sec["duration"]
    f = asset_path(data, sec["file"])
    ext = f.suffix[1:].lower() if f else ""
    v, a = g.lab(label + "v"), g.lab(label + "a")
    has_audio = False
    def fitted(i: int, extra: str = "") -> str:
        """Ảnh / video nền -> đúng W×H: cắt cho đầy | vừa khung + nền màu | vừa khung + nền mờ của chính nó."""
        if sec["fit"] == "pad":
            return (f"[{i}:v]{extra}scale={W}:{H}:force_original_aspect_ratio=decrease,"
                    f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={_ff(sec['bg'])},")
        if sec["fit"] == "blur":
            b1, b2, o = g.lab("sb"), g.lab("sf"), g.lab("so")
            g.add(f"[{i}:v]{extra}split[{b1}][{b2}]")
            g.add(f"[{b1}]scale={W // 4 * 2}:{H // 4 * 2}:force_original_aspect_ratio=increase,"
                  f"crop={W // 4 * 2}:{H // 4 * 2},boxblur=12:2,eq=brightness=-0.08,scale={W}:{H}[{b1}b];"
                  f"[{b2}]scale={W}:{H}:force_original_aspect_ratio=decrease[{b2}s];"
                  f"[{b1}b][{b2}s]overlay=(W-w)/2:(H-h)/2[{o}]")
            return f"[{o}]null,"
        return f"[{i}:v]{extra}scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"

    if f and ext in IMAGE_EXT:
        i = g.input("-loop", "1", "-t", _q(dur + 0.5), "-i", str(f.resolve()))
        src = fitted(i)
    elif f and ext in VIDEO_EXT:
        info = _probe(f)
        i = g.input("-i", str(f.resolve()))
        # video ngắn hơn độ dài đoạn: giữ khung cuối; dài hơn: cắt
        src = fitted(i) + f"tpad=stop_mode=clone:stop_duration={_q(dur)},"
        has_audio = info["audio"] and sec["audio"] and sec["volume"] > 0
    else:
        src = f"color=c={_ff(sec['bg'])}:s={W}x{H}:r={fps}:d={_q(dur + 0.5)},"
    chain = src + f"fps={fps},setsar=1,format=yuv420p"
    size = int(H * sec["size"])
    if sec["title"].strip():
        ty = 0.45 if sec["sub"].strip() else 0.5
        chain += "," + _drawtext(g, sec["title"], x=0.5, y=ty, size_px=size, color=sec["color"],
                                 stroke_px=int(size * 0.06), a=0.15, b=dur, anim=sec["anim"])
    if sec["sub"].strip():
        s2 = int(size * 0.5)
        chain += "," + _drawtext(g, sec["sub"], x=0.5, y=0.45 + sec["size"] * 0.9 if sec["title"].strip() else 0.5,
                                 size_px=s2, color=sec["color"], font="bold", box=True, box_color=sec["accent"],
                                 box_opacity=0.9, a=0.4, b=dur, anim=sec["anim"])
    g.add(f"{chain},trim=duration={_q(dur)},setpts=PTS-STARTPTS,settb=AVTB[{v}]")
    if has_audio:
        g.add(f"[{i}:a]volume={_q(sec['volume'])},aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,"
              f"atrim=duration={_q(dur)},asetpts=PTS-STARTPTS[{a}]")
    else:
        g.add(f"anullsrc=r=44100:cl=stereo,atrim=duration={_q(dur)},aformat=sample_fmts=fltp:channel_layouts=stereo[{a}]")
    return v, a


def _join(g: _G, va: tuple[str, str], vb: tuple[str, str], len_a: float, trans: str, td: float) -> tuple[tuple[str, str], float]:
    v, a = g.lab("jv"), g.lab("ja")
    if trans == "none":
        g.add(f"[{va[0]}][{va[1]}][{vb[0]}][{vb[1]}]concat=n=2:v=1:a=1[{v}][{a}]")
        return (v, a), 0.0
    g.add(f"[{va[0]}][{vb[0]}]xfade=transition={trans}:duration={_q(td)}:offset={_q(max(0.0, len_a - td))}[{v}]")
    g.add(f"[{va[1]}][{vb[1]}]acrossfade=d={_q(td)}[{a}]")
    return (v, a), td


def build(src: Path, work: Path, tpl: dict, data: Path, fps: str = "30", max_long: int = 1920,
          base: dict | None = None) -> tuple[_G, str, str, float]:
    """-> (đồ thị, nhãn hình cuối, nhãn tiếng cuối, độ dài)."""
    t = normalize(tpl)
    info = _probe(src)
    if not info["w"]:
        raise RuntimeError("Không đọc được video")
    W, H = canvas_size(info["w"], info["h"], t["canvas"]["ratio"], max_long)
    m = t["main"]
    g = _G(work)
    g.input("-i", str(src.resolve()))
    t0 = min(m["trim_start"], max(0.0, info["dur"] - 1))
    t1 = max(t0 + 0.5, info["dur"] - m["trim_end"])
    D = (t1 - t0) / m["speed"]

    c = [f"[0:v]trim=start={_q(t0)}:end={_q(t1)},setpts=(PTS-STARTPTS)/{_q(m['speed'])}"]
    if base:
        # Render thử trên video gốc tải lên: áp cả lật / nghiêng / màu phủ của kênh (bình thường làm ở editor.edit)
        import math
        if base.get("hflip"):
            c.append("hflip")
        deg = _num(base.get("rotate_deg"), -10, 10, 0.0)
        if deg:
            a_, w0, h0 = math.radians(abs(deg)), info["w"], info["h"]
            k = max(math.cos(a_) + math.sin(a_) * h0 / w0, math.cos(a_) + math.sin(a_) * w0 / h0) * 1.002
            c.append(f"scale={_even(w0 * k)}:{_even(h0 * k)},rotate={math.radians(deg):.6f}:fillcolor=black,crop={w0}:{h0}")
        op = _num(base.get("tint_opacity"), 0, 0.4, 0.0)
        col = _col(base.get("tint_color"), "")
        if op and col:
            r, g_, b = (int(col[i:i + 2], 16) for i in (1, 3, 5))
            c.append("format=rgb24,lutrgb=" + ":".join(f"{ch}='val*{_q(1 - op)}+{_q(op * v)}'"
                                                         for ch, v in (("r", r), ("g", g_), ("b", b))))
    if (W, H) != (info["w"], info["h"]):
        fit = t["canvas"]["fit"]
        if fit == "crop":
            c.append(f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}")
        elif fit == "black":
            c.append(f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black")
        else:
            bg, fg = g.lab("bg"), g.lab("fg")
            pre = g.lab("pre")
            g.add(",".join(c) + f",split[{bg}][{fg}]")
            g.add(f"[{bg}]scale={W // 4 * 2}:{H // 4 * 2}:force_original_aspect_ratio=increase,"
                  f"crop={W // 4 * 2}:{H // 4 * 2},boxblur=12:2,eq=brightness=-0.08,scale={W}:{H}[{bg}b]")
            g.add(f"[{fg}]scale={W}:{H}:force_original_aspect_ratio=decrease[{fg}s];"
                  f"[{bg}b][{fg}s]overlay=(W-w)/2:(H-h)/2[{pre}]")
            c = [f"[{pre}]null"]
    c.append("setsar=1")
    if m["zoom"]:
        # phóng to chậm dần tới (1+zoom) ở cuối (Ken Burns), giữ tâm khung
        c.append(f"scale=w='2*trunc({W}*(1+{_q(m['zoom'])}*t/{_q(D)})/2)':h='2*trunc({H}*(1+{_q(m['zoom'])}*t/{_q(D)})/2)'"
                 f":eval=frame,crop={W}:{H}")
    if m["brightness"] or m["contrast"] != 1 or m["saturation"] != 1:
        c.append(f"eq=brightness={_q(m['brightness'])}:contrast={_q(m['contrast'])}:saturation={_q(m['saturation'])}")
    if m["temperature"] != 6500:
        c.append(f"colortemperature=temperature={m['temperature']}")
    if m["hue"]:
        c.append(f"hue=h={m['hue']}")
    if m["sharpen"]:
        c.append(f"unsharp=5:5:{_q(m['sharpen'])}")
    if m["grain"]:
        c.append(f"noise=alls={m['grain']}:allf=t")
    if m["vignette"]:
        c.append("vignette=PI/5")
    c.append(f"fps={fps},format=yuv420p")
    cur = g.lab("m")
    g.add(",".join(c) + f"[{cur}]")

    # lớp chữ / ảnh (thời gian theo video chính)
    for L in t["layers"]:
        a = L["start"]
        b = D if L["end"] is None else min(D, L["end"])
        if a >= D:
            continue
        nxt = g.lab("m")
        if L["type"] == "text":
            size = max(8, int(H * L["size"]))
            g.add(f"[{cur}]" + _drawtext(g, L["text"], x=L["x"], y=L["y"], size_px=size, color=L["color"], font=L["font"],
                                         stroke=L["stroke"], stroke_px=int(size * L["stroke_w"]), box=L["box"],
                                         box_color=L["box_color"], box_opacity=L["box_opacity"], opacity=L["opacity"],
                                         a=a, b=b, anim=L["anim"]) + f"[{nxt}]")
        else:
            f = asset_path(data, L["file"])
            if not f or f.suffix[1:].lower() not in IMAGE_EXT | VIDEO_EXT:
                continue
            if f.suffix[1:].lower() in IMAGE_EXT:
                i = g.input("-loop", "1", "-t", _q(D + 1), "-i", str(f.resolve()))
            else:
                i = g.input("-stream_loop", "-1", "-t", _q(D + 1), "-i", str(f.resolve()))
            im = g.lab("im")
            fades = ""
            if L["anim"] != "none":
                fades = f",fade=t=in:st={_q(a)}:d=0.35:alpha=1,fade=t=out:st={_q(max(a, b - 0.35))}:d=0.35:alpha=1"
            g.add(f"[{i}:v]scale={_even(W * L['size'])}:-2,format=rgba,colorchannelmixer=aa={_q(L['opacity'])}"
                  f"{fades}[{im}]")
            g.add(f"[{cur}][{im}]overlay=x='{_q(L['x'])}*W-w/2':y='{_q(L['y'])}*H-h/2':"
                  f"enable='between(t,{_q(a)},{_q(b)})':eof_action=pass,format=yuv420p[{nxt}]")
        cur = nxt
    pg = t["progress"]
    if pg["enabled"]:
        bh = max(2, _even(H * pg["height"]))
        nxt, bar = g.lab("m"), g.lab("bar")
        g.add(f"color=c={_ff(pg['color'])}:s={W}x{bh}:r={fps}[{bar}]")
        y = "0" if pg["pos"] == "top" else f"H-{bh}"
        g.add(f"[{cur}][{bar}]overlay=x='-W+W*t/{_q(D)}':y={y}:shortest=1[{nxt}]")
        cur = nxt
    fi = m["fade_in"] if not t["intro"]["enabled"] else 0
    fo = m["fade_out"] if not t["outro"]["enabled"] else 0
    tail = f"trim=duration={_q(D)},setpts=PTS-STARTPTS,settb=AVTB"
    if fi:
        tail += f",fade=t=in:st=0:d={_q(fi)}"
    if fo:
        tail += f",fade=t=out:st={_q(max(0, D - fo))}:d={_q(fo)}"
    mv = g.lab("mv")
    g.add(f"[{cur}]{tail}[{mv}]")

    ma = g.lab("ma")
    atempo = f"atempo={_q(m['speed'])}," if m["speed"] != 1 else ""
    if info["audio"]:
        afade = ""
        if fi:
            afade += f",afade=t=in:st=0:d={_q(fi)}"
        if fo:
            afade += f",afade=t=out:st={_q(max(0, D - fo))}:d={_q(fo)}"
        g.add(f"[0:a]atrim=start={_q(t0)}:end={_q(t1)},asetpts=PTS-STARTPTS,{atempo}volume={_q(m['volume'])},"
              f"aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,atrim=duration={_q(D)}{afade}[{ma}]")
    else:
        g.add(f"anullsrc=r=44100:cl=stereo,atrim=duration={_q(D)},aformat=sample_fmts=fltp:channel_layouts=stereo[{ma}]")

    seq, total = (mv, ma), D
    if t["intro"]["enabled"]:
        it = t["intro"]
        iseg = _segment(g, it, data, W, H, fps, "in")
        td = min(it["trans_dur"], it["duration"] / 2, D / 2)
        seq, used = _join(g, iseg, seq, it["duration"], it["transition"], td)
        total = it["duration"] + D - used
    if t["outro"]["enabled"]:
        ot = t["outro"]
        oseg = _segment(g, ot, data, W, H, fps, "out")
        td = min(ot["trans_dur"], ot["duration"] / 2, D / 2)
        seq, used = _join(g, seq, oseg, total, ot["transition"], td)
        total = total + ot["duration"] - used

    v, a = seq
    mf = asset_path(data, t["music"]["file"])
    if mf and mf.suffix[1:].lower() in AUDIO_EXT | VIDEO_EXT:
        i = g.input("-stream_loop", "-1", "-i", str(mf.resolve()))
        mu, mix = g.lab("mu"), g.lab("mix")
        fo2 = t["music"]["fade_out"]
        g.add(f"[{i}:a]volume={_q(t['music']['volume'])},aresample=44100,"
              f"aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=duration={_q(total)}"
              + (f",afade=t=out:st={_q(max(0, total - fo2))}:d={_q(fo2)}" if fo2 else "") + f"[{mu}]")
        g.add(f"[{a}][{mu}]amix=inputs=2:duration=first:normalize=0[{mix}]")
        a = mix
    return g, v, a, total


def is_noop(tpl: dict | None) -> bool:
    if not tpl:
        return True
    t, d = normalize(tpl), default_template()
    return (t["canvas"]["ratio"] == "keep" and t["main"] == d["main"] and not t["intro"]["enabled"]
            and not t["outro"]["enabled"] and not t["layers"] and not t["music"]["file"] and not t["progress"]["enabled"])


def apply(src: Path, dst: Path, work: Path, tpl: dict, data: Path, crf: int = 20, preset: str = "medium",
          max_seconds: float | None = None, max_long: int = 1920, base: dict | None = None) -> float:
    """Áp mẫu lên `src` -> `dst`. Trả độ dài video ra (giây). Render thử: `max_seconds` = chỉ xuất N giây đầu,
    `max_long` nhỏ (vd 960) = xuất cỡ nhỏ cho nhanh."""
    from .editor import frame_rate
    from .subtitles import ensure_fonts
    work.mkdir(parents=True, exist_ok=True)
    ensure_fonts(work)
    g, v, a, total = build(src, work, tpl, data, frame_rate(src), max_long, base)
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for args in g.inputs:
        cmd += args
    cmd += ["-filter_complex", ";".join(g.parts), "-map", f"[{v}]", "-map", f"[{a}]",
            *(["-t", _q(max_seconds)] if max_seconds else []),
            "-c:v", "libx264", "-crf", str(crf), "-preset", preset, "-c:a", "aac", "-b:a", "160k",
            "-map_metadata", "-1", "-movflags", "+faststart", str(Path(dst).resolve())]
    progress.check()
    r = subprocess.run(cmd, cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise RuntimeError("FFmpeg lỗi khi áp mẫu edit: " + (r.stderr.strip().splitlines() or ["?"])[-1][:400])
    return total
