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
    """Hỏi AI. Không dùng API key nữa (người dùng yêu cầu): đi qua CLI đăng nhập bằng tài khoản — Claude / Codex /
    Gemini CLI, chọn ở Cài đặt › AI (ai.py). Tên hàm giữ nguyên cho các chỗ gọi cũ."""
    from . import ai
    return ai.ask(prompt)


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


FONTS_DIR = Path(__file__).parent / "assets" / "fonts"
DEFAULT_FONT = "Be Vietnam Pro ExtraBold"   # assets/fonts (OFL), FFmpeg nạp qua fontsdir
_FONT_FILES = {"Be Vietnam Pro ExtraBold": "BeVietnamPro-ExtraBold.ttf", "Be Vietnam Pro Bold": "BeVietnamPro-Bold.ttf"}
# chữ nối thường mở đầu vế sau -> ngắt dòng trước chúng đọc tự nhiên hơn
_LEAD = {"thì", "mà", "là", "và", "nhưng", "của", "để", "cho", "với", "khi", "nếu", "vì", "nên", "còn", "hay",
         "hoặc", "rồi", "lại", "cũng", "đã", "đang", "sẽ", "không", "chứ", "như", "ở", "trong", "từ", "bởi"}


_PUNCT = ",;:.!?…\"'()“”"


@lru_cache(maxsize=1)
def _compounds() -> frozenset:
    """Từ nhiều âm tiết (assets/vi_words.txt, từ điển vntk MIT) — để không ngắt dòng giữa 1 từ ghép."""
    f = Path(__file__).parent / "assets" / "vi_words.txt"
    try:
        return frozenset(f.read_text(encoding="utf-8").split("\n"))
    except OSError:
        return frozenset()


def splits_word(words: list[str], j: int) -> bool:
    """Ngắt giữa words[j-1] và words[j] có cắt đôi 1 từ ghép không (vd "bưu | kiện")."""
    import unicodedata
    if j <= 0 or j >= len(words) or words[j - 1][-1] in _PUNCT:
        return False
    comp = _compounds()
    lo = [unicodedata.normalize("NFC", w.strip(_PUNCT).lower()) for w in words[max(0, j - 3): j + 3]]
    k = min(j, 3)                                   # vị trí chỗ ngắt trong lo
    for a in range(max(0, k - 3), k):
        for b in range(k + 1, min(len(lo), a + 4) + 1):
            if " ".join(lo[a:b]) in comp:
                return True
    return False


def _measure(font: str, size: int):
    """Hàm đo độ rộng chữ (px) theo đúng font; không có Pillow / file font thì ước lượng."""
    f = _FONT_FILES.get(font)
    if f and (FONTS_DIR / f).exists():
        try:
            from PIL import ImageFont
            return ImageFont.truetype(str(FONTS_DIR / f), size).getlength
        except Exception:
            pass
    return lambda t: len(t) * size * 0.56


def wrap_line(text: str, max_w: float, measure, max_lines: int = 3, hard_w: float | None = None) -> list[str]:
    """Chia câu thành ít dòng nhất vừa max_w, các dòng dài gần bằng nhau; ưu tiên ngắt sau dấu câu /
    trước chữ nối, tránh dòng chỉ 1 chữ."""
    words = text.split()
    if len(words) < 2 or measure(text) <= max_w:
        return [text]
    space = measure(" ")
    wl = [measure(w) for w in words]
    n = len(words)

    def width(i, j):
        return sum(wl[i:j]) + space * (j - i - 1)

    best = [text]
    for lines in range(2, min(max_lines, n) + 1):
        # quy hoạch động: chia thành `lines` dòng, phạt lệch so với độ dài trung bình + chỗ ngắt xấu
        target = width(0, n) / lines
        inf = float("inf")
        cost = [[inf] * (n + 1) for _ in range(lines + 1)]
        back = [[0] * (n + 1) for _ in range(lines + 1)]
        cost[0][0] = 0.0
        for k in range(1, lines + 1):
            for j in range(k, n + 1):
                for i in range(k - 1, j):
                    if cost[k - 1][i] == inf:
                        continue
                    w = width(i, j)
                    c = ((w - target) / max_w) ** 2 + max(0.0, w - max_w) / max_w * 50
                    if j - i == 1:
                        c += 0.6                       # dòng chỉ 1 chữ
                    if j < n:
                        if words[j - 1][-1] in ",;:.!?…":
                            c -= 0.25                  # ngắt sau dấu câu
                        elif splits_word(words, j):
                            c += 0.8                   # cắt đôi từ ghép
                        elif words[j].lower() in _LEAD:
                            c -= 0.12                  # ngắt trước chữ nối
                    if cost[k - 1][i] + c < cost[k][j]:
                        cost[k][j], back[k][j] = cost[k - 1][i] + c, i
        out, j = [], n
        for k in range(lines, 0, -1):
            i = back[k][j]
            out.append(" ".join(words[i:j]))
            j = i
        best = out[::-1]
        # 2 dòng được lấn vào lề tới hard_w: dễ đọc hơn 3 dòng
        if all(measure(x) <= (hard_w or max_w if lines == 2 else max_w) for x in best):
            break
    return best


