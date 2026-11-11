"""Tạo phụ đề tiếng Việt: nhận dạng giọng nói (faster-whisper) -> dịch -> file .ass."""

import json
import re
import subprocess
from functools import lru_cache
from pathlib import Path

HASHTAG = re.compile(r"#\S+")


def load_pcm(path: Path, sr: int = 16000):
    """Giải mã audio bằng ffmpeg -> numpy float32 mono (tránh lỗi PyAV của faster-whisper)."""
    import numpy as np
    pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(pcm, dtype=np.float32)


@lru_cache(maxsize=2)
def _whisper(model: str):
    from faster_whisper import WhisperModel
    return WhisperModel(model, device="cpu", compute_type="int8")


def transcribe(video: Path, work: Path, model: str = "small", lang: str = "zh", split: bool = True) -> list[dict]:
    """split=True: cắt câu ngắn để làm phụ đề. split=False: giữ nguyên câu Whisper (dùng cho lồng tiếng)."""
    import numpy as np
    # Giải mã audio bằng ffmpeg (tránh phụ thuộc phiên bản PyAV của faster-whisper)
    pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1",
                          "-ar", "16000", "-f", "f32le", "-"], capture_output=True, check=True).stdout
    audio = np.frombuffer(pcm, dtype=np.float32)
    if audio.size < 16000:
        return []
    segments, _ = _whisper(model).transcribe(audio, language=lang, vad_filter=True, word_timestamps=True,
                                             initial_prompt="以下是普通话的句子。")
    segments = list(segments)
    if split:
        return _split(segments)
    return [{"start": sg.start, "end": sg.end, "text": sg.text.strip()} for sg in segments if sg.text.strip()]


def word_timestamps(clip: Path, n_words: int) -> list[tuple[float, float]]:
    """Mốc (bắt đầu, kết thúc) của từng từ trong clip giọng đọc tiếng Việt, đúng n_words phần tử.
    Whisper nghe lại clip; số từ không khớp thì nội suy theo vị trí âm tiết trên đoạn có tiếng."""
    import numpy as np
    audio = load_pcm(clip)
    total = len(audio) / 16000
    segs, _ = _whisper("small").transcribe(audio, language="vi", word_timestamps=True)
    ws = [(w.start, w.end) for sg in segs for w in (sg.words or [])]
    if len(ws) == n_words:
        return ws
    t0, t1 = (ws[0][0], ws[-1][1]) if ws else (0.0, total)
    edges = np.linspace(t0, t1, n_words + 1)
    return [(float(edges[i]), float(edges[i + 1])) for i in range(n_words)]


PUNCT = set("，。！？；、,.!?;")


def _split(segments, max_chars: int = 14) -> list[dict]:
    """Tách câu dài thành các dòng ngắn (≤ max_chars chữ Hán) theo dấu câu, dựa trên mốc thời gian từng từ."""
    out = []
    for seg in segments:
        words = seg.words or []
        if not words:
            if seg.text.strip():
                out.append({"start": seg.start, "end": seg.end, "text": seg.text.strip()})
            continue
        cur, start = "", words[0].start
        for i, w in enumerate(words):
            cur += w.word
            last = i == len(words) - 1
            if last or w.word.strip()[-1:] in PUNCT or len(cur.strip()) >= max_chars:
                text = cur.strip().strip("".join(PUNCT))
                if text:
                    out.append({"start": start, "end": w.end, "text": text})
                cur = ""
                if not last:
                    start = words[i + 1].start
    return out


def syllables(text: str) -> int:
    """Số âm tiết tiếng Việt ~ số từ cách nhau bởi dấu cách."""
    return len(re.findall(r"[\w']+", text))


def budgets(durations: list[float], rate: float = 4.5) -> list[int]:
    """Số âm tiết tối đa cho mỗi câu = thời lượng (giây) x tốc độ nói tự nhiên (âm tiết/giây)."""
    return [max(2, int(d * rate)) for d in durations]


def translate(lines: list[str], cfg: dict, durations: list[float] | None = None) -> list[str]:
    """Dịch sang tiếng Việt. Nếu có durations (giây), Claude sẽ dịch gọn cho vừa thời lượng để lồng tiếng."""
    if not lines:
        return []
    if cfg.get("translator", "google") == "claude":
        limits = budgets(durations, cfg.get("syllables_per_sec", 4.5)) if durations else None
        return _translate_claude(lines, cfg, limits)
    from deep_translator import GoogleTranslator
    tr = GoogleTranslator(source="zh-CN", target="vi")
    return [t or "" for t in tr.translate_batch(lines)]


def _claude(cfg: dict, prompt: str) -> str:
    import anthropic
    # ưu tiên key trong config.yaml, nếu trống thì dùng biến môi trường ANTHROPIC_API_KEY
    client = anthropic.Anthropic(api_key=cfg.get("anthropic_api_key") or None)
    msg = client.messages.create(model=cfg.get("claude_model", "claude-sonnet-5-5"), max_tokens=8192,
                                 messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in msg.content if b.type == "text")


