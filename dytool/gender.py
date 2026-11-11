"""Nhận diện giọng nam/nữ trên phần giọng đã tách bằng Demucs, theo từng câu (video nhiều người nói vẫn đúng).
Chính: model ECAPA-TDNN (github.com/JaesungHuh/voice-gender-classifier, 98.7% trên VoxCeleb).
Dự phòng: cao độ F0 (nam ~85-165 Hz, nữ ~165-300 Hz) — sai với giọng nữ trầm."""

from functools import lru_cache

import subprocess
from pathlib import Path

import numpy as np

SR = 16000
MALE_MAX = 165.0     # F0 trung vị <= ngưỡng này -> nam


def _load(path: Path) -> np.ndarray:
    pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", str(SR),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(pcm, np.float32)


def f0_track(x: np.ndarray) -> np.ndarray:
    """F0 theo khung 40ms (bước 20ms) bằng tự tương quan; chỉ lấy khung có giọng rõ."""
    fr, hop, lo, hi = 640, 320, SR // 400, SR // 70
    if len(x) < fr:
        return np.array([])
    thr = max(0.01, 0.15 * np.sqrt((x ** 2).mean()) * 3)
    out = []
    for i in range(0, len(x) - fr, hop):
        w = x[i:i + fr] - x[i:i + fr].mean()
        if np.sqrt((w ** 2).mean()) < thr:
            continue
        ac = np.correlate(w, w, "full")[fr - 1:]
        k = lo + int(ac[lo:hi].argmax())
        if ac[k] > 0.45 * ac[0]:
            out.append(SR / k)
    return np.array(out)


@lru_cache(maxsize=1)
def _model():
    import torch
    from ._ecapa_gender import ECAPA_gender
    m = ECAPA_gender.from_pretrained("JaesungHuh/voice-gender-classifier")
    m.eval()
    return m, torch


def _p_female(x: np.ndarray) -> float:
    """Xác suất là giọng nữ (model ECAPA)."""
    m, torch = _model()
    with torch.no_grad():
        logits = m.forward(torch.from_numpy(x.copy()).unsqueeze(0))
        prob = torch.softmax(logits, dim=1)[0]
    female_idx = [k for k, v in m.pred2gender.items() if v == "female"][0]
    return float(prob[female_idx])


def detect_genders(vocals: Path, segments: list[dict], method: str = "model") -> list[str]:
    """Trả về 'male'/'female' cho từng câu. Câu quá ngắn/không chắc thì theo giọng chiếm đa số của video."""
    x = _load(vocals)
    scores = []                                   # điểm "nữ" 0..1, None = không đo được
    for s in segments:
        seg = x[int(s["start"] * SR):int(s["end"] * SR)]
        if len(seg) < int(0.6 * SR):
            scores.append(None)
            continue
        if method == "model":
            scores.append(_p_female(seg))
        else:
            f = f0_track(seg)
            scores.append(None if len(f) < 8 else float(np.median(f) > MALE_MAX))
    # trọng số theo độ dài câu cho kết luận chung
    known = [(sc, s["end"] - s["start"]) for sc, s in zip(scores, segments) if sc is not None]
    overall = "female" if known and sum(sc * w for sc, w in known) / sum(w for _, w in known) >= 0.5 else "male"
    return [overall if sc is None or abs(sc - 0.5) < 0.25 else ("female" if sc > 0.5 else "male") for sc in scores]


def describe(vocals: Path, segments: list[dict]) -> list[tuple[float | None, str]]:
    """Dùng để kiểm tra: (F0 trung vị, giới tính) từng câu."""
    return [(None, g) for g in detect_genders(vocals, segments)]
