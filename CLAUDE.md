# dytool — Douyin → edit → lồng tiếng Việt → Facebook Page

Người dùng là người Việt: **luôn trả lời bằng tiếng Việt**. Trước khi tự viết một thành phần mới, **tìm trên GitHub
trước** (`gh search repos`), so sánh sao / license / lần cập nhật cuối, ưu tiên dùng repo có sẵn còn được bảo trì.

## Chạy

```bash
uv sync                                   # Python 3.12, môi trường: macOS arm64 + Windows x64 (xem [tool.uv])
uv run playwright install chromium
uv run dytool ui --autostart              # giao diện http://127.0.0.1:8000, tự bật worker
uv run dytool douyin-login                # đăng nhập douyin.com -> lưu cookie vào config.yaml (mọi OS)
uv run dytool fb-login                    # (cũ, 1 tài khoản) — giờ đăng nhập FB qua giao diện, mỗi tài khoản 1 phiên
uv run dytool edit-file video.mp4         # edit thử 1 file theo config.yaml
```
Windows: bấm đúp `install_windows.bat` (1 file lai batch + PowerShell, cài mọi thứ; giữ **CRLF + chỉ ASCII** khi sửa
vì Windows PowerShell 5.1 đọc file theo ANSI) rồi chạy shortcut dytool / `scripts\run_ui.bat`.

**Đừng chạy giao diện bằng preview của app Claude** (`preview_start` có lệnh khởi động): app sẽ tự tắt server
khi nhàn rỗi → worker chết giữa chừng. Chạy tiến trình riêng (run_ui.bat / `nohup caffeinate -i uv run dytool ui
--autostart &`); `.claude/launch.json` chỉ *attach* vào URL.

Dịch vụ ngoài phải đang chạy:
- **Backend tải Douyin** = TikTokDownloader (vendor/, tự clone) ở cổng 5555 — worker tự bật khi cần (`services.ensure_tdl`).
- **VoiceStudio** (app desktop, cổng 3900) — lồng tiếng. Người dùng tự mở app. OmniVoice là CC-BY-NC (Page không kiếm tiền → OK).

## Kiến trúc (dytool/)

