"""Lồng tiếng Việt: tách giọng gốc khỏi nhạc nền (Demucs) -> đọc từng câu tiếng Việt bằng VoiceStudio
(API /v1/audio/speech chạy cục bộ) -> co giãn cho khớp thời lượng câu gốc -> trộn với nhạc nền."""

import json
import subprocess
import sys
from pathlib import Path

import httpx


class DubError(RuntimeError):
    pass


def separate(video: Path, work: Path) -> tuple[Path, Path]:
    """Tách thành (giọng nói, nhạc nền) bằng Demucs htdemucs — cùng model VoiceStudio dùng nội bộ."""
    out_dir = work / "sep"
    wav = work / f"{video.stem}_src.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "2", "-ar", "44100",
                    str(wav)], check=True)
    base = [sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs", "-o", str(out_dir)]
    # Demucs tự dùng GPU nếu torch có CUDA (bản cài "gpu", máy có card NVIDIA). Lỗi phía GPU (hết VRAM — card 4 GB
    # dùng chung với VoiceStudio, driver…) -> chạy lại bằng CPU thay vì làm hỏng cả video
    r = subprocess.run(base + [str(wav)], capture_output=True)
    if r.returncode != 0:
        from . import progress
        progress.check()               # bị nút Dừng tắt -> không chạy lại bằng CPU
        err = r.stderr.decode("utf-8", "replace")
        if any(w in err for w in ("CUDA", "cuda", "cuDNN", "out of memory")):
            print("Demucs lỗi trên GPU -> chạy lại bằng CPU", flush=True)
            r = subprocess.run(base + ["-d", "cpu", str(wav)], capture_output=True)
        if r.returncode != 0:
            raise subprocess.CalledProcessError(r.returncode, r.args, r.stdout, r.stderr)
    stem_dir = out_dir / "htdemucs" / wav.stem
    wav.unlink(missing_ok=True)
    return stem_dir / "vocals.wav", stem_dir / "no_vocals.wav"


def _duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True, check=True).stdout
    return float(out.strip() or 0)


def page_voices(s: dict) -> dict:
    """Giọng nam / nữ của 1 kênh (cài đặt kênh): chọn sẵn trong thư viện VoiceStudio -> dùng đúng giọng đó; không
    thì giọng thiết kế theo mô tả + seed — tên gắn với nội dung mô tả nên đổi mô tả / seed là tự tạo giọng mới."""
    import hashlib
    seed = int(s.get("voice_seed") or 7)
    tag = hashlib.md5(f"{s.get('voice_male', '')}|{s.get('voice_female', '')}|{seed}".encode()).hexdigest()[:6]
    out = {"male": {"name": f"dy-nam-{tag}", "instruct": s.get("voice_male", ""), "seed": seed},
           "female": {"name": f"dy-nu-{tag}", "instruct": s.get("voice_female", ""), "seed": seed + 4}}
    for g in ("male", "female"):
        picked = (s.get(f"voice_{g}_profile") or "").strip()
        if picked:
            out[g] = {"name": picked, "fixed": True}
    return out


