"""Tự dò vị trí phụ đề tiếng Trung in cứng bằng RapidOCR trên vài khung hình mẫu."""

import logging
import re
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np

CJK = re.compile(r"[一-鿿]")


@lru_cache(maxsize=1)
def _ocr():
    from rapidocr import RapidOCR
    logging.getLogger("RapidOCR").setLevel(logging.WARNING)
    return RapidOCR()


def _frame(video: Path, t: float, w: int, h: int) -> np.ndarray | None:
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video),
                          "-frames:v", "1", "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                         capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3) if len(raw) == w * h * 3 else None


def detect_band(video: Path, duration: float, w: int, h: int, samples: int = 10,
                min_frames: int = 2, region_from: float = 0.5) -> tuple[float, float] | None:
    """Trả về (y_from, y_to) theo tỉ lệ chiều cao, hoặc None nếu không thấy sub.
    Chỉ xét chữ Hán ở nửa dưới khung hình (bỏ qua UI game/logo ở phía trên),
    và chỉ tính là sub khi xuất hiện ở >= min_frames khung."""
    # OCR trên ảnh thu nhỏ (cạnh ngắn ~720) cho nhanh
    k = min(1.0, 720 / min(w, h))
    sw, sh = int(w * k) // 2 * 2, int(h * k) // 2 * 2
    boxes = []   # (frame, top, bottom)
    for i in range(samples):
        img = _frame(video, duration * (i + 0.5) / samples, sw, sh)
        if img is None:
            continue
        res = _ocr()(img)
        if res.boxes is None:
            continue
        for box, txt, score in zip(res.boxes, res.txts, res.scores):
            ys = box[:, 1] / sh
            if score < 0.6 or not CJK.search(txt) or ys.mean() < region_from:
                continue
            boxes.append((i, float(ys.min()), float(ys.max())))
    if not boxes:
        return None
    # Phụ đề luôn nằm cùng 1 hàng ở mọi khung: gom các box có tâm gần nhau,
    # chọn hàng xuất hiện ở nhiều khung nhất (loại chữ UI game, sticker... chỉ thỉnh thoảng hiện)
    best: list = []
    for _, t0, b0 in boxes:
        c0 = (t0 + b0) / 2
        row = [bx for bx in boxes if abs((bx[1] + bx[2]) / 2 - c0) < 0.035]
        if len({f for f, *_ in row}) > len({f for f, *_ in best}):
            best = row
    if len({f for f, *_ in best}) < min_frames:
        return None
    pad = 0.004
    return max(0.0, min(t for _, t, _ in best) - pad), min(1.0, max(b for *_, b in best) + pad)


def extract_cues(video: Path, band: tuple[float, float], w: int, h: int, duration: float,
                 fps: float = 3.0) -> list[dict]:
    """Đọc NỘI DUNG phụ đề in cứng theo thời gian: OCR dải sub ở `fps` khung/giây,
    gộp các khung liên tiếp cùng nội dung thành 1 câu {start, end, text}.
    Bỏ chữ đứng yên suốt video (logo, ID kênh...)."""
    from difflib import SequenceMatcher
    y1, y2 = int(h * band[0]) // 2 * 2, int(h * band[1]) // 2 * 2
    cw = min(w, 1080)                     # độ phân giải gốc (tối đa 1080) để OCR chính xác
    ch = max(16, int((y2 - y1) * cw / w)) // 2 * 2
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(video), "-vf",
                          f"fps={fps},crop={w}:{y2 - y1}:0:{y1},scale={cw}:{ch}",
                          "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True).stdout
    n = len(raw) // (cw * ch * 3)
    frames = np.frombuffer(raw[: n * cw * ch * 3], np.uint8).reshape(n, ch, cw, 3)
    from . import progress
    texts = []
    last_mask, last_text = None, ""
    for fi, f in enumerate(frames):
        if fi % 5 == 0:
            progress.detail(f"khung {fi + 1}/{n}")
        # chỉ OCR khi chữ trên dải sub thay đổi (so mặt nạ điểm ảnh sáng = chữ trắng)
        mask = f.max(axis=2) > 200
        if last_mask is not None and np.mean(mask != last_mask) < 0.015:
            texts.append(last_text)
            continue
        res = _ocr()(f)
        if res.boxes is None:
            t = ""
        else:
            # các dòng sub theo thứ tự trên -> dưới
            items = sorted(((float(b[:, 1].mean()), t) for b, t, s in zip(res.boxes, res.txts, res.scores)
                            if s >= 0.6 and CJK.search(t)), key=lambda x: x[0])
            t = "".join(t for _, t in items).strip()
            # chỉ giữ chữ Hán, số, chữ Latin và dấu câu tiếng Trung (bỏ ký tự rác OCR như の, ・)
            t = re.sub(r"[^\u4e00-\u9fff0-9A-Za-z，。！？、：；“”…—]", "", t)
        texts.append(t)
        last_mask, last_text = mask, t
    # chữ xuất hiện ở > 60% số khung = logo/ID kênh, không phải lời thoại
    from collections import Counter
    common = {t for t, c in Counter(texts).items() if t and c > 0.6 * max(1, n)}
    cues: list[dict] = []
    for i, t in enumerate(texts):
        t = "" if t in common else t
        start = i / fps
        prev = cues[-1]["text"] if cues else ""
        same = t and prev and (SequenceMatcher(None, prev, t).ratio() >= 0.6 or prev in t or t in prev)
        if cues and same and start - cues[-1]["end"] <= 1.0 / fps + 1e-6:
            cues[-1]["end"] = start + 1 / fps
            if len(t) > len(cues[-1]["text"]):      # giữ bản đọc dài/đủ nhất
                cues[-1]["text"] = t
        elif t:
            cues.append({"start": start, "end": start + 1 / fps, "text": t})
    return [c for c in cues if c["end"] - c["start"] >= 0.3 and len(CJK.findall(c["text"])) >= 2]
