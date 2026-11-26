"""Edit video bằng FFmpeg theo mẫu trong config:
che sub Trung (làm mờ dải) -> lật ngang -> nghiêng 1° (phóng to bù góc) -> phủ xanh 12% -> sub tiếng Việt."""

import json
import math
import subprocess
from pathlib import Path

from . import progress, subtitles


_codec_cache: dict[tuple[str, float], str] = {}


def video_codec(path: Path) -> str:
    """Mã hoá luồng hình ('h264', 'hevc'…; '' nếu không đọc được) — nhớ theo đường dẫn + giờ sửa file."""
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        return ""
    if key not in _codec_cache:
        try:
            _codec_cache[key] = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name",
                 "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
        if len(_codec_cache) > 500:
            _codec_cache.clear()
    return _codec_cache[key]


def probe(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:stream_side_data=rotation", "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    st = json.loads(out)["streams"][0]
    w, h = st["width"], st["height"]
    rot = next((abs(int(d.get("rotation", 0))) for d in st.get("side_data_list", [])), 0)
    return (h, w) if rot in (90, 270) else (w, h)


def frame_rate(path: Path) -> str:
    """Tốc độ khung của video gốc dạng "30/1" / "30000/1001" (lỗi / lạ thì "30")."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=avg_frame_rate,r_frame_rate", "-of", "json", str(path)],
                         capture_output=True, text=True).stdout
    try:
        st = json.loads(out)["streams"][0]
    except (ValueError, KeyError, IndexError):
        return "30"
    for r in (st.get("avg_frame_rate"), st.get("r_frame_rate")):
        try:
            n, d = (int(x) for x in str(r).split("/"))
            if d and 10 <= n / d <= 120:
                return f"{n}/{d}"
        except ValueError:
            pass
    return "30"


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True, check=True).stdout
    return float(out.strip() or 0)


def _even(x: float) -> int:
    return int(round(x / 2)) * 2


def target_size(w: int, h: int, cfg: dict) -> tuple[int, int]:
    """Thu nhỏ để cạnh dài <= max_long_edge (mặc định 1920) — video 2K/4K encode rất chậm mà FB cũng nén lại."""
    m = cfg.get("output", {}).get("max_long_edge", 1920)
    k = min(1.0, m / max(w, h))
    return _even(w * k), _even(h * k)


def band_rows(h: int, band: tuple[float, float], feather: int) -> tuple[int, int]:
    """Hàng trên / dưới (px) của dải làm mờ gồm cả phần mép mờ dần."""
    f = _even(feather)
    return max(0, _even(h * band[0]) - f), min(h, _even(h * band[1]) + f)


def feather_mask(work: Path, w: int, h: int, band: tuple[float, float], feather: int) -> Path:
    """Ảnh mặt nạ alpha cho dải mờ: 0 ở mép -> 255 khi vào tới lõi (smoothstep). Tạo 1 lần bằng geq rồi đưa
    vào FFmpeg dạng ảnh lặp — tính geq mỗi khung làm xuất chậm gấp đôi."""
    y1, y2 = band_rows(h, band, feather)
    f = max(1, _even(feather))
    out = work / f"mask_{w}x{y2 - y1}_{f}.png"
    if not out.exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"color=c=black:s={w}x{y2 - y1}:d=0.04,format=gray,"
                        f"geq=lum='st(0,clip(min(Y,H-1-Y)/{f},0,1));255*ld(0)*ld(0)*(3-2*ld(0))'",
                        "-frames:v", "1", str(out)], check=True)
    return out


def build_filter(w: int, h: int, cfg: dict, ass_name: str | None, src_size: tuple[int, int] | None = None,
                 band: tuple[float, float] | None = None, feather: int = 0, mask_input: int | None = None,
                 fps: str = "30") -> str:
    chain = []
    if src_size and src_size != (w, h):
        chain.append(f"scale={w}:{h}")
    if band:
        # lõi mờ đặc = đúng dải sub Trung; thêm `feather` px mờ dần ở mép trên / dưới (không có cạnh cứng)
        f = _even(feather)
        y1, y2 = band_rows(h, band, feather)
        # boxblur yêu cầu bán kính < 1/2 cạnh nhỏ của kênh màu (= 1/4 chiều cao dải)
        b = max(1, min(int(cfg.get("hide_hardsub", {}).get("blur", 20)), (y2 - y1) // 4 - 1))
        blur = f"crop={w}:{y2 - y1}:0:{y1},boxblur={b}:2"
        if f and mask_input is not None:
            # mặt nạ alpha (feather_mask, ảnh PNG đưa vào bằng -loop 1): mép dải mờ tan dần vào hình
            chain.append(f"split[a][b];[{mask_input}:v]format=gray,scale={w}:{y2 - y1}[mk];[b]{blur}[bb];"
                         f"[bb][mk]alphamerge=shortest=1[bl];[a][bl]overlay=0:{y1}:shortest=1")
        else:
            chain.append(f"split[a][b];[b]{blur}[bl];[a][bl]overlay=0:{y1}")
    if cfg.get("hflip"):
        chain.append("hflip")
    deg = float(cfg.get("rotate_deg", 0))
    if deg:
        a = math.radians(abs(deg))
        # hệ số phóng to tối thiểu để khung xoay vẫn phủ kín khung gốc (không lộ góc đen)
        s = max(math.cos(a) + math.sin(a) * h / w, math.cos(a) + math.sin(a) * w / h) * 1.002
        chain.append(f"scale={_even(w * s)}:{_even(h * s)},rotate={math.radians(deg):.6f}:fillcolor=black,"
                     f"crop={w}:{h}")
    tint = cfg.get("tint")
    if tint and tint.get("opacity"):
        # pha màu chuẩn trên RGB: out = (1-a)*video + a*màu  (drawbox pha trong YUV cho màu đậm sai lệch)
        # r= tốc độ khung video gốc: blend lấy nhịp của lớp trên ([tint]) — thiếu r thì color mặc định 25 khung/s
        # -> video 30 khung/s ra còn 25 (mất khung, kém mượt)
        chain.append(f"format=gbrp[pre];color=c={tint['color']}:s={w}x{h}:r={fps},format=gbrp[tint];"
                     f"[tint][pre]blend=all_mode=normal:all_opacity={tint['opacity']}:shortest=1")
    if ass_name:
        chain.append(f"subtitles={ass_name}:fontsdir=fonts")
    chain.append("format=yuv420p")
    return "[0:v]" + ",".join(chain) + "[v]"


def _ai_cfg(sub_cfg: dict) -> dict:
    """Phần cài đặt phụ đề ảnh hưởng tới bản dịch (khoá lưu kết quả dịch): bỏ cách trình bày (font, cỡ, vị trí…)
    để sửa kiểu chữ sau khi lỗi không làm mất bản dịch đã có."""
    skip = ("font", "size", "color", "outline", "shadow", "margin", "style", "band", "pos", "box", "bold", "align")
    return {k: v for k, v in sub_cfg.items() if not any(w in k for w in skip)}


def pick_source(src: Path, band, src_size, work: Path, sub_cfg: dict, mode: str = "auto", steps=None):
    """Chọn nguồn chữ để dịch. Trả về (các câu [{start,end,text,asr?}], "ocr"|"asr", có_giọng_nói, các đoạn
    Whisper nghe được tiếng người [(start, end)] — lồng tiếng chỉ tắt tiếng gốc ở các đoạn này, giữ tiếng mèo…).
    - Có phụ đề in cứng (OCR đọc được): dùng chữ OCR làm chính, đính kèm lời Whisper nghe được cùng
      khoảng thời gian để Claude đối chiếu (OCR đúng chữ hơn nhưng đôi khi đọc lỗi).
    - Không có phụ đề: dùng giọng nói (Whisper).
    - Chỉ có chữ, không có lời: vẫn dùng OCR (has_speech=False -> chỉ làm phụ đề, trừ khi bật dub.text_only)."""
    from .stepcache import NoSteps
    steps = steps or NoSteps()
    model, lang = sub_cfg.get("whisper_model", "small"), sub_cfg.get("source_lang", "zh")
    speech = steps.run("whisper", [model, lang],
                       lambda: subtitles.transcribe(src, work, model, lang, split=False),
                       label="Nghe giọng nói (Whisper)")
    spans = [(x["start"], x["end"]) for x in speech]
    speech_secs = sum(x["end"] - x["start"] for x in speech)
    has_speech = speech_secs >= 1.0
    cues = []
    if band and mode in ("auto", "ocr"):
        from .hardsub import extract_cues
        cues = steps.run("ocr", [list(band)], lambda: extract_cues(src, band, *src_size, duration(src)),
                         label="Đọc phụ đề trên video (OCR)")
    total = duration(src)
    use_ocr = mode == "ocr" or (mode == "auto" and cues and
                                sum(c["end"] - c["start"] for c in cues) >= min(3.0, 0.15 * total))
    if use_ocr and cues:
        for c in cues:
            c["asr"] = "".join(x["text"] for x in speech
                               if min(c["end"], x["end"]) - max(c["start"], x["start"]) > 0.2)
        return cues, "ocr", has_speech, spans
    return speech, "asr", has_speech, spans


def edit(src: Path, dst: Path, work: Path, cfg: dict) -> dict:
    """Trả về thông tin phụ: số câu sub. Kết quả từng bước tốn thời gian lưu ở work/<id>_steps (stepcache):
    edit lỗi / bị Dừng rồi Thử lại thì dùng lại, không chạy lại từ đầu; edit xong thì xoá."""
    from .stepcache import Steps
    steps = Steps(work, src)
    src_size = probe(src)
    w, h = target_size(*src_size, cfg)
    ass_name = None
    n_lines = 0
    sub_cfg = dict(cfg.get("subtitles", {}))

    # vị trí sub Trung cần che: auto = dò bằng OCR, fixed = dùng y_from/y_to, off = không che
    hs = cfg.get("hide_hardsub", {})
    mode = hs.get("mode", "auto") if hs.get("enabled", True) else "off"
    band = None
    if mode == "fixed":
        band = (hs["y_from"], hs["y_to"])
    elif mode == "auto":
        from .hardsub import detect_band
        band = steps.run("band", [], lambda: detect_band(src, duration(src), *src_size),
                         label="Dò vị trí phụ đề Trung")
        band = tuple(band) if band else None          # JSON trả list
    if band:
        # đặt sub tiếng Việt đè lên đúng chỗ sub Trung cũ: căn giữa dải (write_ass dùng sub_cfg["band"])
        sub_cfg["margin_v_pct"] = max(0.02, 1 - band[1] + 0.005)
        sub_cfg["band"] = band
    dub_cfg = cfg.get("dub", {})
    dub_track = None
    source = "none"
    if sub_cfg.get("enabled") or dub_cfg.get("enabled"):
        segs, source, has_speech, spans = pick_source(src, band, src_size, work, sub_cfg, cfg.get("text_source", "auto"),
                                               steps)
        # 3 trường hợp: có sub -> chữ OCR (+ giọng để đối chiếu) | không sub -> giọng nói | chỉ có chữ -> OCR
        want_dub = dub_cfg.get("enabled") and segs and (has_speech or dub_cfg.get("text_only", False))
        if want_dub:
            from .dub import dub_audio
            dub_track, sub_lines = dub_audio(src, segs, work, dub_cfg, sub_cfg, steps, speech_spans=spans)
            n_lines = len(sub_lines)
            if sub_cfg.get("enabled") and sub_lines:
                ass_name = subtitles.write_ass(sub_lines, [x["text"] for x in sub_lines],
                                               work / f"{src.stem}.ass", w, h, sub_cfg).name
        elif sub_cfg.get("enabled") and segs:
            n_lines = len(segs)
            lines, wins = [x["text"] for x in segs], [x["end"] - x["start"] + 0.3 for x in segs]
            asr = [x.get("asr", "") for x in segs]
            texts = steps.run("translate_sub", [lines, wins, asr, _ai_cfg(sub_cfg)],
                              lambda: subtitles.translate_utterances(lines, wins, sub_cfg, asr=asr),
                              label="Dịch phụ đề (Claude)")
            ass_name = subtitles.write_ass(segs, texts, work / f"{src.stem}.ass", w, h, sub_cfg).name

    feather = 0
    if band:
        # mép mờ dần: đủ rộng để chữ Việt 2 dòng (căn giữa dải) tràn ra mà không thấy "lòi khỏi khung"
        feather = round(subtitles.feather_px(band, w, h, sub_cfg))
    if ass_name:
        subtitles.ensure_fonts(work)
    mask_args, mask_input = [], None
    if band and feather:
        mask_input = 2 if dub_track else 1
        mask_args = ["-loop", "1", "-framerate", "30", "-t", f"{duration(src) + 1:.2f}",
                     "-i", str(feather_mask(work, w, h, band, feather).resolve())]
    out = cfg.get("output", {})
    tpl = cfg.get("template")
    final = dst
    if tpl:
        dst = work / f"{src.stem}_base.mp4"      # bản edit chính -> áp mẫu (vtemplate) ra file cuối
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(src.resolve()),
        *(["-i", str(dub_track.resolve())] if dub_track else []),
        *mask_args,
        "-filter_complex", build_filter(w, h, cfg, ass_name, src_size, band, feather, mask_input,
                                                frame_rate(src)),
        "-map", "[v]", "-map", "1:a" if dub_track else "0:a?",
        "-c:v", "libx264", "-crf", str(out.get("crf", 20)), "-preset", out.get("preset", "medium"),
        "-c:a", "aac", "-b:a", "128k", "-map_metadata", "-1", "-movflags", "+faststart",
        str(dst.resolve()),
    ]
    progress.step("Xuất video (FFmpeg)")
    # chạy trong thư mục work để filter subtitles đọc file .ass bằng tên ngắn (tránh lỗi escape đường dẫn)
    subprocess.run(cmd, cwd=work, check=True)
    if tpl:
        from . import vtemplate
        progress.step("Áp mẫu edit (intro / outro, hiệu ứng)")
        try:
            vtemplate.apply(dst, final, work, tpl, work.parent, crf=int(out.get("crf", 20)),
                            preset=out.get("preset", "medium"))
        finally:
            dst.unlink(missing_ok=True)
    steps.clear()            # edit xong: lần Edit lại sau dịch / đọc mới
    return {"sub_lines": n_lines, "band": band, "dubbed": bool(dub_track), "text_source": source}