class VoiceStudio:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        from .services import vs_api
        self.api = vs_api(cfg)
        self.http = httpx.Client(timeout=300)
        self.voice = cfg.get("voice", "dytool-vi")

    def check(self) -> None:
        try:
            self.http.get(self.api + "/health", timeout=5).raise_for_status()
        except httpx.HTTPError as e:
            raise DubError(f"Không kết nối được máy chủ giọng nói tại {self.api}. Bật ở chip Giọng nói trên cùng rồi thử lại.") from e

    REF_TEXT = ("Xin chào các bạn, hôm nay mình sẽ kể cho các bạn nghe một câu chuyện rất thú vị. "
                "Nhớ theo dõi đến cuối video nhé.")

    def voice_spec(self, gender: str | None) -> dict:
        """Chọn cấu hình giọng theo giới tính (voices.male / voices.female), không có thì dùng giọng chung."""
        voices = self.cfg.get("voices") or {}
        if gender in voices:
            return voices[gender]
        return {"name": self.cfg.get("voice", "dytool-vi"), "instruct": self.cfg.get("voice_instruct", ""),
                "seed": self.cfg.get("voice_seed", 42), "ref_audio": self.cfg.get("voice_ref_audio", ""),
                "ref_text": self.cfg.get("voice_ref_text", "")}

    def ensure_voice(self, work: Path, spec: dict) -> str:
        """Đảm bảo có voice profile cố định. Không có giọng mẫu thì OmniVoice sinh giọng NGẪU NHIÊN mỗi lần gọi
        -> mỗi câu một giọng. Nên: tạo 1 câu mẫu (thiết kế theo instruct + seed cố định, hoặc file giọng của bạn)
        rồi clone giọng đó cho mọi câu."""
        name = spec["name"]
        profiles = self.http.get(self.api + "/profiles").json()
        profiles = profiles.get("profiles", profiles) if isinstance(profiles, dict) else profiles
        if any(p.get("name", "").lower() == name.lower() or p.get("id") == name for p in profiles):
            return name
        if spec.get("fixed"):        # giọng chọn sẵn trong thư viện (Cài đặt kênh) đã bị xoá -> không tự sinh giọng khác
            raise DubError(f"Giọng '{name}' không còn trong Giọng đã lưu: chọn giọng khác ở Cài đặt kênh › Edit & lồng tiếng")
        ref_path = spec.get("ref_audio")          # file giọng mẫu của bạn (tuỳ chọn, cần có quyền dùng giọng)
        ref_text = spec.get("ref_text", "")
        if not ref_path:
            ref_text = self.REF_TEXT
            body = {"model": self.cfg.get("model", "omnivoice"), "voice": "default", "input": ref_text,
                    "language": "vi", "seed": int(spec.get("seed", 42)), "response_format": "wav"}
            if spec.get("instruct"):
                body["instructions"] = spec["instruct"]
            r = self.http.post(self.api + "/v1/audio/speech", json=body)
            if r.status_code != 200:
                raise DubError(f"Không tạo được giọng mẫu: {r.text[:300]}")
            ref_path = work / f"{name}_ref.wav"
            Path(ref_path).write_bytes(r.content)
        with open(ref_path, "rb") as f:
            r = self.http.post(self.api + "/profiles", data={"name": name, "ref_text": ref_text, "language": "vi",
                                                              "kind": "clone", "instruct": spec.get("instruct", "")},
                               files={"ref_audio": (Path(ref_path).name, f, "audio/wav")})
        if r.status_code >= 300:
            raise DubError(f"Không tạo được voice profile: {r.text[:300]}")
        return name

    def speak(self, text: str, dest: Path, duration: float | None = None, voice: str | None = None) -> Path:
        body = {
            "model": self.cfg.get("model", "omnivoice"),
            "voice": voice or self.voice,
            "input": text,
            "language": self.cfg.get("language", "vi"),
            "seed": int(self.cfg.get("voice_seed", 42)),
            "response_format": "wav",
        }
        # Truyền thời lượng mong muốn: không có thì OmniVoice ước lượng thiếu cho tiếng Việt -> CỤT CHỮ CUỐI.
        if duration:
            body["duration"] = round(duration, 2)
        if self.cfg.get("num_step"):
            body["num_step"] = int(self.cfg["num_step"])
        r = self.http.post(self.api + "/v1/audio/speech", json=body)
        if r.status_code != 200:
            raise DubError(f"Máy chủ giọng nói lỗi {r.status_code}: {r.text[:300]}")
        raw = dest.with_suffix(".raw.wav")
        raw.write_bytes(r.content)
        # chỉ cắt lặng ĐẦU; đuôi chỉ bỏ phần im lặng hẳn (-60dB) dài hơn 0.15s để giữ âm cuối tắt dần
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af",
                        "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.02,"
                        "areverse,silenceremove=start_periods=1:start_threshold=-60dB:start_silence=0.15,areverse",
                        str(dest)], check=True)
        raw.unlink(missing_ok=True)
        return dest


def group_utterances(segments: list[dict], max_gap: float = 0.35, max_len: float = 10.0) -> list[dict]:
    """Gom các đoạn Whisper liên tiếp thành 1 LƯỢT NÓI để đọc liền một hơi:
    khoảng lặng giữa 2 đoạn < max_gap (người nói chưa dừng) và tổng độ dài <= max_len."""
    utts: list[dict] = []
    for s in segments:
        if utts and s["start"] - utts[-1]["end"] < max_gap and s["end"] - utts[-1]["start"] <= max_len:
            u = utts[-1]
            u["end"] = s["end"]
            u["text"] += s["text"]
            u["asr"] += s.get("asr", "")
            u["parts"].append(s)
        else:
            utts.append({"start": s["start"], "end": s["end"], "text": s["text"], "asr": s.get("asr", ""),
                         "parts": [s]})
    return utts