def _clean(text: str) -> str:
    text = text.replace("\n", " ").replace("\\", "/").replace("{", "(").replace("}", ")")
    text = re.sub(r"\s+", " ", text).strip()
    # phụ đề video ngắn không để dấu chấm / phẩy cuối dòng (giữ ? ! …)
    return re.sub(r"(?<![.…])[.,;:]+$", "", text).strip()


def sub_size(width: int, height: int, cfg: dict) -> int:
    pct = float(cfg.get("font_size_pct") or 0.046)
    if pct == 0.040:                        # mặc định cũ (cho Arial) -> cỡ hợp với font đậm
        pct = 0.046
    return round(min(height, width * 16 / 9) * pct)   # video ngang: tính theo khung dọc để chữ không quá to


def feather_px(band: tuple[float, float], width: int, height: int, cfg: dict) -> float:
    """Độ rộng (px) phần mờ dần ở mép trên / dưới dải che sub Trung. Lõi mờ đặc chỉ bằng đúng dải sub Trung;
    chữ Việt 2 dòng căn giữa dải tràn sang phần mờ dần nên không thấy cạnh khung."""
    core = (band[1] - band[0]) * height
    two_lines = 2 * sub_size(width, height, cfg) * 1.25
    return max(core * 0.35, (two_lines - core) / 2 + height * 0.012)


def write_ass(segments: list[dict], texts: list[str], path: Path, width: int, height: int, cfg: dict) -> Path:
    """Phụ đề kiểu video ngắn: chữ đậm trắng viền đen + bóng mềm (`style: outline`, mặc định) hoặc hộp nền
    mờ (`style: box`). Tự ngắt dòng cân đối theo độ rộng thật của font; dòng ngắn được giữ đủ lâu để đọc,
    khe hở nhỏ giữa 2 câu được lấp để chữ không nhấp nháy. cfg["band"] = (y1, y2) dải đã làm mờ -> khối
    chữ (1 hay 2 dòng) căn giữa dải."""
    font = cfg.get("font") or DEFAULT_FONT
    if font == "Arial":                     # giá trị mặc định cũ trong config.yaml
        font = DEFAULT_FONT
    size = sub_size(width, height, cfg)
    margin_v = round(height * cfg.get("margin_v_pct", 0.18))
    band = cfg.get("band")
    pos = f"{{\\an5\\pos({width // 2},{round((band[0] + band[1]) / 2 * height)})}}" if band else ""
    margin_h = round(width * 0.07)
    if cfg.get("style", "outline") == "box":
        # BorderStyle=3: hộp nền mờ phía sau chữ, che phần sub cũ còn sót
        look = f"&H00FFFFFF,&H00FFFFFF,&H50000000,&H50000000,0,0,0,0,100,100,0,0,3,{max(3, size // 4)},0"
    else:
        outline, shadow = max(2, round(size * 0.11)), max(1, round(size * 0.06))
        look = f"&H00FFFFFF,&H00FFFFFF,&H00101010,&H8C000000,0,0,0,0,100,100,0.5,0,1,{outline},{shadow}"
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{look},2,{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    measure = _measure(font, size)
    max_w = width - 2 * margin_h
    cues = sorted(((float(s["start"]), float(s["end"]), _clean(t)) for s, t in zip(segments, texts)),
                  key=lambda c: c[0])
    cues = [c for c in cues if c[2]]
    min_dur = float(cfg.get("min_duration", 0.9))
    events = []
    for i, (a, b, text) in enumerate(cues):
        nxt = cues[i + 1][0] if i + 1 < len(cues) else None
        b = max(b, a + min_dur)                 # dòng ngắn: giữ đủ lâu để kịp đọc
        if nxt is not None:
            if nxt - b < 0.35:                  # khe hở nhỏ: nối liền cho đỡ nháy
                b = nxt
            b = min(b, nxt)                     # không đè câu sau
        if b - a < 0.05:
            continue
        hard_w = width * 0.92
        chunks = [text]
        if len(wrap_line(text, max_w, measure, hard_w=hard_w)) > 2:
            # cần 3 dòng trở lên -> hiện thành nhiều lần, mỗi lần ≤ 2 dòng, chia giờ theo độ dài chữ
            chunks = [_clean(c) for c in wrap_line(text, max_w * 1.8, measure, max_lines=6)]
        total = sum(len(c) for c in chunks)
        t = a
        for c in chunks:
            e = b if c is chunks[-1] else t + (b - a) * len(c) / total
            events.append(f"Dialogue: 0,{_ts(t)},{_ts(e)},Default,,0,0,0,," + pos
                          + "\\N".join(wrap_line(c, max_w, measure, hard_w=hard_w)))
            t = e
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return path


def ensure_fonts(work: Path) -> str:
    """Chép font phụ đề vào work/fonts (FFmpeg chạy trong work -> fontsdir=fonts, khỏi escape đường dẫn)."""
    dst = work / "fonts"
    dst.mkdir(exist_ok=True)
    for f in _FONT_FILES.values():
        src, out = FONTS_DIR / f, dst / f
        if src.exists() and (not out.exists() or out.stat().st_size != src.stat().st_size):
            out.write_bytes(src.read_bytes())
    return "fonts"
