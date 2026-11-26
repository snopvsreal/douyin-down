"""App dytool với DỮ LIỆU MẪU (kênh, video giả) để chụp ảnh README — không đụng data/ thật.

  uv run --no-sync python scripts/readme_demo.py . <thư mục tạm> seed    # dựng dữ liệu + video mẫu (cần ffmpeg)
  uv run --no-sync python scripts/readme_demo.py . <thư mục tạm> serve   # app mẫu ở http://127.0.0.1:8010
  uv run --no-sync python scripts/readme_shots.py <thư mục ảnh>          # chụp (Playwright), rồi thu về 1920px
                                                                          # vào docs/images/app-*.png
Bản mẫu không tải nhân ShardX, không lấy ảnh / link Facebook, không bật chạy tự động.
"""
import json
import random
import subprocess
import sys
import time
from pathlib import Path

REPO, DEMO, MODE = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, str(REPO))
DATA = DEMO / "data"
CFG = DEMO / "config.yaml"

PAGES = [  # name, platform, account, enabled, slots, color seed
    ("Mèo Lười Vui Vẻ", "facebook", 1, 1, "07:00, 11:30, 19:30, 21:00"),
    ("Bếp Nhà Mây", "facebook", 1, 1, "10:00, 17:30, 20:30"),
    ("Mẹo Vặt Mỗi Ngày", "tiktok", 2, 1, "08:00, 12:00, 18:00, 22:00"),
    ("Du Lịch Trung Hoa", "facebook", 1, 1, "09:00, 19:00"),
    ("Xe Đẹp 24h", "facebook", 1, 0, "20:00"),
]
VIDEOS = {  # page -> [(caption, zh desc)]
    1: [("Bé mèo lần đầu thấy tuyết, phản ứng cực đáng yêu", "小猫第一次看到雪"),
        ("Mèo con học cách mở cửa tủ lạnh", "小猫学会开冰箱"),
        ("Khi chủ đi làm về và hai bé mèo ra đón", "主人下班回家两只猫来迎接"),
        ("Mèo cam ngủ quên trong hộp giấy", "橘猫在纸箱里睡着了"),
        ("Thử thách: mèo có sợ dưa chuột không?", "猫怕黄瓜吗"),
        ("Hai anh em mèo tranh nhau chỗ nằm", "两只猫抢位置")],
    2: [("Mì lạnh Tứ Xuyên chua cay làm trong 10 phút", "四川凉面"),
        ("Bánh bao hấp mềm xốp, không cần máy nhồi", "松软包子"),
        ("Gà xé phay kiểu Trùng Khánh", "重庆口水鸡"),
        ("Trà sữa trân châu đường đen tại nhà", "黑糖珍珠奶茶")],
    3: [("Mẹo gấp áo thun trong 2 giây", "两秒叠T恤"),
        ("Làm sạch bếp ga bằng 1 nguyên liệu có sẵn", "清洁燃气灶"),
        ("Cách bảo quản rau tươi cả tuần", "蔬菜保鲜"),
        ("Treo đồ gọn tủ quần áo gấp đôi", "衣柜收纳")],
    4: [("Phố cổ Phượng Hoàng lên đèn về đêm", "凤凰古城夜景"),
        ("Trương Gia Giới mùa mây phủ", "张家界云海"),
        ("Ăn sáng 20 tệ ở Trùng Khánh có gì?", "重庆早餐")],
    5: [("Xe điện mini giá rẻ chạy thử đường đèo", "迷你电动车")],
}
GRAD = [("0x1d4e89", "0xf79256"), ("0x2a9d8f", "0xe9c46a"), ("0x6a4c93", "0xff595e"), ("0x264653", "0x8ab17d"),
        ("0xb5179e", "0x4361ee"), ("0x0b525b", "0xc8e7ff"), ("0xd00000", "0xffba08"), ("0x3a0ca3", "0x4cc9f0")]


def vn_ts(day: int, hhmm: str) -> int:
    """Giờ VN (UTC+7) của ngày hôm nay + day."""
    h, m = map(int, hhmm.split(":"))
    now = time.time() + 7 * 3600
    midnight = now - now % 86400
    return int(midnight + day * 86400 + h * 3600 + m * 60 - 7 * 3600)


