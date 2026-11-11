"""Phân tích nhịp đọc tiếng Việt thực tế (video review phim, thuyết minh, đọc truyện).

Đầu vào: data/research/audio/<phong_cach>_<id>.m4a (4 phút giữa mỗi video).
Cách làm: Demucs tách giọng khỏi nhạc nền -> faster-whisper (vi) lấy mốc thời gian TỪNG TỪ
-> đo khoảng nghỉ giữa các từ, tốc độ, độ dài cụm đọc liền, quan hệ với dấu câu và từ nối.
Đầu ra: data/research/prosody_stats.json (+ in bảng tóm tắt).

Chạy:  uv run python research/analyze_prosody.py
"""

import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
AUDIO = ROOT / "data/research/audio"
WORK = ROOT / "data/research/work"
OUT = ROOT / "data/research/prosody_stats.json"

MODEL = "small"
PAUSE_MIN = 0.15          # khoảng lặng >= 150ms giữa 2 từ mới coi là "ngắt"
SENT_END = re.compile(r"[.!?…]$")
COMMA = re.compile(r"[,;:]$")


def words_of(audio: Path) -> list[dict]:
    cache = WORK / f"{audio.stem}.words.json"
    if cache.exists():
        return json.loads(cache.read_text())
    WORK.mkdir(parents=True, exist_ok=True)
    # 1) tách giọng (bỏ nhạc nền phim) để mốc thời gian chính xác hơn
    wav = WORK / f"{audio.stem}.wav"
    if not (WORK / "htdemucs" / wav.stem / "vocals.wav").exists():
      subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(audio), "-ac", "2", "-ar", "44100", str(wav)],
                   check=True)
    vocals = WORK / "htdemucs" / wav.stem / "vocals.wav"
    if not vocals.exists():
        subprocess.run([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs", "-o", str(WORK),
                        str(wav)], check=True, capture_output=True)
    # 2) nhận dạng tiếng Việt + mốc thời gian từng từ
    from faster_whisper import WhisperModel
    model = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=8)
    pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(vocals), "-ac", "1", "-ar", "16000", "-f", "f32le",
                          "-"], capture_output=True, check=True).stdout
    segs, _ = model.transcribe(np.frombuffer(pcm, np.float32), language="vi", word_timestamps=True, vad_filter=False,
                               condition_on_previous_text=True)
    words = []
    for s in segs:
        for w in s.words or []:
            t = w.word.strip()
            if t:
                words.append({"w": t, "s": round(w.start, 3), "e": round(w.end, 3), "p": round(w.probability, 3)})
    cache.write_text(json.dumps(words, ensure_ascii=False))
    wav.unlink(missing_ok=True)
    return words


def syllables(word: str) -> int:
    return max(1, len(re.findall(r"\w+", word)))


