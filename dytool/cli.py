import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console

from . import config

con = Console()
ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "TikTokDownloader"
TDL_REPO = "https://github.com/JoeanAmier/TikTokDownloader"


def cmd_server(cfg, args):
    """Chạy TikTokDownloader ở chế độ Web API (cổng 5555)."""
    if not VENDOR.exists():
        con.print("Đang tải TikTokDownloader ...")
        VENDOR.parent.mkdir(exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", TDL_REPO, str(VENDOR)], check=True)
    if not (VENDOR / ".venv").exists():
        subprocess.run(["uv", "sync", "--python", "3.12"], cwd=VENDOR, check=True)
    # Ghi settings: tự chọn mục "Web API 模式" (mục 7) và cookie
    settings_path = VENDOR / "Volume" / "settings.json"
    settings_path.parent.mkdir(exist_ok=True)
    settings = json.loads(settings_path.read_text("utf-8")) if settings_path.exists() else {}
    settings["run_command"] = "7"
    if cfg["douyin"].get("cookie"):
        settings["cookie"] = cfg["douyin"]["cookie"]
    settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=4), "utf-8")
    con.print("Web API: http://127.0.0.1:5555/docs  (Ctrl+C để dừng)")
    # Lần đầu chạy, chương trình hỏi ngôn ngữ (1 = 简体中文) và xác nhận miễn trừ trách nhiệm (YES)
    vpy = (VENDOR / ".venv" / "Scripts" / "python.exe") if sys.platform == "win32" else (VENDOR / ".venv" / "bin" / "python")
    p = subprocess.Popen([str(vpy), "main.py"], cwd=VENDOR,
                         stdin=subprocess.PIPE, text=True)
    p.stdin.write("1\nYES\n")
    p.stdin.flush()
    try:
        p.wait()
    except KeyboardInterrupt:
        p.terminate()


def _pipe(cfg):
    from .pipeline import Pipeline
    return Pipeline(cfg)


def cmd_scan(cfg, args):
    _pipe(cfg).scan()


def cmd_run(cfg, args):
    _pipe(cfg).run_once(post=not args.no_post)


def cmd_watch(cfg, args):
    _pipe(cfg).watch(post=not args.no_post)


def cmd_upload(cfg, args):
    _pipe(cfg).upload(args.n)


def cmd_edit_file(cfg, args):
    """Edit thử 1 file bất kỳ để xem mẫu."""
    from .editor import edit
    src = Path(args.input)
    dst = Path(args.output or src.with_name(src.stem + "_edited.mp4"))
    info = edit(src, dst, cfg["_data"] / "work", cfg["edit"])
    con.print(f"[green]Xong:[/] {dst}  ({info['sub_lines']} câu sub)")


def cmd_status(cfg, args):
    from .db import DB
    db = DB(cfg["_data"] / "dytool.db")
    for status, n in db.stats():
        con.print(f"{status:12} {n}")


def cmd_cookie(cfg, args):
    """Lấy cookie douyin.com từ Chrome và ghi vào config.yaml (không in giá trị ra màn hình)."""
    from . import cookies
    try:
        data = cookies.read_douyin_cookie(args.profile)
    except Exception as e:
        sys.exit(f"Không đọc được cookie Chrome ({e}). Nếu báo 'unable to open database file': "
                 "cấp quyền Full Disk Access cho ứng dụng Terminal trong System Settings > "
                 "Privacy & Security, hoặc kiểm tra tên profile (--profile).")
    if not data:
        sys.exit(f"Không thấy cookie douyin.com trong Chrome profile '{args.profile}'. "
                 "Mở douyin.com trên Chrome trước, hoặc thử --profile 'Profile 1'.")
    if not cookies.is_logged_in(data):
        sys.exit(f"Có {len(data)} cookie nhưng chưa thấy cookie đăng nhập (sessionid). "
                 "Hãy đăng nhập douyin.com trên Chrome rồi chạy lại.")
    cookies.save_to_config(cookies.to_header(data), Path(args.config))
    con.print(f"[green]Đã lưu {len(data)} cookie douyin.com (có phiên đăng nhập) vào {args.config}[/]")


