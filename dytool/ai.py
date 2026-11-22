"""AI cho dytool (dịch phụ đề, dịch lồng tiếng theo số âm tiết, rút gọn câu, từ khoá Douyin) qua CLI đăng nhập bằng
tài khoản — KHÔNG dùng API key (người dùng yêu cầu): Claude CLI (Claude Code), Codex CLI (ChatGPT), Gemini CLI (Google).

- Chọn CLI ở Cài đặt › AI, lưu ở data/ai.json {"provider": "codex"}.
- Cài: npm install -g --prefix <ROOT>/tools/ai-cli <gói> (Node.js có sẵn trên máy, không có thì tải Node bản zip chính thức
  vào tools/node, kiểm SHA256 theo SHASUMS256.txt của nodejs.org). CLI người dùng tự cài sẵn (PATH) cũng dùng được.
- Đăng nhập: mở 1 cửa sổ console riêng chạy lệnh đăng nhập của CLI (người dùng tự đăng nhập trong trình duyệt);
  đã đăng nhập = có file thông tin đăng nhập của CLI trong thư mục người dùng.
- Gọi: chạy CLI ở chế độ không tương tác, đề bài đưa qua stdin, trong 1 thư mục tạm trống (CLI không đọc dự án nào).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CLI_DIR = ROOT / "tools" / "ai-cli"          # npm --prefix: CLI dytool tự cài
NODE_DIR = ROOT / "tools" / "node"           # Node.js bản zip khi máy chưa có
HOME = Path.home()

PROVIDERS = {
    "claude": {"name": "Claude CLI", "by": "Anthropic · tài khoản Claude (Pro / Max)", "npm": "@anthropic-ai/claude-code",
               "bin": "claude", "login": ["auth", "login"], "creds": [HOME / ".claude" / ".credentials.json"],
               "status": ["auth", "status"],
               "login_hint": "Cửa sổ đăng nhập Claude mở ra và trình duyệt mở trang đăng nhập — đăng nhập bằng tài khoản Claude, "
                             "xong đóng cửa sổ đó."},
    "codex": {"name": "Codex CLI", "by": "OpenAI · tài khoản ChatGPT (Plus / Pro)", "npm": "@openai/codex",
              "bin": "codex", "login": ["login"], "creds": [HOME / ".codex" / "auth.json"], "status": ["login", "status"],
              "login_hint": "Trình duyệt mở trang đăng nhập ChatGPT — đăng nhập xong cửa sổ tự báo thành công."},
    "gemini": {"name": "Gemini CLI", "by": "Google · tài khoản Google (miễn phí có giới hạn)", "npm": "@google/gemini-cli",
               "bin": "gemini", "login": [], "creds": [HOME / ".gemini" / "oauth_creds.json"],
               "login_hint": "Cửa sổ Gemini mở ra: chọn \"Login with Google\" và đăng nhập trong trình duyệt. Xong gõ /quit để đóng."},
}

_jobs: dict[str, dict] = {}        # cài đặt đang chạy: provider -> {state: installing|done|error, msg}
_lock = threading.Lock()


class AIError(RuntimeError):
    pass


# ---------------- tìm / trạng thái ----------------

def _bin_dirs() -> list[Path]:
    return [CLI_DIR, NODE_DIR]


def exe(provider: str) -> str | None:
    """Đường dẫn CLI: bản dytool tự cài (tools/ai-cli) trước, rồi PATH của máy."""
    name = PROVIDERS[provider]["bin"]
    for d in _bin_dirs():
        for ext in (".cmd", ".exe", ""):
            p = d / f"{name}{ext}"
            if p.is_file():
                return str(p)
    return shutil.which(name)


_login_cache: dict[str, tuple[float, bool]] = {}


def logged_in(provider: str, fresh: bool = False) -> bool:
    """Đã đăng nhập? Hỏi chính CLI (`claude auth status` JSON loggedIn, `codex login status`) — file đăng nhập cũ / hết
    hạn vẫn còn trên đĩa nên chỉ xem file là không đủ. Gemini chưa có lệnh này -> xem file. Nhớ 20 giây."""
    info = PROVIDERS[provider]
    hit = _login_cache.get(provider)
    if hit and not fresh and time.time() - hit[0] < 20:
        return hit[1]
    files = any(p.is_file() and p.stat().st_size > 2 for p in info["creds"])
    ok = files
    e = exe(provider)
    if not e:
        ok = False
    elif info.get("status"):
        try:
            p = subprocess.run([e, *info["status"]], capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=30, env=_env(), cwd=str(HOME))
            out = (p.stdout or "") + (p.stderr or "")
            if provider == "claude":
                ok = '"loggedIn": true' in out.replace("\n", "")
            else:
                ok = p.returncode == 0 and "logged in" in out.lower() and "not logged in" not in out.lower()
        except (OSError, subprocess.SubprocessError):
            ok = files
    _login_cache[provider] = (time.time(), ok)
    return ok


def _env() -> dict:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(CLI_DIR), str(NODE_DIR), env.get("PATH", "")])
    env.pop("ANTHROPIC_API_KEY", None)                        # dùng tài khoản đã đăng nhập, không dùng key
    return env


def chosen() -> str | None:
    try:
        p = json.loads((DATA / "ai.json").read_text("utf-8-sig")).get("provider")
        return p if p in PROVIDERS else None
    except (OSError, ValueError):
        return None


def choose(provider: str) -> None:
    if provider not in PROVIDERS:
        raise AIError("Không có CLI này")
    DATA.mkdir(exist_ok=True)
    (DATA / "ai.json").write_text(json.dumps({"provider": provider}), "utf-8")


def active() -> str | None:
    """CLI sẽ dùng: cái người dùng chọn; chưa chọn thì cái đầu tiên đã cài + đăng nhập."""
    c = chosen()
    if c:
        return c
    return next((p for p in PROVIDERS if exe(p) and logged_in(p)), None)


def ready() -> bool:
    p = active()
    return bool(p and exe(p) and logged_in(p))


def status() -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(3) as pool:                      # mỗi CLI hỏi trạng thái mất ~1-2 s: hỏi song song
        login = dict(zip(PROVIDERS, pool.map(logged_in, PROVIDERS)))
    cur = active()
    out = []
    for key, info in PROVIDERS.items():
        e = exe(key)
        out.append({"key": key, **{k: info[k] for k in ("name", "by", "login_hint")}, "installed": bool(e),
                    "logged_in": login[key], "active": key == cur, "path": e or "",
                    "managed": bool(e) and Path(e).parent == CLI_DIR, "job": _jobs.get(key)})
    return out


# ---------------- cài đặt ----------------

def _npm() -> str:
    for p in (NODE_DIR / "npm.cmd",):
        if p.is_file():
            return str(p)
    npm = shutil.which("npm")
    if npm:
        return npm
    if sys.platform != "win32":
        raise AIError("Chưa có Node.js — cài Node.js 20+ (nodejs.org) rồi bấm Cài lại")
    _install_node()
    return str(NODE_DIR / "npm.cmd")


def _install_node() -> None:
    """Node.js LTS bản zip chính thức cho Windows x64 -> tools/node, kiểm SHA256 theo SHASUMS256.txt."""
    import hashlib
    import io
    import zipfile
    import httpx
    base = "https://nodejs.org/dist/latest-v22.x/"
    sums = httpx.get(base + "SHASUMS256.txt", timeout=30, follow_redirects=True).text
    line = next((l for l in sums.splitlines() if l.endswith("-win-x64.zip")), None)
    if not line:
        raise AIError("Không tìm thấy bản Node.js cho Windows x64")
    digest, name = line.split()
    data = httpx.get(base + name, timeout=httpx.Timeout(600, connect=20), follow_redirects=True).content
    if hashlib.sha256(data).hexdigest() != digest:
        raise AIError("File Node.js tải về không khớp SHA256 — không cài")
    tmp = NODE_DIR.with_name("node.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(tmp)
    inner = next(tmp.iterdir())
    shutil.rmtree(NODE_DIR, ignore_errors=True)
    inner.replace(NODE_DIR)
    shutil.rmtree(tmp, ignore_errors=True)


def install(provider: str) -> None:
    """Cài / cập nhật CLI ở luồng nền (npm install -g --prefix tools/ai-cli). Tiến độ ở _jobs[provider]."""
    if provider not in PROVIDERS:
        raise AIError("Không có CLI này")
    with _lock:
        j = _jobs.get(provider)
        if j and j["state"] == "installing":
            return
        _jobs[provider] = {"state": "installing", "msg": "Đang cài…", "at": time.time()}

    def run():
        try:
            npm = _npm()
            CLI_DIR.mkdir(parents=True, exist_ok=True)
            env = dict(os.environ)
            node_dir = str(Path(npm).parent)
            env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")          # npm.cmd cần node.exe cạnh nó
            p = subprocess.run([npm, "install", "-g", "--prefix", str(CLI_DIR), PROVIDERS[provider]["npm"] + "@latest",
                                "--no-fund", "--no-audit"], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", env=env, timeout=1200)
            if p.returncode != 0 or not exe(provider):
                tail = (p.stderr or p.stdout or "").strip().splitlines()[-3:]
                raise AIError("npm báo lỗi: " + " | ".join(tail)[:300])
            _jobs[provider] = {"state": "done", "msg": "Đã cài xong", "at": time.time()}
        except Exception as e:
            _jobs[provider] = {"state": "error", "msg": str(e)[:300], "at": time.time()}
    threading.Thread(target=run, daemon=True, name=f"ai-install-{provider}").start()


def login(provider: str) -> None:
    """Mở cửa sổ console riêng chạy lệnh đăng nhập của CLI (người dùng tự đăng nhập, dytool không nhập mật khẩu)."""
    e = exe(provider)
    if not e:
        raise AIError(f"Chưa cài {PROVIDERS[provider]['name']} — bấm Cài trước")
    info = PROVIDERS[provider]
    cmd = subprocess.list2cmdline([e, *info["login"]])
    env = _env()
    _login_cache.pop(provider, None)
    if sys.platform == "win32":
        # chuỗi lệnh (không phải list: list2cmdline bọc nháy làm hỏng `cmd /c`); chữ không dấu: cmd dùng bảng mã OEM
        title = f"Dang nhap {info['name']} - dytool"
        subprocess.Popen(f'cmd.exe /c "title {title} & {cmd} & echo. & echo Xong - dong cua so nay. & pause"',
                         creationflags=subprocess.CREATE_NEW_CONSOLE, cwd=str(HOME), env=env)
    else:
        subprocess.Popen([e, *info["login"]], cwd=str(HOME), env=env)


# ---------------- gọi ----------------

def ask(prompt: str, provider: str | None = None, timeout: int = 600) -> str:
    """Gửi 1 đề bài, trả lời bằng chữ. Lỗi (chưa cài / chưa đăng nhập / CLI lỗi / hết hạn mức) -> AIError rõ ràng."""
    provider = provider or active()
    if not provider:
        raise AIError("Chưa chọn AI — vào Cài đặt › AI, cài và đăng nhập Claude / Codex / Gemini CLI")
    info = PROVIDERS[provider]
    e = exe(provider)
    if not e:
        raise AIError(f"Chưa cài {info['name']} — vào Cài đặt › AI bấm Cài")
    if not logged_in(provider):
        raise AIError(f"{info['name']} chưa đăng nhập — vào Cài đặt › AI bấm Đăng nhập")
    work = Path(tempfile.mkdtemp(prefix="dy-ai-"))           # thư mục trống: CLI không đọc dự án / CLAUDE.md nào
    env = _env()
    try:
        if provider == "claude":
            argv, out_file = [e, "-p", "--output-format", "text"], None
        elif provider == "codex":
            out_file = work / "out.txt"
            argv = [e, "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only", "--color", "never",
                    "-o", str(out_file), "-"]
        else:
            argv, out_file = [e, "-p", "Làm đúng yêu cầu trong phần dưới đây."], None
        try:
            p = subprocess.run(argv, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               cwd=str(work), env=env, timeout=timeout)
        except subprocess.TimeoutExpired as ex:
            raise AIError(f"{info['name']} không trả lời sau {timeout // 60} phút") from ex
        text = out_file.read_text("utf-8", "replace") if out_file and out_file.exists() else p.stdout
        if p.returncode != 0 or not text.strip():
            _login_cache.pop(provider, None)
            msg = (p.stderr or p.stdout or "").strip().splitlines()
            raise AIError(f"{info['name']} lỗi (mã {p.returncode}): " + " | ".join(msg[-3:])[:300])
        return text.strip()
    finally:
        shutil.rmtree(work, ignore_errors=True)