def make_line(vs: VoiceStudio, text: str, window: float, clip: Path, cfg: dict, sub_cfg: dict,
              voice: str) -> tuple[str, float]:
    """Đọc 1 lượt nói vừa `window` giây mà KHÔNG tua/cắt audio:
    thời lượng tự nhiên (theo số liệu giọng thật) -> cho phép đọc nhanh hơn tối đa max_rate_boost
    (qua tham số duration của VoiceStudio) -> vẫn dài thì nhờ Claude rút gọn rồi đọc lại."""
    from . import phrasing
    from .subtitles import shorten
    boost = float(cfg.get("max_rate_boost", 1.2))
    for attempt in range(int(cfg.get("max_shorten", 2)) + 1):
        text = phrasing.fix_commas(text)
        nat = phrasing.natural_duration(text)
        dur = max(min(nat, window), nat / boost)
        if dur <= window + 0.05 or attempt == int(cfg.get("max_shorten", 2)):
            break
        text = shorten(text, phrasing.max_syllables(window * boost), sub_cfg)
    d = _duration(vs.speak(text, clip, dur, voice))
    return text, d


def split_cues(words: list[str], max_chars: int = 34, comma_chars: int = 14) -> list[list[int]]:
    """Chia câu thành các cụm phụ đề (mỗi cụm ≤ ~2 dòng): luôn tách ở cuối câu (. ! ? …), tách ở dấu phẩy
    khi cụm đã đủ dài, cụm quá max_chars thì lùi về chỗ ngắt đẹp nhất (dấu phẩy > trước chữ nối > không
    cắt đôi từ ghép). Không để cụm cuối chỉ 1-2 chữ."""
    from .subtitles import _LEAD, splits_word
    out, cur = [], []

    def chars(idx):
        return sum(len(words[k]) + 1 for k in idx) - 1

    for i, w in enumerate(words):
        cur.append(i)
        left = len(words) - 1 - i
        if left == 0:
            break
        if left <= 2:
            continue
        if (w[-1] in ".!?…" and len(cur) >= 2) or (w[-1] in ",;:" and chars(cur) >= comma_chars):
            out.append(cur)
            cur = []
        elif chars(cur) > max_chars and len(cur) >= 4:
            # quá dài: chọn chỗ ngắt tốt nhất trong nửa sau cụm
            best, score = len(cur) - 1, -1
            for p in range(len(cur) // 2, len(cur)):
                j = cur[p]                          # ngắt TRƯỚC words[j]
                s = 3 if words[j - 1][-1] in ",;:" else 2 if words[j].lower() in _LEAD else \
                    0 if splits_word(words, j) else 1
                if s >= score:
                    best, score = p, s
            out.append(cur[:best])
            cur = cur[best:]
    if cur:
        out.append(cur)
    return [x for x in out if x]


def align_subtitles(clip: Path, offset: float, text: str) -> list[dict]:
    """Phụ đề khớp đúng giọng đọc: Whisper nghe lại clip TTS lấy mốc thời gian từng từ, chia cụm bằng
    split_cues. Ngắt dòng trong cụm do subtitles.write_ass làm theo độ rộng chữ. Không nhận dạng được
    thì chia đều."""
    from .subtitles import word_timestamps
    words = text.split()
    times = word_timestamps(clip, len(words))
    lines = split_cues(words)
    out = []
    for idx in lines:
        t0, t1 = times[idx[0]][0], times[idx[-1]][1]
        out.append({"start": offset + t0, "end": offset + t1,
                    "text": " ".join(words[k] for k in idx).rstrip(",;:")})
    return out


def dub_audio(video: Path, segments: list[dict], work: Path, cfg: dict,
              sub_cfg: dict | None = None) -> tuple[Path, list[dict]]:
    """Lồng tiếng Việt. Trả về (file .m4a gồm nhạc nền + giọng Việt, các dòng phụ đề khớp giọng đọc).
    1. Gom đoạn Whisper thành lượt nói (đọc liền), nhận giọng nam/nữ từng lượt.
    2. Claude dịch từng lượt, có giới hạn âm tiết và quy tắc ngắt nghỉ học từ giọng đọc thật.
    3. OmniVoice đọc CẢ lượt trong 1 lần (ngữ điệu liền mạch), thời lượng = tốc độ người Việt thật.
    4. Xếp lịch tuần tự: không chồng giọng, nghỉ giữa các lượt như người thật, trễ tối đa max_drift."""
    from . import phrasing
    from .gender import detect_genders
    from .subtitles import translate_utterances
    sub_cfg = sub_cfg or {}
    vs = VoiceStudio(cfg)
    vs.check()
    from . import progress
    progress.step("Tách giọng gốc khỏi nhạc nền (Demucs)")
    vocals, background = separate(video, work)
    total = _duration(video)
    utts = group_utterances(segments, float(cfg.get("merge_gap", 0.35)), float(cfg.get("max_utterance", 10.0)))
    mode = cfg.get("gender", "auto")
    progress.step("Nhận giọng nam/nữ")
    genders = detect_genders(vocals, utts) if mode == "auto" else [mode] * len(utts)
    voice_names = {g: vs.ensure_voice(work, vs.voice_spec(g)) for g in set(genders)}

    max_drift = float(cfg.get("max_drift", 0.6))
    windows = [((utts[i + 1]["start"] if i + 1 < len(utts) else total) - u["start"]) for i, u in enumerate(utts)]
    progress.step("Dịch sang tiếng Việt (Claude)", f"{len(utts)} lượt nói")
    texts = translate_utterances([u["text"] for u in utts], windows, sub_cfg, asr=[u["asr"] for u in utts])
    progress.step("Lồng tiếng (OmniVoice)")

    clip_dir = work / f"{video.stem}_tts"
    clip_dir.mkdir(exist_ok=True)
    inputs, filters, labels, subs, plan = ["-i", str(background)], [], [], [], []
    cursor = 0.0
    for i, (u, text) in enumerate(zip(utts, texts)):
        if not text.strip():
            continue
        start = max(u["start"], cursor)
        if start >= total - 0.3:
            # các câu trước đọc dài đẩy câu này ra sát / quá cuối video: bỏ câu (trước: atrim độ dài âm -> ffmpeg lỗi
            # cả video)
            progress.detail(f"bỏ câu {i + 1}: hết thời lượng video")
            continue
        nxt = utts[i + 1]["start"] if i + 1 < len(utts) else total
        window = max(0.4, min(total, nxt + max_drift) - start - phrasing.COMMA_PAUSE * 0.5)
        clip = clip_dir / f"{i:03d}.wav"
        progress.detail(f"câu {i + 1}/{len(utts)}: {text[:60]}")
        text, d = make_line(vs, text, window, clip, cfg, sub_cfg, voice_names[genders[i]])
        d = min(d, total - start)
        if d <= 0.05:
            continue
        n = len(inputs) // 2
        inputs += ["-i", str(clip)]
        delay = int(start * 1000)
        filters.append(f"[{n}:a]atrim=0:{d:.3f},aresample=44100,adelay={delay}|{delay}[v{n}]")
        labels.append(f"[v{n}]")
        subs += align_subtitles(clip, start, text)
        plan.append({"start": round(start, 2), "end": round(start + d, 2), "src": [round(u["start"], 2), round(u["end"], 2)],
                     "zh": u["text"], "vi": text, "gender": genders[i], "voice": voice_names[genders[i]],
                     "rate_syl_s": round(phrasing.syl(text) / max(d, 0.1), 2)})
        # nghỉ giữa 2 lượt nói như người thật (~0.34s), nhưng không sớm hơn hình gốc
        cursor = start + d + phrasing.COMMA_PAUSE * 0.5

    progress.step("Trộn giọng Việt với nhạc nền")
    out = work / f"{video.stem}_dub.m4a"
    bg_vol = cfg.get("background_volume", 0.9)
    voice_vol = cfg.get("voice_volume", 1.0)
    keep = float(cfg.get("original_voice_volume", 0.0))
    if keep > 0:   # giữ lại chút giọng gốc nếu muốn
        inputs += ["-i", str(vocals)]
        k = len(inputs) // 2 - 1
        filters.append(f"[{k}:a]volume={keep}[orig]")
        labels.append("[orig]")
    parts = filters + [f"[0:a]volume={bg_vol}[bg]"]
    if labels:
        parts += [f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0,volume={voice_vol}[voice]",
                  "[bg][voice]amix=inputs=2:normalize=0:duration=first[out]"]
    else:
        parts += ["[bg]anull[out]"]
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(parts),
                    "-map", "[out]", "-t", f"{total:.3f}", "-c:a", "aac", "-b:a", "160k", str(out)], check=True)
    (work / f"{video.stem}_dub.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    return out, subs
