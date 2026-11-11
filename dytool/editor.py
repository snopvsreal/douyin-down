"""Edit video bằng FFmpeg theo mẫu trong config:
che sub Trung (làm mờ dải) -> lật ngang -> nghiêng 1° (phóng to bù góc) -> phủ xanh 12% -> sub tiếng Việt."""

import json
import math
import subprocess
from pathlib import Path

from . import progress, subtitles


def probe(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:stream_side_data=rotation", "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    st = json.loads(out)["streams"][0]
    w, h = st["width"], st["height"]
    rot = next((abs(int(d.get("rotation", 0))) for d in st.get("side_data_list", [])), 0)
    return (h, w) if rot in (90, 270) else (w, h)


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


def build_filter(w: int, h: int, cfg: dict, ass_name: str | None, src_size: tuple[int, int] | None = None,
                 band: tuple[float, float] | None = None) -> str:
    chain = []
    if src_size and src_size != (w, h):
        chain.append(f"scale={w}:{h}")
    if band:
        y1, y2 = _even(h * band[0]), _even(h * band[1])
        # boxblur yêu cầu bán kính < 1/2 cạnh nhỏ của kênh màu (= 1/4 chiều cao dải)
        b = max(1, min(int(cfg.get("hide_hardsub", {}).get("blur", 20)), (y2 - y1) // 4 - 1))
        chain.append(f"split[a][b];[b]crop={w}:{y2 - y1}:0:{y1},boxblur={b}:2[bl];[a][bl]overlay=0:{y1}")
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
        chain.append(f"format=gbrp[pre];color=c={tint['color']}:s={w}x{h},format=gbrp[tint];"
                     f"[tint][pre]blend=all_mode=normal:all_opacity={tint['opacity']}:shortest=1")
    if ass_name:
        chain.append(f"subtitles={ass_name}")
    chain.append("format=yuv420p")
    return "[0:v]" + ",".join(chain) + "[v]"


def pick_source(src: Path, band, src_size, work: Path, sub_cfg: dict, mode: str = "auto"):
    """Chọn nguồn chữ để dịch. Trả về (các câu [{start,end,text,asr?}], "ocr"|"asr", có_giọng_nói).
    - Có phụ đề in cứng (OCR đọc được): dùng chữ OCR làm chính, đính kèm lời Whisper nghe được cùng
      khoảng thời gian để Claude đối chiếu (OCR đúng chữ hơn nhưng đôi khi đọc lỗi).
    - Không có phụ đề: dùng giọng nói (Whisper).
    - Chỉ có chữ, không có lời: vẫn dùng OCR (has_speech=False -> chỉ làm phụ đề, trừ khi bật dub.text_only)."""
    progress.step("Nghe giọng nói (Whisper)")
    speech = subtitles.transcribe(src, work, sub_cfg.get("whisper_model", "small"),
                                  sub_cfg.get("source_lang", "zh"), split=False)
    speech_secs = sum(x["end"] - x["start"] for x in speech)
    has_speech = speech_secs >= 1.0
    cues = []
    if band and mode in ("auto", "ocr"):
        from .hardsub import extract_cues
        progress.step("Đọc phụ đề trên video (OCR)")
        cues = extract_cues(src, band, *src_size, duration(src))
    total = duration(src)
    use_ocr = mode == "ocr" or (mode == "auto" and cues and
                                sum(c["end"] - c["start"] for c in cues) >= min(3.0, 0.15 * total))
    if use_ocr and cues:
        for c in cues:
            c["asr"] = "".join(x["text"] for x in speech
                               if min(c["end"], x["end"]) - max(c["start"], x["start"]) > 0.2)
        return cues, "ocr", has_speech
    return speech, "asr", has_speech


def edit(src: Path, dst: Path, work: Path, cfg: dict) -> dict:
    """Trả về thông tin phụ: số câu sub."""
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
        progress.step("Dò vị trí phụ đề Trung")
        band = detect_band(src, duration(src), *src_size)
    if band:
        # đặt sub tiếng Việt đè lên đúng chỗ sub Trung cũ
        sub_cfg["margin_v_pct"] = max(0.02, 1 - band[1] + 0.005)
    dub_cfg = cfg.get("dub", {})
    dub_track = None
    source = "none"
    if sub_cfg.get("enabled") or dub_cfg.get("enabled"):
        segs, source, has_speech = pick_source(src, band, src_size, work, sub_cfg, cfg.get("text_source", "auto"))
        # 3 trường hợp: có sub -> chữ OCR (+ giọng để đối chiếu) | không sub -> giọng nói | chỉ có chữ -> OCR
        want_dub = dub_cfg.get("enabled") and segs and (has_speech or dub_cfg.get("text_only", False))
        if want_dub:
            from .dub import dub_audio
            dub_track, sub_lines = dub_audio(src, segs, work, dub_cfg, sub_cfg)
            n_lines = len(sub_lines)
            if sub_cfg.get("enabled") and sub_lines:
                ass_name = subtitles.write_ass(sub_lines, [x["text"] for x in sub_lines],
                                               work / f"{src.stem}.ass", w, h, sub_cfg).name
        elif sub_cfg.get("enabled") and segs:
            n_lines = len(segs)
            progress.step("Dịch phụ đề (Claude)")
            texts = subtitles.translate_utterances([x["text"] for x in segs],
                                                   [x["end"] - x["start"] + 0.3 for x in segs], sub_cfg,
                                                   asr=[x.get("asr", "") for x in segs])
            ass_name = subtitles.write_ass(segs, texts, work / f"{src.stem}.ass", w, h, sub_cfg).name

    out = cfg.get("output", {})
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(src.resolve()),
        *(["-i", str(dub_track.resolve())] if dub_track else []),
        "-filter_complex", build_filter(w, h, cfg, ass_name, src_size, band),
        "-map", "[v]", "-map", "1:a" if dub_track else "0:a?",
        "-c:v", "libx264", "-crf", str(out.get("crf", 20)), "-preset", out.get("preset", "medium"),
        "-c:a", "aac", "-b:a", "128k", "-map_metadata", "-1", "-movflags", "+faststart",
        str(dst.resolve()),
    ]
    progress.step("Xuất video (FFmpeg)")
    # chạy trong thư mục work để filter subtitles đọc file .ass bằng tên ngắn (tránh lỗi escape đường dẫn)
    subprocess.run(cmd, cwd=work, check=True)
    return {"sub_lines": n_lines, "band": band, "dubbed": bool(dub_track), "text_source": source}
