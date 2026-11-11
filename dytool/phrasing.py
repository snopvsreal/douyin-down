"""Quy tắc ngắt nghỉ khi đọc tiếng Việt — rút ra từ phân tích 12 video đọc thật
(review phim nam/nữ, thuyết minh hài TikTok, đọc truyện; ~10.600 từ, ~1.150 cụm đọc liền).
Xem research/analyze_prosody.py và data/research/prosody_stats.json.

Kết quả đo được:
- Tốc độ khi đang nói ~5.3 âm tiết/giây (đọc truyện 4.5). Tính cả nghỉ ~3.9.
- Cụm đọc liền (giữa 2 lần nghỉ >=150ms): trung vị 8 âm tiết (~1.6s), 90% <= 16 âm tiết.
- Nghỉ sau dấu phẩy ~0.34s, sau dấu chấm ~0.4-0.5s. Không có dấu câu thì 98.8% đọc liền.
- Âm tiết cuối cụm dài hơn ~11%.
- Hay nghỉ TRƯỚC: nhưng, nếu, sau (đó), dù, lúc, khi, vì, nên, mà, còn, và...
- Hay nghỉ SAU (tiểu từ cuối câu): ấy, vậy, sao, rồi, đó, này, gì, đi, mình...
- Hầu như KHÔNG nghỉ sau: một, cho, của, trong, đã, thì, với, có, là, không...
"""

import re

ARTIC_RATE = 5.29         # âm tiết/giây khi đang nói
COMMA_PAUSE = 0.32        # giây
SENT_PAUSE = 0.38
FINAL_LENGTHEN = 1.11
GROUP_MEDIAN = 8
GROUP_MAX = 16            # vượt quá -> bắt buộc ngắt

BREAK_BEFORE = ["nhưng", "nếu", "sau đó", "dù", "mặc dù", "lúc", "khi", "vì", "bởi vì", "nên", "cho nên",
                "mà", "còn", "và", "rồi", "thế là", "ngay lúc", "trong khi", "để", "hay là"]
BREAK_AFTER = {"ấy", "vậy", "sao", "rồi", "đó", "này", "gì", "đi", "mình", "nhé", "nha", "à", "ạ", "chứ", "luôn"}
NO_BREAK_AFTER = {"một", "cho", "của", "trong", "đã", "thì", "với", "có", "là", "không", "những", "các", "đang",
                  "sẽ", "bị", "được", "rất", "quá", "người", "cái", "con", "ở", "từ", "về", "vào", "ra", "tới"}

PUNCT_END = ".!?…"
PUNCT_MID = ",;:"


def syl(text: str) -> int:
    return len(re.findall(r"[\w']+", text))


def groups(text: str) -> list[str]:
    """Tách thành các cụm đọc liền theo dấu câu."""
    return [g.strip() for g in re.split(r"(?<=[,;:.!?…])\s+", text.strip()) if g.strip()]


def _norm(w: str) -> str:
    return re.sub(r"[^\w]", "", w.lower())


def fix_commas(text: str) -> str:
    """1) Bỏ dấu phẩy đứng sau từ không được ngắt (vd 'của, ...').
    2) Cụm dài > GROUP_MAX âm tiết: chèn dấu phẩy ở chỗ tự nhiên nhất (trước từ nối, sau tiểu từ),
       ưu tiên gần giữa cụm, không bao giờ sau từ cấm ngắt."""
    words = text.split()
    for i, w in enumerate(words[:-1]):
        if w.endswith(",") and _norm(w) in NO_BREAK_AFTER:
            words[i] = w[:-1]
    out: list[str] = []
    for g in groups(" ".join(words)):
        out.extend(_split_long(g.split()))
    return " ".join(out)


def _split_long(ws: list[str]) -> list[str]:
    if len(ws) <= GROUP_MAX:
        return ws
    best, best_score = None, -1e9
    for i in range(3, len(ws) - 3):                  # ngắt giữa ws[i-1] và ws[i], mỗi bên >= 3 âm tiết
        prev, nxt = _norm(ws[i - 1]), _norm(ws[i])
        two = nxt + " " + _norm(ws[i + 1]) if i + 1 < len(ws) else nxt
        if prev in NO_BREAK_AFTER:
            continue
        score = -abs(i - len(ws) / 2) / len(ws)       # gần giữa
        if nxt in BREAK_BEFORE or two in BREAK_BEFORE:
            score += 2
        if prev in BREAK_AFTER:
            score += 1.5
        if score > best_score:
            best, best_score = i, score
    if best is None:
        best = len(ws) // 2
    left = ws[:best]
    left[-1] = left[-1].rstrip(",;:") + ","
    return _split_long(left) + _split_long(ws[best:])


def natural_duration(text: str, rate: float = ARTIC_RATE) -> float:
    """Thời lượng đọc tự nhiên (giây) của 1 lượt nói, gồm nghỉ ở dấu câu bên trong và kéo dài âm cuối cụm."""
    gs = groups(text)
    t = 0.0
    for i, g in enumerate(gs):
        n = syl(g)
        t += (n - 1 + FINAL_LENGTHEN) / rate
        if i < len(gs) - 1:
            t += SENT_PAUSE if g[-1] in PUNCT_END else COMMA_PAUSE
    return t + 0.12          # đuôi tắt dần, tránh cụt chữ cuối


def max_syllables(seconds: float, rate: float = ARTIC_RATE) -> int:
    """Số âm tiết tối đa đọc vừa `seconds` giây ở tốc độ tự nhiên (đã trừ chỗ nghỉ ước lượng)."""
    n_pause = max(0, int(seconds * rate / GROUP_MEDIAN) - 1)
    return max(2, int((seconds - 0.12 - n_pause * COMMA_PAUSE) * rate))


PROMPT_RULES = f"""Quy tắc ngắt nghỉ (đo từ giọng đọc review phim/TikTok tiếng Việt thật):
- Người Việt đọc liền từng cụm ~5-12 âm tiết, tối đa {GROUP_MAX} âm tiết rồi mới nghỉ. Dấu phẩy = chỗ NGHỈ ngắn (~0.3s),
  dấu chấm = nghỉ dài hơn. KHÔNG có dấu câu = đọc LIỀN một hơi. Hãy đặt dấu câu đúng chỗ người đọc sẽ nghỉ.
- Ý liền mạch (chủ ngữ + vị ngữ, động từ + tân ngữ, cụm giới từ) phải nằm trong cùng một cụm, không chèn dấu phẩy.
- Chỗ nghỉ tự nhiên: TRƯỚC các từ nối nhưng / nếu / sau đó / dù / lúc / khi / vì / nên / mà / còn / rồi / thế là;
  SAU tiểu từ cuối câu ấy / vậy / sao / rồi / đó / này / gì / đi / nhé.
- TUYỆT ĐỐI không đặt dấu phẩy ngay sau: một, cho, của, trong, đã, thì, với, có, là, không, những, các, đang, sẽ, được.
- Viết số bằng chữ, không viết tắt, không dùng ký tự đặc biệt (để giọng đọc phát âm đúng)."""