def analyze(words: list[dict]) -> dict:
    # bỏ từ nhận dạng kém
    words = [w for w in words if w["p"] >= 0.4]
    gaps, rows = [], []
    for a, b in zip(words, words[1:]):
        g = b["s"] - a["e"]
        if g < -0.05 or g > 3.0:              # nhảy cảnh/lỗi mốc thời gian
            continue
        g = max(0.0, g)
        punct = "sent" if SENT_END.search(a["w"]) else "comma" if COMMA.search(a["w"]) else "none"
        nxt = re.sub(r"\W", "", b["w"].lower())
        rows.append({"gap": g, "punct": punct, "next": nxt, "dur_prev": a["e"] - a["s"]})
        gaps.append(g)
    gaps = np.array(gaps)

    # cụm đọc liền = chuỗi từ giữa 2 lần ngắt >= PAUSE_MIN
    groups, cur, cur_t0 = [], [], None
    for a, b in zip(words, words[1:]):
        cur.append(a)
        if b["s"] - a["e"] >= PAUSE_MIN:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    g_syl = np.array([sum(syllables(w["w"]) for w in g) for g in groups])
    g_dur = np.array([g[-1]["e"] - g[0]["s"] for g in groups])
    ok = g_dur > 0.3
    artic = float(g_syl[ok].sum() / g_dur[ok].sum())              # âm tiết/giây khi đang nói (không tính nghỉ)
    span = words[-1]["e"] - words[0]["s"]
    overall = float(sum(syllables(w["w"]) for w in words) / span)  # âm tiết/giây tính cả nghỉ

    # kéo dài âm cuối cụm: thời lượng từ cuối cụm / thời lượng từ giữa cụm
    last = [g[-1]["e"] - g[-1]["s"] for g in groups if len(g) >= 3]
    mid = [w["e"] - w["s"] for g in groups if len(g) >= 3 for w in g[1:-1]]

    by_punct = defaultdict(list)
    for r in rows:
        by_punct[r["punct"]].append(r["gap"])
    # từ nào hay có ngắt TRƯỚC nó khi không có dấu câu
    before = defaultdict(lambda: [0, 0])
    for r in rows:
        if r["punct"] == "none" and r["next"]:
            before[r["next"]][0] += 1
            before[r["next"]][1] += r["gap"] >= PAUSE_MIN
    base_rate = float(np.mean([r["gap"] >= PAUSE_MIN for r in rows if r["punct"] == "none"]))
    cue_words = sorted(((w, c, k / c) for w, (c, k) in before.items() if c >= 15), key=lambda x: -x[2])

    def q(a, ps=(10, 25, 50, 75, 90)):
        a = np.asarray(a)
        return {f"p{p}": round(float(np.percentile(a, p)), 3) for p in ps} if len(a) else {}

    return {
        "n_words": len(words), "n_groups": len(groups),
        "articulation_syl_per_sec": round(artic, 2), "overall_syl_per_sec": round(overall, 2),
        "gap_hist": {
            "lien(<0.15s)": round(float((gaps < 0.15).mean()), 3),
            "ngan(0.15-0.35)": round(float(((gaps >= 0.15) & (gaps < 0.35)).mean()), 3),
            "vua(0.35-0.7)": round(float(((gaps >= 0.35) & (gaps < 0.7)).mean()), 3),
            "dai(>=0.7)": round(float((gaps >= 0.7).mean()), 3),
        },
        "pause_after": {k: {"n": len(v), "pause_rate": round(float(np.mean(np.array(v) >= PAUSE_MIN)), 3),
                            **q(v)} for k, v in by_punct.items()},
        "group_syllables": q(g_syl), "group_seconds": q(g_dur),
        "final_lengthening": round(float(np.median(last) / np.median(mid)), 2) if last and mid else None,
        "no_punct_pause_base_rate": round(base_rate, 3),
        "pause_before_words": [{"word": w, "n": c, "pause_rate": round(r, 3)} for w, c, r in cue_words[:25]],
    }


def main():
    files = sorted(AUDIO.glob("*.m4a")) + sorted(AUDIO.glob("*.webm")) + sorted(AUDIO.glob("*.opus"))
    per_style = defaultdict(list)
    offset = 0.0
    for f in files:
        import time; t0 = time.time(); print("•", f.name, end=" ", flush=True)
        ws = words_of(f)
        print(f"{len(ws)} từ, {time.time() - t0:.0f}s", flush=True)
        # nối các file liên tiếp, chèn 10s giả giữa 2 file (khoảng nghỉ > 3s bị bỏ qua khi thống kê)
        per_style[f.stem.rsplit("_", 1)[0]].extend({**w, "s": w["s"] + offset, "e": w["e"] + offset} for w in ws)
        offset += (ws[-1]["e"] if ws else 0) + 10.0
    stats = {"all": analyze([w for ws in per_style.values() for w in sorted(ws, key=lambda x: x["s"])])}
    for style, ws in per_style.items():
        stats[style] = analyze(ws)
    OUT.write_text(json.dumps(stats, ensure_ascii=False, indent=1))
    print(json.dumps(stats, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
