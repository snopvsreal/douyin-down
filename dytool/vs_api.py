"""Gọi API của backend VoiceStudio (cổng 3900) cho các trang VoiceStudio trong dytool (giao diện riêng của dytool,
không nhúng giao diện VoiceStudio): giọng đã lưu, nhân bản / thiết kế giọng, tạo giọng nói, lịch sử, hệ thống.

Mọi hàm ném VSError với lời nhắn tiếng Việt khi backend tắt / trả lỗi. Danh sách giọng được nhớ ra
data/vs_profiles.json để Cài đặt kênh vẫn hiện được giọng khi VoiceStudio đang tắt."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PROFILES_CACHE = ROOT / "data" / "vs_profiles.json"

LANGS = (("vi", "Tiếng Việt"), ("en", "Tiếng Anh"), ("zh", "Tiếng Trung"), ("yue", "Tiếng Quảng Đông"),
         ("ja", "Tiếng Nhật"), ("ko", "Tiếng Hàn"), ("th", "Tiếng Thái"), ("id", "Tiếng Indonesia"),
         ("ms", "Tiếng Mã Lai"), ("tl", "Tiếng Philippines"), ("km", "Tiếng Khmer"), ("lo", "Tiếng Lào"),
         ("hi", "Tiếng Hindi"), ("ar", "Tiếng Ả Rập"), ("ru", "Tiếng Nga"), ("uk", "Tiếng Ukraina"),
         ("fr", "Tiếng Pháp"), ("de", "Tiếng Đức"), ("es", "Tiếng Tây Ban Nha"), ("pt", "Tiếng Bồ Đào Nha"),
         ("it", "Tiếng Ý"), ("nl", "Tiếng Hà Lan"), ("pl", "Tiếng Ba Lan"), ("tr", "Tiếng Thổ Nhĩ Kỳ"),
         ("Auto", "Tự nhận theo văn bản"))


def _all_langs() -> tuple[tuple[str, str], ...]:
    """Mọi ngôn ngữ OmniVoice đọc được (~600, bảng omnivoice/utils/lang_map.py) -> ((mã ISO 639, tên tiếng Anh)…).
    Nạp đúng file bảng, không import gói omnivoice (kéo theo torch, chậm)."""
    import importlib.util
    try:
        spec = importlib.util.find_spec("omnivoice")
        loc = Path(next(iter(spec.submodule_search_locations))) / "utils" / "lang_map.py"
        sp = importlib.util.spec_from_file_location("_ov_lang_map", loc)
        mod = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(mod)
        names = {}
        for name, code in mod.LANG_NAME_TO_ID.items():
            names.setdefault(code, name.title())
    except Exception:
        return ()
    main = {k for k, _ in LANGS}
    return tuple(sorted(((c, n) for c, n in names.items() if c not in main), key=lambda x: x[1]))


ALL_LANGS = _all_langs()
LANG_NAMES = {**dict(ALL_LANGS), **dict(LANGS), "English": "Tiếng Anh", "Vietnamese": "Tiếng Việt"}
EFFECTS = (("broadcast", "Phát thanh — âm lượng đều, ấm, rõ"), ("raw", "Nguyên bản — không xử lý"),
           ("podcast", "Podcast — gần mic, nén mạnh"), ("cinematic", "Điện ảnh — vang nhẹ"),
           ("warm", "Ấm — dày tiếng trầm"), ("bright", "Sáng — trong, thoáng"))
# Thiết kế giọng (OmniVoice): mỗi nhóm chọn 1 (trống = để model tự quyết) -> ghép thành instruct tiếng Anh.
# Giọng vùng miền tiếng Trung và giọng tiếng Anh theo nước không dùng chung được.
GENDERS = (("male", "Nam"), ("female", "Nữ"))
AGES = (("child", "Trẻ em"), ("teenager", "Thiếu niên"), ("young adult", "Thanh niên"),
        ("middle-aged", "Trung niên"), ("elderly", "Người già"))
PITCHES = (("very low pitch", "Rất trầm"), ("low pitch", "Trầm"), ("moderate pitch", "Vừa"),
           ("high pitch", "Cao"), ("very high pitch", "Rất cao"))
STYLES = (("whisper", "Thì thầm"),)
ACCENTS = (("american accent", "Mỹ"), ("british accent", "Anh"), ("australian accent", "Úc"),
           ("canadian accent", "Canada"), ("indian accent", "Ấn Độ"), ("chinese accent", "Trung Quốc"),
           ("korean accent", "Hàn Quốc"), ("japanese accent", "Nhật Bản"), ("portuguese accent", "Bồ Đào Nha"),
           ("russian accent", "Nga"))
DIALECTS = (("河南话", "Hà Nam"), ("陕西话", "Thiểm Tây"), ("四川话", "Tứ Xuyên"), ("贵州话", "Quý Châu"),
            ("云南话", "Vân Nam"), ("桂林话", "Quế Lâm"), ("济南话", "Tế Nam"), ("石家庄话", "Thạch Gia Trang"),
            ("甘肃话", "Cam Túc"), ("宁夏话", "Ninh Hạ"), ("青岛话", "Thanh Đảo"), ("东北话", "Đông Bắc"))
VD_GROUPS = (("gender", "Giới tính", GENDERS), ("age", "Tuổi", AGES), ("pitch", "Cao độ", PITCHES),
             ("style", "Kiểu nói", STYLES), ("accent", "Giọng tiếng Anh", ACCENTS),
             ("dialect", "Vùng miền Trung Quốc", DIALECTS))
# điểm khởi đầu (như trang Voice design của VoiceStudio) + biểu cảm chèn vào văn bản đầu câu
DESIGN_PRESETS = (
    ("Người dẫn chuyện uy quyền", {"gender": "male", "age": "middle-aged", "pitch": "low pitch", "accent": "british accent"}, ""),
    ("Trẻ con hào hứng", {"age": "child", "pitch": "high pitch"}, "[laughter] "),
    ("Thì thầm", {"age": "young adult", "style": "whisper"}, "[question-en] "),
    ("Cô gái ngạc nhiên", {"gender": "female", "age": "young adult", "pitch": "high pitch"}, "[surprise-wa] "),
    ("Cụ già kể chuyện", {"gender": "male", "age": "elderly", "pitch": "low pitch"}, "[sigh] "),
    ("Giọng Tứ Xuyên", {"dialect": "四川话"}, ""),
    ("Thuyết minh review (nam)", {"gender": "male", "age": "young adult", "pitch": "moderate pitch"}, ""),
    ("Thuyết minh review (nữ)", {"gender": "female", "age": "young adult", "pitch": "high pitch"}, ""),
)
# biểu cảm OmniVoice hiểu (đặt trong văn bản) + định dạng đọc VoiceStudio xử lý trước khi đọc
EXPRESSIONS = (("[laughter]", "Cười"), ("[sigh]", "Thở dài"), ("[confirmation-en]", "Ừ (đồng ý)"),
               ("[question-en]", "Hử? (hỏi)"), ("[question-ah]", "À? (hỏi)"), ("[question-oh]", "Ồ? (hỏi)"),
               ("[question-ei]", "Ê? (hỏi)"), ("[question-yi]", "Ủa? (hỏi)"), ("[surprise-ah]", "A! (ngạc nhiên)"),
               ("[surprise-oh]", "Ồ! (ngạc nhiên)"), ("[surprise-wa]", "Oa! (ngạc nhiên)"),
               ("[surprise-yo]", "Ô! (ngạc nhiên)"), ("[dissatisfaction-hnn]", "Hừm (không hài lòng)"))
PAUSES = (("[pause 300ms]", "Nghỉ 0,3 giây"), ("[pause 500ms]", "Nghỉ 0,5 giây"), ("[pause 1s]", "Nghỉ 1 giây"),
          ("[pause 2s]", "Nghỉ 2 giây"))
# tham số giọng nâng cao (/generate). None = mặc định của model
ADVANCED = (("num_step", "Số bước (Steps)", 8, 64, 1, "Nhiều bước = kỹ hơn, chậm hơn. Mặc định 32."),
            ("guidance_scale", "Bám mô tả / giọng mẫu (CFG)", 0.5, 5, 0.1, "Cao = giống giọng mẫu hơn nhưng dễ gắt. Mặc định 2."),
            ("t_shift", "t_shift", 0.05, 1, 0.05, "Phân bố bước khử nhiễu. Để trống nếu không chắc."),
            ("position_temperature", "Nhiệt độ vị trí", 0, 10, 0.1, "Cao = ngắt nhịp đa dạng hơn."),
            ("class_temperature", "Nhiệt độ âm", 0, 2, 0.05, "Cao = biến hoá hơn, dễ lỗi phát âm."),
            ("layer_penalty_factor", "Phạt tầng (layer penalty)", 0, 10, 0.1, "Để trống nếu không chắc."),
            ("audio_chunk_duration", "Văn bản dài: mỗi đoạn (giây)", 5, 60, 1,
             "Văn bản dài được đọc từng đoạn khoảng chừng này giây rồi nối. Mặc định 15."),
            ("audio_chunk_threshold", "Văn bản dài: chia khi dài hơn (giây)", 10, 600, 1,
             "Chỉ chia đoạn khi ước lượng bản đọc dài hơn ngưỡng này. Mặc định 30."))
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class VSError(Exception):
    pass


def api(cfg: dict) -> str:
    from .services import vs_api
    return vs_api(cfg)


def _call(cfg: dict, method: str, path: str, timeout: float = 8.0, **kw) -> httpx.Response:
    try:
        r = httpx.request(method, api(cfg) + path, timeout=httpx.Timeout(timeout, connect=1.0), **kw)
    except httpx.ConnectError as e:
        raise VSError("Máy chủ giọng nói đang tắt — bấm Bật ở trang Giọng nói rồi thử lại") from e
    except httpx.TimeoutException as e:
        raise VSError("Máy chủ giọng nói trả lời quá lâu — có thể đang bận đọc / nạp model, thử lại sau ít phút") from e
    except httpx.HTTPError as e:
        raise VSError(f"Không gọi được máy chủ giọng nói: {e}") from e
    if r.status_code >= 400:
        try:
            d = r.json().get("detail")
            msg = d if isinstance(d, str) else json.dumps(d, ensure_ascii=False)
        except ValueError:
            msg = r.text
        raise VSError(f"Máy chủ giọng nói báo lỗi {r.status_code}: {(msg or '').strip()[:300]}")
    return r


def check_id(x: str) -> str:
    if not ID_RE.match(x or ""):
        raise VSError("Mã không hợp lệ")
    return x


# ---------------- giọng đã lưu (profiles) ----------------

def profiles(cfg: dict) -> list[dict]:
    """Giọng đã lưu, mới nhất trước. Lưu kèm bản nhớ (tên) cho Cài đặt kênh."""
    data = _call(cfg, "GET", "/profiles").json()
    items = data.get("profiles", data) if isinstance(data, dict) else data
    items = sorted(items or [], key=lambda p: -(p.get("created_at") or 0))
    try:
        PROFILES_CACHE.parent.mkdir(parents=True, exist_ok=True)
        PROFILES_CACHE.write_text(json.dumps([{"id": p.get("id"), "name": p.get("name"),
                                               "instruct": p.get("instruct") or "", "language": p.get("language")}
                                              for p in items], ensure_ascii=False), "utf-8")
    except OSError:
        pass
    return items


def cached_profiles() -> list[dict]:
    try:
        v = json.loads(PROFILES_CACHE.read_text("utf-8-sig"))
        return v if isinstance(v, list) else []
    except (OSError, ValueError):
        return []


def profile_audio(cfg: dict, pid: str) -> bytes:
    return _call(cfg, "GET", f"/profiles/{check_id(pid)}/audio", timeout=20).content


def rename_profile(cfg: dict, pid: str, name: str) -> None:
    name = clean_name(name)
    if any(p.get("name", "").lower() == name.lower() and p.get("id") != pid for p in profiles(cfg)):
        raise VSError(f"Đã có giọng tên '{name}'")
    _call(cfg, "PUT", f"/profiles/{check_id(pid)}", json={"name": name})


def delete_profile(cfg: dict, pid: str) -> None:
    _call(cfg, "DELETE", f"/profiles/{check_id(pid)}")


def clean_name(name: str) -> str:
    name = " ".join((name or "").split())[:40]
    if not name:
        raise VSError("Đặt tên cho giọng")
    return name


def _unique(cfg: dict, name: str) -> str:
    name = clean_name(name)
    if any(p.get("name", "").lower() == name.lower() for p in profiles(cfg)):
        raise VSError(f"Đã có giọng tên '{name}' — đặt tên khác")
    return name


def transcribe(cfg: dict, audio: bytes, filename: str, language: str = "vi") -> str:
    """Nghe file -> chữ (Whisper của VoiceStudio). Dùng làm lời mẫu khi nhân bản giọng mà người dùng không gõ lời."""
    r = _call(cfg, "POST", "/transcribe", timeout=300, files={"audio": (filename, audio)},
              data={"language": language} if language and language != "Auto" else {})
    d = r.json()
    return (d.get("text") if isinstance(d, dict) else str(d) or "").strip()


def clone(cfg: dict, name: str, audio: bytes, filename: str, ref_text: str, language: str = "vi") -> dict:
    """Nhân bản giọng từ 1 file ghi âm (3–30 giây, 1 người nói, không nhạc)."""
    name = _unique(cfg, name)
    if not audio:
        raise VSError("Chọn file ghi âm giọng mẫu")
    if len(audio) > 25 * 1024 * 1024:
        raise VSError("File quá lớn (tối đa 25 MB) — cắt còn 5–30 giây")
    ref_text = (ref_text or "").strip() or transcribe(cfg, audio, filename, language)
    r = _call(cfg, "POST", "/profiles", timeout=120,
              data={"name": name, "ref_text": ref_text, "language": language, "kind": "clone"},
              files={"ref_audio": (filename or "voice.wav", audio)})
    return r.json()


REF_TEXT = ("Xin chào các bạn, hôm nay mình sẽ kể cho các bạn nghe một câu chuyện rất thú vị. "
            "Nhớ theo dõi đến cuối video nhé.")


def design_preview(cfg: dict, instruct: str, seed: int, text: str = "", language: str = "vi") -> bytes:
    """Đọc 1 câu bằng giọng thiết kế theo mô tả (chưa lưu). Cùng mô tả + seed -> cùng giọng."""
    body = {"model": "omnivoice", "voice": "default", "input": (text or REF_TEXT)[:400], "language": language,
            "seed": int(seed), "response_format": "wav", "instructions": instruct}
    return _call(cfg, "POST", "/v1/audio/speech", timeout=300, json=body).content


def design_save(cfg: dict, name: str, instruct: str, seed: int, language: str = "vi") -> dict:
    """Lưu giọng thiết kế thành giọng cố định: đọc câu mẫu theo mô tả + seed rồi nhân bản đúng câu đó."""
    name = _unique(cfg, name)
    wav = design_preview(cfg, instruct, seed, REF_TEXT, language)
    r = _call(cfg, "POST", "/profiles", timeout=120,
              data={"name": name, "ref_text": REF_TEXT, "language": language, "kind": "clone",
                    "instruct": instruct, "seed": str(int(seed))},
              files={"ref_audio": ("design.wav", wav, "audio/wav")})
    return r.json()


def instruct_of(gender: str = "", age: str = "", pitch: str = "", extra: str = "", style: str = "",
                accent: str = "", dialect: str = "") -> str:
    """Ghép mô tả giọng: chỉ nhận giá trị trong danh sách; giọng vùng tiếng Trung và giọng tiếng Anh không đi cùng."""
    if dialect and accent:
        accent = ""
    parts = [v for v, opts in ((gender, GENDERS), (age, AGES), (pitch, PITCHES), (style, STYLES),
                               (accent, ACCENTS), (dialect, DIALECTS)) if v and v in dict(opts)]
    extra = re.sub(r"[^\w ,.'-]", "", extra or "")[:120].strip()
    return ", ".join(parts + ([extra] if extra else []))


def describe(cfg: dict, text: str) -> dict:
    """Mô tả tự do (tiếng Anh) -> các đặc điểm giọng VoiceStudio hiểu được. Trả {gender, age, pitch, style, accent,
    dialect, instruct, unmatched}."""
    text = (text or "").strip()[:300]
    if not text:
        raise VSError("Gõ mô tả giọng (tiếng Anh, vd. warm elderly male storyteller)")
    d = _call(cfg, "POST", "/design/describe", json={"description": text}).json()
    a = d.get("attrs") or {}
    pick = lambda v: "" if not v or v == "Auto" else v
    return {"gender": pick(a.get("Gender")), "age": pick(a.get("Age")), "pitch": pick(a.get("Pitch")),
            "style": pick(a.get("Style")), "accent": pick(a.get("EnglishAccent")),
            "dialect": pick(a.get("ChineseDialect")), "instruct": d.get("instruct") or "",
            "unmatched": d.get("unmatched") or []}


# ---------------- tạo giọng nói + lịch sử ----------------

def _num(v, lo: float, hi: float, as_int: bool = False):
    """Ô số tuỳ chọn: trống / sai -> None; có -> kẹp trong [lo, hi]."""
    try:
        x = float(str(v).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None
    x = max(lo, min(hi, x))
    return int(round(x)) if as_int else x


def generate(cfg: dict, text: str, profile_id: str = "", instruct: str = "", language: str = "vi",
             speed: float = 1.0, seed: int | None = None, effect: str = "broadcast", num_step=None,
             duration=None, ref_audio: bytes | None = None, ref_name: str = "", ref_text: str = "",
             advanced: dict | None = None, denoise: bool = True, postprocess: bool | None = None,
             pronounce: bool = True, record: bool = True) -> tuple[bytes, dict]:
    """Đọc văn bản (ghi vào Lịch sử của VoiceStudio). Văn bản dài được VoiceStudio tự chia đoạn rồi nối.
    Giọng: profile_id (giọng đã lưu) | ref_audio (file ghi âm dùng 1 lần, kèm lời trong file nếu có) | instruct (thiết
    kế). Trả (wav, thông tin: id, giây, thời gian tạo, seed)."""
    text = (text or "").strip()
    if not text:
        raise VSError("Gõ văn bản cần đọc")
    if len(text) > 20000:
        raise VSError("Văn bản quá dài (tối đa 20.000 ký tự mỗi lần)")
    data = {"text": text, "language": language or "vi", "speed": str(max(0.5, min(2.0, float(speed or 1)))),
            "effect_preset": effect if effect in dict(EFFECTS) else "broadcast",
            "denoise": "true" if denoise else "false", "pronounce": "true" if pronounce else "false",
            "record": "true" if record else "false"}
    files = None
    if profile_id:
        data["profile_id"] = check_id(profile_id)
    elif ref_audio:
        if len(ref_audio) > 25 * 1024 * 1024:
            raise VSError("File giọng mẫu quá lớn (tối đa 25 MB) — 5–15 giây là đủ")
        files = {"ref_audio": (ref_name or "ref.wav", ref_audio)}
        if ref_text.strip():
            data["ref_text"] = ref_text.strip()[:1000]
    if instruct and not profile_id:
        data["instruct"] = instruct
    if seed is not None:
        data["seed"] = str(int(seed))
    if postprocess is not None:
        data["postprocess_output"] = "true" if postprocess else "false"
    adv = dict(advanced or {})
    if num_step:
        adv.setdefault("num_step", num_step)
    for key, _label, lo, hi, _step, _hint in ADVANCED:
        v = _num(adv.get(key), lo, hi, as_int=key == "num_step")
        if v is not None:
            data[key] = str(v)
    d = _num(duration, 0.3, 600)
    if d:
        data["duration"] = str(round(d, 2))
    r = _call(cfg, "POST", "/generate", timeout=1800, data=data, files=files)
    h = r.headers
    meta = {"id": h.get("x-audio-id") or "", "duration": _num(h.get("x-audio-duration"), 0, 1e6),
            "gen": _num(h.get("x-gen-time"), 0, 1e6), "seed": h.get("x-seed") or ""}
    return r.content, meta


def save_take(cfg: dict, hid: str, name: str) -> dict:
    """Lưu 1 bản đọc (lịch sử) thành giọng cố định: chính bản đọc đó làm giọng mẫu, lời của nó làm lời mẫu."""
    name = _unique(cfg, name)
    take = next((h for h in history(cfg) if h.get("id") == hid), None)
    if not take:
        raise VSError("Không còn bản đọc này trong lịch sử")
    audio, _mime = history_audio(cfg, hid)
    r = _call(cfg, "POST", "/profiles", timeout=120,
              data={"name": name, "ref_text": take.get("text") or "", "language": take.get("language") or "vi",
                    "kind": "clone", "instruct": take.get("instruct") or ""},
              files={"ref_audio": (f"{hid}.wav", audio, "audio/wav")})
    return r.json()


# ---------------- từ điển phát âm (VoiceStudio áp dụng trước khi đọc, mọi giọng) ----------------

def pron_list(cfg: dict) -> list[dict]:
    d = _call(cfg, "GET", "/pronunciation").json()
    return d.get("entries", d) if isinstance(d, dict) else d


def pron_add(cfg: dict, term: str, replacement: str, language: str = "*") -> None:
    term, replacement = " ".join((term or "").split())[:100], " ".join((replacement or "").split())[:200]
    if not term or not replacement:
        raise VSError("Gõ cả từ và cách đọc")
    _call(cfg, "POST", "/pronunciation", json={"term": term, "replacement": replacement, "type": "respelling",
                                               "language": language or "*", "enabled": True})


def pron_toggle(cfg: dict, eid: str, on: bool) -> None:
    _call(cfg, "PUT", f"/pronunciation/{check_id(str(eid))}", json={"enabled": bool(on)})


def pron_delete(cfg: dict, eid: str) -> None:
    _call(cfg, "DELETE", f"/pronunciation/{check_id(str(eid))}")


def pron_test(cfg: dict, text: str, language: str = "vi") -> str:
    d = _call(cfg, "POST", "/pronunciation/test", json={"text": (text or "")[:1000], "language": language}).json()
    return d.get("substituted") or d.get("text") or d.get("input") or ""


def history(cfg: dict) -> list[dict]:
    items = _call(cfg, "GET", "/history").json()
    items = items.get("items", items) if isinstance(items, dict) else items
    return sorted(items or [], key=lambda h: -(h.get("created_at") or 0))


def history_audio(cfg: dict, hid: str) -> tuple[bytes, str]:
    """File âm thanh 1 mục lịch sử. Thử .wav gốc (audio_path), không có thì bản .opus."""
    hid = check_id(hid)
    for path, mime in ((f"/audio/{hid}.wav", "audio/wav"), (f"/audio/{hid}.opus", "audio/ogg")):
        try:
            return _call(cfg, "GET", path, timeout=30).content, mime
        except VSError:
            continue
    raise VSError("Không lấy được file âm thanh của mục này")


def star(cfg: dict, hid: str, on: bool) -> None:
    _call(cfg, "PUT", f"/history/{check_id(hid)}/starred", json={"starred": bool(on)})


def delete_history(cfg: dict, hid: str) -> None:
    _call(cfg, "DELETE", f"/history/{check_id(hid)}")


def clear_history(cfg: dict) -> None:
    _call(cfg, "DELETE", "/history")


# ---------------- hệ thống ----------------

def sysinfo(cfg: dict) -> dict:
    return _call(cfg, "GET", "/sysinfo", timeout=4).json()


def offload(cfg: dict) -> bool:
    return bool(_call(cfg, "GET", "/api/settings/perf/offload-after-generation", timeout=4).json().get("enabled"))


def set_offload(cfg: dict, on: bool) -> None:
    _call(cfg, "PUT", "/api/settings/perf/offload-after-generation", json={"enabled": bool(on)})


def use_gpu(cfg: dict) -> None:
    """Bỏ chế độ dự phòng CPU (card đồ hoạ lỗi lần trước) — máy chủ nạp lại model lên GPU."""
    _call(cfg, "POST", "/model/use-gpu", timeout=15)


def flush_memory(cfg: dict) -> dict:
    r = _call(cfg, "POST", "/system/flush-memory", timeout=60)
    try:
        return r.json()
    except ValueError:
        return {}


def ago(ts) -> str:
    try:
        s = time.time() - float(ts)
    except (TypeError, ValueError):
        return ""
    if s < 60:
        return "vừa xong"
    if s < 3600:
        return f"{int(s // 60)} phút trước"
    if s < 86400:
        return f"{int(s // 3600)} giờ trước"
    return f"{int(s // 86400)} ngày trước"