def cmd_ui(cfg, args):
    import uvicorn
    from .web.app import create_app
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    con.print(f"Giao diện: http://127.0.0.1:{args.port}" + ("  (mở được từ máy khác cùng wifi)" if args.lan else ""))
    uvicorn.run(create_app(args.config, autostart=args.autostart), host=host, port=args.port, log_level="warning")


def cmd_fb_login(cfg, args):
    from . import fb_browser
    fb_browser.login(cfg["_data"])


def cmd_fb_probe(cfg, args):
    from . import fb_browser
    fb_browser.probe(cfg["_data"], args.url, cfg["_data"] / "fb_probe")


def cmd_douyin_login(cfg, args):
    """Đa nền tảng: đăng nhập douyin.com trong Chromium của tool rồi lưu cookie (dùng trên Windows)."""
    from . import cookies
    n = cookies.douyin_login(cfg["_data"], Path(args.config))
    con.print(f"[green]Đã lưu {n} cookie douyin.com vào {args.config}[/]")


def cmd_fbcheck(cfg, args):
    from .facebook import FacebookPage
    con.print(FacebookPage(cfg["facebook"]).whoami())


def main():
    if not shutil.which("ffmpeg"):
        sys.exit("Chưa cài ffmpeg")
    ap = argparse.ArgumentParser(prog="dytool", description="Douyin -> edit -> Facebook Page")
    ap.add_argument("-c", "--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("server", help="chạy backend TikTokDownloader (để ở 1 cửa sổ riêng)").set_defaults(f=cmd_server)
    sub.add_parser("scan", help="quét kênh/từ khóa, ghi video mới vào DB").set_defaults(f=cmd_scan)
    for name, f, h in (("run", cmd_run, "chạy 1 lượt: quét, tải, edit, đăng"),
                       ("watch", cmd_watch, "chạy liên tục theo poll_minutes")):
        p = sub.add_parser(name, help=h)
        p.add_argument("--no-post", action="store_true", help="không đăng Facebook")
        p.set_defaults(f=f)
    p = sub.add_parser("upload", help="đăng video đã edit (tôn trọng giới hạn/ngày)")
    p.add_argument("-n", type=int, default=1)
    p.set_defaults(f=cmd_upload)
    p = sub.add_parser("edit-file", help="edit thử 1 file video")
    p.add_argument("input")
    p.add_argument("-o", "--output")
    p.set_defaults(f=cmd_edit_file)
    sub.add_parser("status", help="thống kê trạng thái").set_defaults(f=cmd_status)
    sub.add_parser("fb-check", help="kiểm tra token Facebook").set_defaults(f=cmd_fbcheck)
    p = sub.add_parser("ui", help="mở giao diện web quản lý nhiều tài khoản / Page")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--lan", action="store_true", help="cho phép mở từ điện thoại/máy khác cùng mạng")
    p.add_argument("--autostart", action="store_true", help="tự bật chạy tự động khi mở giao diện")
    p.set_defaults(f=cmd_ui)
    sub.add_parser("fb-login", help="mở trình duyệt để đăng nhập Facebook 1 lần (lưu phiên)").set_defaults(
        f=cmd_fb_login)
    p = sub.add_parser("fb-probe", help="(dev) chụp 1 trang Facebook bằng phiên đã lưu")
    p.add_argument("url")
    p.set_defaults(f=cmd_fb_probe)
    sub.add_parser("douyin-login", help="đăng nhập douyin.com trong trình duyệt của tool, lưu cookie "
                   "(mọi hệ điều hành)").set_defaults(f=cmd_douyin_login)
    p = sub.add_parser("cookie", help="(chỉ macOS/Linux) lấy cookie douyin.com từ Chrome đã đăng nhập, ghi vào config")
    p.add_argument("--profile", default="Default", help="thư mục profile Chrome, vd 'Profile 1'")
    p.set_defaults(f=cmd_cookie)
    args = ap.parse_args()
    args.f(config.load(args.config), args)


if __name__ == "__main__":
    main()