| File | Vai trò |
|---|---|
| `desktop.py` | **App desktop**: pywebview (Edge WebView2) bọc giao diện web trong cửa sổ riêng + icon khay (pystray). Server uvicorn chạy trong thread, worker tự bật. Đóng cửa sổ = ẩn xuống khay; Thoát ở menu khay (hỏi lại nếu worker đang làm). Mở lần 2 → `POST /app/show` đưa cửa sổ cũ lên. Chạy bằng pythonw → log ở `data/app.log`. Tự đặt PATH (`tools\ffmpeg\bin`, `~\.local\bin`) + `PLAYWRIGHT_BROWSERS_PATH` |
| `installer/` | `launcher.cs` → `dytool.exe` (build bằng csc.exe có sẵn của .NET 4, `/codepage:65001`); `dytool.iss` (Inno Setup 7, tiếng Việt `Vietnamese.isl`, cài `C:\dytool`, `PrivilegesRequired=lowest`, gỡ cài giữ `data\` + `config.yaml`); `build.ps1` → `dist\dytool-Setup-<ver>.exe`. Bước tải thư viện: `scripts/setup_app.ps1` (ASCII + CRLF; Python vào `.python`, FFmpeg vào `tools\ffmpeg`, TikTokDownloader tải zip không cần git). Icon: `scripts/make_icon.py` |
| `web/app.py` + `web/templates/` | FastAPI + Jinja2 + HTMX (htmx vendored ở `web/static`). Trang: Tổng quan (dải "dây chuyền" số video từng bước), Duyệt video, Tiến trình, Tài khoản & kênh (Facebook / TikTok 2 cột), Cài đặt kênh (mục lục + thanh Lưu dính). Thanh bên: huy hiệu Chờ duyệt / Lỗi + trạng thái dịch vụ (`_services.html`, poll 6s). Font Be Vietnam Pro (Google Fonts, không mạng thì Segoe UI). CSS: tránh tên class trùng `.done` chung; lưới dùng `minmax(0, 1fr)` để không tràn ngang; sửa template bằng PowerShell thì ghi UTF-8 **không BOM** (BOM làm trang vào quirks mode) |
| `worker.py` | Thread nền, **tuần tự 1 việc nặng/lần** (VoiceStudio chỉ 1 GPU: chạy song song chậm 4-6×). Ưu tiên: gửi lịch / đăng ngay / đăng đã duyệt → edit → tải → quét |
| `store.py` | SQLite `data/app.db`: accounts, pages(settings JSON = DEFAULT_PAGE_SETTINGS), sources, videos, logs. `next_free_slot` cho tự lên lịch |
| `progress.py` | Trạng thái bước đang chạy cho bảng tiến trình (`progress.begin/step/detail/end`) |
| `douyin.py` | Gọi Web API TikTokDownloader với `source=True` và **tự parse dữ liệu gốc** (`normalize`) — extractor của nó trả rỗng cho tìm kiếm. Tìm video rỗng → fallback `/douyin/search/general`. Link CDN hết hạn sau vài giờ → `fresh_url` qua `/douyin/detail` |
| `editor.py` | FFmpeg: thu về ≤1080p, làm mờ dải sub Trung, lật, nghiêng, phủ màu (blend RGB), phụ đề ASS. `pick_source` chọn nguồn chữ |
| `hardsub.py` | RapidOCR: `detect_band` (dải sub, cụm hàng xuất hiện nhiều khung nhất) + `extract_cues` (OCR theo thời gian, chỉ OCR khi dải thay đổi) |
| `subtitles.py` | faster-whisper (giải mã bằng ffmpeg → numpy, **không đưa đường dẫn** vì lỗi PyAV), dịch bằng Claude, ghi .ass |
| `phrasing.py` | Quy tắc ngắt nghỉ tiếng Việt **đo từ 12 video đọc thật** (research/analyze_prosody.py): 5.29 âm tiết/s, cụm ≤16, nghỉ phẩy 0.32s… |
| `dub.py` | Demucs tách giọng → gom lượt nói → Claude dịch theo ngân sách âm tiết → VoiceStudio đọc **cả lượt 1 lần** với `duration` → xếp lịch tuần tự (không chồng giọng) → căn phụ đề theo Whisper nghe lại |
| `gender.py` + `_ecapa_gender.py` | Nhận nam/nữ từng lượt (ECAPA, MIT, github.com/JaesungHuh/voice-gender-classifier) |
| `fb_browser.py` | Playwright + Meta Business Suite reels composer: đăng / lên lịch / chia sẻ nhóm / quét Page (`delegate_page_id`) |
| `tt_browser.py` | TikTok: `login` (người dùng tự đăng nhập, lấy @tên kênh), `publish` (tải lên + mô tả + hẹn giờ qua TikTok Studio, `dry_run` dừng trước nút Post). Điền form bằng thư viện **wkaisertexas/tiktok-uploader** (MIT, Playwright) với trang của profile riêng `data/accounts/<id>/tt_profile`; context `locale=en-US` (bộ chọn tiếng Anh) + `timezone_id=Asia/Ho_Chi_Minh`. Hẹn giờ: 20 phút – 10 ngày, phút chia hết cho 5. Thử: `uv run dytool tt-dryrun <id tài khoản> video.mp4` |
| `cookies.py` | `douyin_login` (Playwright, mọi OS); `read_douyin_cookie` (pycookiecheat — **chỉ macOS/Linux**) |
| `loginstate.py` + `web/templates/_login.html` | Thanh tiến trình đăng nhập Douyin / Facebook: tiến trình đăng nhập ghi bước hiện tại ra `data/douyin_login.json` / `data/accounts/<id>/login_state.json`, giao diện poll 1s. Xong → tự ẩn sau 5s; lỗi → giữ tới khi bấm ×. Trạng thái "Douyin: đã đăng nhập · còn N ngày" tính từ `sid_guard` (`cookies.cookie_status`); đăng nhập xong nạp cookie mới vào `cfg` đang chạy, không cần khởi động lại |
| `pipeline.py`, `facebook.py` | Chế độ CLI cũ 1 Page (config.yaml) + Graph API — vẫn chạy được nhưng giao diện là đường chính |

TikTok có 2 cách dùng (người dùng muốn cả hai): (1) **kênh riêng** — tài khoản TikTok (`accounts.platform='tiktok'`)
có 1 dòng `pages` platform='tiktok', đi đúng luồng như Page (nguồn, edit, duyệt); worker `_upload` rẽ sang
`_upload_tiktok`; lịch xa hơn 10 ngày nằm chờ ở `schedule_pending` tới khi vào cửa sổ 10 ngày. (2) **đăng kèm** —
cài đặt Page Facebook `tiktok_mirror` = id kênh TikTok: đăng / lên lịch FB thành công → thêm dòng `crossposts`
(cùng giờ) → worker bước 1b đăng/hẹn giờ lên TikTok (lịch < 25 phút thì chờ tới giờ rồi đăng ngay).

Luồng 1 video: `new` → tải → `downloaded` → edit → `review` (hoặc `approved` nếu Page bật tự đăng) →
`approved` (+ tự lên lịch → `schedule_pending`) → worker đẩy lên FB → `scheduled` / `uploaded`. Lỗi → `failed` (nút Thử lại).

## Bài học — đừng lặp lại

- **OmniVoice phải có giọng mẫu cố định** (voice profile): `voice: default` sinh giọng ngẫu nhiên mỗi lần gọi.
  Profile tạo tự động theo `instruct` + `seed` (tên `dy-nam-<md5>` — dùng md5, **không dùng `hash()`** vì đổi mỗi lần chạy).
- **Luôn truyền `duration`** cho `/v1/audio/speech`: thiếu thì OmniVoice ước lượng ngắn cho tiếng Việt → cụt chữ cuối.
- Không tua audio (atempo) / không cắt clip TTS; câu dài → rút gọn bằng Claude rồi đọc lại.
- Màu phủ: `drawbox` pha trong YUV cho màu sai (đậm ~3×) → dùng `color` + `blend` trên gbrp, **[tint] là lớp trên**.
- `boxblur` bán kính phải < 1/4 chiều cao dải.
- `uv` từng chọn torch 2.2.2 (bản Mac Intel) → lỗi "Numpy is not available"; đã ghim `torch>=2.5` + giới hạn environments.
- Chromium chạy ẩn không đọc được cookie do bản có cửa sổ mã hoá bằng Keychain → lưu phiên FB ra `fb_state.json`
  rồi `add_cookies` mỗi lần chạy.
- Trường giờ/phút của FB là ô có định dạng: `input_value()` rỗng — kiểm tra bằng chữ hiển thị. Phím chọn hết: `ControlOrMeta+A`.
- Đăng thành công = hộp "Đang xử lý thước phim…" hoặc "Đã lên lịch đăng thước phim" (không đổi URL).
- Lịch đã quá giờ (tool tắt lúc đó) → **không tự đăng**, trả về `review`.
- Khởi động lại server khi worker đang chạy giết Demucs (SIGTERM) → worker tự đưa video về `downloaded`.

## Quy tắc an toàn

- **Không đăng / lên lịch thật lên Facebook hay TikTok khi chưa được người dùng đồng ý** cho lần đó. Thử nghiệm dùng
  `fb_browser.publish(..., dry_run=True)` (đi hết các bước, dừng trước nút Chia sẻ / Lên lịch) và
  `tt_browser.publish(..., dry_run=True)` / `dytool tt-dryrun` (dừng trước nút Post).
- Không tự nhập mật khẩu: đăng nhập FB/Douyin luôn do người dùng làm trong cửa sổ Chromium mở ra.
- Không đẩy `config.yaml`, `data/` (cookie, `fb_state.json`, video) lên git. Page thật của người dùng: **Meow Meow**,
  **No1td Math**; **KC Crypto Lab** là Page crypto riêng — đừng đăng video Douyin lên đó.

## Windows — điểm cần biết

- Chưa từng chạy thử trên Windows: lần đầu hãy chạy `uv run dytool edit-file` với 1 video, rồi `dry_run` đăng FB.
- FFmpeg phải là bản **full** (Gyan.FFmpeg) để có `subtitles`/libass; font mặc định Arial.
- torch trên Windows từ PyPI là bản CPU (Demucs/Whisper chậm hơn). Có GPU NVIDIA thì cài torch CUDA theo pytorch.org.
- Chống ngủ máy: worker tự gọi `SetThreadExecutionState` khi chạy.
- Máy Windows của người dùng (10 LTSC, không có winget) **chặn chạy exe/junction trong `AppData`**: chrome.exe báo lỗi
  side-by-side, `uv python install` báo "Missing expected target directory for Python minor version link". Cùng file
  đặt ở `C:\` thì chạy được. Vì vậy Chromium ở `C:\ms-playwright` (`PLAYWRIGHT_BROWSERS_PATH`, run_ui.bat tự đặt),
  runtime VoiceStudio ở `C:\VoiceStudio\runtime`; FFmpeg (bản BtbN, có libass) chép vào `~\.local\bin`.
- `page.goto` douyin.com / facebook.com dùng `wait_until="domcontentloaded"` + timeout dài: chờ "load" bị quá 30s
  trên mạng VN làm cửa sổ đăng nhập tự đóng.
- Phiên Facebook / cookie Douyin **không mang từ Mac sang** — đăng nhập lại trên Windows (tránh FB nghi ngờ đăng nhập lạ).