def _translate_claude(lines: list[str], cfg: dict, limits: list[int] | None = None) -> list[str]:
    if limits:
        items = [{"id": i, "zh": l, "max_am_tiet": n} for i, (l, n) in enumerate(zip(lines, limits))]
        prompt = (
            "Bạn dịch thoại video ngắn Douyin sang tiếng Việt để LỒNG TIẾNG. Mỗi câu có giới hạn "
            "`max_am_tiet` (số âm tiết/từ tiếng Việt tối đa) để đọc vừa khít thời lượng câu gốc.\n"
            "- Dịch thoát ý, tự nhiên như người Việt nói, giọng mạng xã hội.\n"
            "- BẮT BUỘC không vượt max_am_tiet; được lược từ đệm, chủ ngữ thừa, ý phụ để vừa.\n"
            "- Giữ nguyên số lượng và thứ tự câu; hiểu ngữ cảnh cả đoạn.\n"
            "Chỉ trả về mảng JSON các chuỗi, không giải thích.\n\n" + json.dumps(items, ensure_ascii=False)
        )
    else:
        prompt = (
            "Dịch các câu thoại tiếng Trung sau sang tiếng Việt tự nhiên, ngắn gọn, hợp làm phụ đề video "
            "mạng xã hội. Giữ nguyên số lượng và thứ tự. Chỉ trả về một mảng JSON các chuỗi.\n\n"
            + json.dumps(lines, ensure_ascii=False)
        )
    text = _claude(cfg, prompt)
    result = json.loads(text[text.find("["): text.rfind("]") + 1])
    if len(result) != len(lines):
        raise ValueError("Số câu dịch không khớp")
    return [str(x) for x in result]


def translate_utterances(lines: list[str], windows: list[float], cfg: dict,
                         asr: list[str] | None = None) -> list[str]:
    """Dịch để LỒNG TIẾNG: mỗi lượt nói 1 câu tiếng Việt vừa thời lượng, dấu câu đặt đúng chỗ người Việt nghỉ."""
    from . import phrasing
    if not lines:
        return []
    if cfg.get("translator", "claude") != "claude":
        return translate(lines, cfg, windows)
    items = [{"id": i, "zh": l, "giay": round(w, 2), "max_am_tiet": phrasing.max_syllables(w)}
             for i, (l, w) in enumerate(zip(lines, windows))]
    if asr and any(asr):
        for it, a in zip(items, asr):
            if a:
                it["zh_nghe"] = a
    prompt = (
        "Bạn là biên dịch lồng tiếng video ngắn Douyin sang tiếng Việt (giọng review/thuyết minh TikTok). "
        "Mỗi phần tử là MỘT LƯỢT NÓI liền mạch của nhân vật; dịch thành câu tiếng Việt tự nhiên như người Việt nói, "
        "hiểu ngữ cảnh cả đoạn, dịch thoát ý. Không vượt `max_am_tiet` âm tiết (để đọc vừa `giay` giây); "
        "được lược ý phụ.\n"
        "`zh` là phụ đề chữ trên video (OCR — thường đúng nhưng có thể sai/thiếu vài chữ); nếu có `zh_nghe` là "
        "lời nói nhận dạng từ giọng (có thể nghe nhầm từ đồng âm). Đối chiếu cả hai để hiểu đúng câu gốc rồi dịch.\n\n"
        + phrasing.PROMPT_RULES +
        "\n\nGiữ nguyên số lượng và thứ tự. Chỉ trả về mảng JSON các chuỗi.\n\n" + json.dumps(items, ensure_ascii=False)
    )
    text = _claude(cfg, prompt)
    result = json.loads(text[text.find("["): text.rfind("]") + 1])
    if len(result) != len(lines):
        raise ValueError("Số câu dịch không khớp")
    return [phrasing.fix_commas(str(x)) for x in result]


def shorten(text: str, max_syllables: int, cfg: dict) -> str:
    """Rút gọn 1 câu tiếng Việt cho <= max_syllables âm tiết, giữ ý chính."""
    prompt = (
        f"Viết lại câu thoại tiếng Việt sau ngắn hơn, tối đa {max_syllables} âm tiết, giữ ý chính, "
        f"văn nói tự nhiên. Chỉ trả về câu mới, không ngoặc kép, không giải thích.\n\n{text}"
    )
    out = _claude(cfg, prompt).strip().strip('"').strip()
    return out or text


def translate_desc(desc: str, cfg: dict) -> str:
    clean = HASHTAG.sub("", desc).strip()
    return translate([clean], cfg)[0] if clean else ""


def _ts(sec: float) -> str:
    cs = int(round(sec * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def write_ass(segments: list[dict], texts: list[str], path: Path, width: int, height: int, cfg: dict) -> Path:
    size = round(height * cfg.get("font_size_pct", 0.045))
    margin_v = round(height * cfg.get("margin_v_pct", 0.18))
    margin_h = round(width * 0.06)
    font = cfg.get("font", "Arial")
    # BorderStyle=3: nền hộp mờ phía sau chữ, giúp che phần sub cũ còn sót
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},&H00FFFFFF,&H00FFFFFF,&H60000000,&H60000000,1,0,0,0,100,100,0,0,3,{max(2, size // 6)},0,2,{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for seg, text in zip(segments, texts):
        text = text.replace("\n", " ").replace("{", "(").replace("}", ")").strip()
        if text:
            events.append(f"Dialogue: 0,{_ts(seg['start'])},{_ts(seg['end'])},Default,,0,0,0,,{text}")
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return path