def make_video(path: Path, text: str, i: int):
    c0, c1 = GRAD[i % len(GRAD)]
    font = "font.ttf"
    words = text.split()
    half = (len(words) + 1) // 2
    line1, line2 = " ".join(words[:half]), " ".join(words[half:])
    fs = 44 if max(len(line1), len(line2)) <= 20 else 36
    for k, t in (("1", line1), ("2", line2)):
        (DEMO / f"t{k}.txt").write_text(t, encoding="utf-8")
    line1, line2 = "t1.txt", ("t2.txt" if line2 else "")
    dt = lambda t, y: (f"drawtext=fontfile={font}:textfile={t}:fontsize={fs}:fontcolor=white:borderw=4:bordercolor=black"
                       f":shadowx=2:shadowy=2:x=(w-text_w)/2:y={y}")
    vf = f"{dt(line1, 'h*0.70')},{dt(line2, 'h*0.70+58')}" if line2 else dt(line1, "h*0.72")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"gradients=s=540x960:c0={c0}:c1={c1}:x0=0:y0=0:x1=540:y1=960:speed=0.02:d=3:r=25",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "3", "-vf", vf,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
                   check=True, cwd=DEMO)


def avatar(path: Path, name: str, i: int):
    from PIL import Image, ImageDraw, ImageFont
    c0, c1 = GRAD[(i * 3 + 1) % len(GRAD)]
    a, b = tuple(int(c0[2:][k:k + 2], 16) for k in (0, 2, 4)), tuple(int(c1[2:][k:k + 2], 16) for k in (0, 2, 4))
    im = Image.new("RGB", (128, 128))
    for y in range(128):
        t = y / 127
        ImageDraw.Draw(im).line((0, y, 128, y), fill=tuple(int(a[k] + (b[k] - a[k]) * t) for k in range(3)))
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype(str(DEMO / "font.ttf"), 58)
    initials = "".join(w[0] for w in name.split()[:2]).upper()
    d.text((64, 66), initials, font=f, fill="white", anchor="mm", stroke_width=2, stroke_fill=(0, 0, 0))
    im.save(path)


def seed():
    import shutil
    from dytool.store import Store
    if DATA.exists():
        shutil.rmtree(DATA)
    for sub in ("edited", "raw", "work", "avatars"):
        (DATA / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "dytool" / "assets" / "fonts" / "BeVietnamPro-ExtraBold.ttf", DEMO / "font.ttf")
    exp = int(time.time()) - 86400
    CFG.write_text(
        (REPO / "config.example.yaml").read_text(encoding="utf-8") + f"\ndata_dir: {DATA.as_posix()}\n", encoding="utf-8")
    import yaml
    cfg = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    cfg["data_dir"] = DATA.as_posix()
    cfg.setdefault("douyin", {})["cookie"] = f"sessionid=demo; sid_guard=demo%7C{exp}%7C5184000%7Cdemo"
    CFG.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")

    st = Store(DATA / "app.db")
    now = int(time.time())
    st.x("INSERT INTO accounts(id,name,status,created_at,platform) VALUES (1,'Tài khoản Facebook','da_dang_nhap',?,'facebook')", (now,))
    st.x("INSERT INTO accounts(id,name,status,created_at,platform) VALUES (2,'Mẹo Vặt Mỗi Ngày','da_dang_nhap',?,'tiktok')", (now,))
    for pid, (name, plat, acc, en, slots) in enumerate(PAGES, 1):
        s = {"schedule_slots": slots, "auto_schedule": True}
        st.x("INSERT INTO pages(id,account_id,name,profile_id,asset_id,enabled,settings,platform) VALUES (?,?,?,?,?,?,?,?)",
             (pid, acc, name, f"demo{pid}", f"10000{pid}", en, json.dumps(s, ensure_ascii=False), plat))
        st.x("INSERT INTO sources(page_id,kind,value,params) VALUES (?,?,?,?)",
             (pid, "keyword", VIDEOS[pid][0][1], json.dumps({"vi": VIDEOS[pid][0][0]}, ensure_ascii=False)))
        avatar(DATA / "avatars" / f"p{pid}.png", name, pid)

    rnd = random.Random(7)
    n = 0
    plan = {  # page -> statuses theo thứ tự video
        1: ["review", "review", "uploaded", "scheduled", "scheduled", "downloaded"],
        2: ["review", "uploaded", "scheduled", "downloaded"],
        3: ["review", "scheduled", "scheduled", "downloaded"],
        4: ["review", "scheduled", "downloaded"],
        5: ["new"],
    }
    for pid, vids in VIDEOS.items():
        slots = [s.strip() for s in PAGES[pid - 1][4].split(",")]
        k_sched = 0
        for j, ((cap, zh), stt) in enumerate(zip(vids, plan[pid])):
            vid = str(7440000000000000000 + pid * 1000 + j)
            likes = rnd.choice([12_400, 38_900, 156_000, 420_000, 87_300, 1_240_000, 9_800])
            st.add_video(pid, {"id": vid, "desc": zh + " #推荐", "create_ts": now - rnd.randint(2, 60) * 86400,
                               "likes": likes, "duration": rnd.randint(14, 58), "url": "", "meta": {}}, "keyword")
            f = {"status": stt, "caption": cap}
            if stt in ("review", "scheduled", "uploaded"):
                make_video(DATA / "edited" / f"p{pid}_{vid}.mp4", cap, n)
                n += 1
            if stt == "scheduled":
                day = 0 if k_sched == 0 else rnd.randint(1, 5)
                slot = slots[-1] if day == 0 else rnd.choice(slots)
                f["scheduled_at"] = vn_ts(day, slot)
                k_sched += 1
            if stt == "uploaded":
                f["scheduled_at"] = f["uploaded_at"] = vn_ts(0, slots[0])
            st.set_video(vid, pid, **f)
    extra = {1: ["Mèo mướp bắt bướm trong vườn", "Bé mèo đòi ăn lúc 5 giờ sáng", "Mèo trắng mắt hai màu cực hiếm",
                 "Phản ứng của mèo khi nghe tiếng gọi tên"],
             2: ["Đậu phụ Ma Bà cay tê chuẩn vị", "Sủi cảo nhân tôm gói kiểu mới", "Cơm chiên Dương Châu 5 phút"],
             3: ["Mẹo cắt hành không cay mắt", "Gấp túi nilon gọn trong ngăn kéo", "Khử mùi tủ lạnh bằng bã cà phê"],
             4: ["Vạn Lý Trường Thành lúc bình minh", "Lệ Giang cổ trấn mùa thu", "Lẩu Trùng Khánh chính gốc"]}
    for pid, caps in extra.items():
        slots = [s.strip() for s in PAGES[pid - 1][4].split(",")]
        for j, cap in enumerate(caps):
            vid = str(7450000000000000000 + pid * 1000 + j)
            st.add_video(pid, {"id": vid, "desc": cap, "create_ts": now - 9 * 86400, "likes": rnd.choice([56_000, 230_000, 18_700]),
                               "duration": rnd.randint(15, 50), "url": "", "meta": {}}, "keyword")
            make_video(DATA / "edited" / f"p{pid}_{vid}.mp4", cap, n); n += 1
            st.set_video(vid, pid, status="scheduled", caption=cap,
                         scheduled_at=vn_ts(1 + (j * 2 + pid) % 6, slots[(j + pid) % min(2, len(slots))]))
    for i, (msg, lvl, pid) in enumerate([
        ("Quét nguồn: 12 video mới (3 bị lọc: ít like)", "info", 1),
        ("Edit xong: Bé mèo lần đầu thấy tuyết… — lồng tiếng giọng nữ", "info", 1),
        ("Đã lên lịch trên Facebook lúc 21:00", "info", 1),
        ("Đã lên lịch trên TikTok lúc 22:00", "info", 3),
        ("Edit xong: Mì lạnh Tứ Xuyên chua cay…", "info", 2)]):
        st.x("INSERT INTO logs(ts,level,page_id,msg) VALUES (?,?,?,?)", (now - (5 - i) * 900, lvl, pid, msg))
    print("seeded", n, "videos")


def serve():
    import uvicorn
    from dytool import avatars, browsers, updates
    updates.installed = lambda app: "0.4.0"
    from dytool.worker import Worker
    browsers.install_engine_background = lambda *a, **k: None     # không tải nhân ShardX
    avatars.refresh_fb_background = lambda *a, **k: None          # không lấy ảnh Facebook
    Worker.start_sync = lambda self: None                         # không lấy link bài
    from dytool.web.app import create_app
    uvicorn.run(create_app(str(CFG)), host="127.0.0.1", port=8010, log_level="warning")


def regen():
    import sqlite3
    c = sqlite3.connect(DATA / "app.db")
    for i, (pid, vid, cap) in enumerate(c.execute("SELECT page_id, id, caption FROM videos ORDER BY rowid")):
        f = DATA / "edited" / f"p{pid}_{vid}.mp4"
        if f.exists():
            make_video(f, cap, i)


{"seed": seed, "serve": serve, "regen": regen}[MODE]()
