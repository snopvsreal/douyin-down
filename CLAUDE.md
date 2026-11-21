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
Windows: cách chính là **trình cài** `dist\dytool-Setup-<ver>.exe` (build: `installer\build.ps1`) → app desktop
`C:\dytool\dytool.exe`. `install_windows.bat` là cách cũ (cần winget — Windows 10 LTSC không có; giữ **CRLF + chỉ
ASCII** khi sửa vì Windows PowerShell 5.1 đọc file theo ANSI). Đừng chạy `run_ui.bat` song song với app desktop.

**Đừng chạy giao diện bằng preview của app Claude** (`preview_start` có lệnh khởi động): app sẽ tự tắt server
khi nhàn rỗi → worker chết giữa chừng. Chạy tiến trình riêng (run_ui.bat / `nohup caffeinate -i uv run dytool ui
--autostart &`); `.claude/launch.json` chỉ *attach* vào URL.

Dịch vụ ngoài phải đang chạy:
- **Backend tải Douyin** = TikTokDownloader (vendor/, tự clone) ở cổng 5555 — worker tự bật khi cần (`services.ensure_tdl`).
- **VoiceStudio** (app desktop, cổng 3900) — lồng tiếng. Người dùng tự mở app. OmniVoice là CC-BY-NC (Page không kiếm tiền → OK).

## Kiến trúc (dytool/)

| File | Vai trò |
|---|---|
| `desktop.py` | **App desktop**: pywebview (Edge WebView2) bọc giao diện web trong cửa sổ riêng + icon khay (pystray). Server uvicorn chạy trong thread, worker tự bật. Đóng cửa sổ = ẩn xuống khay; Thoát ở menu khay (hỏi lại nếu worker đang làm). Mở lần 2 → `POST /app/show` đưa cửa sổ cũ lên. Chạy bằng pythonw → log ở `data/app.log`. Tự đặt PATH (`tools\ffmpeg\bin`, `~\.local\bin`) |
| `installer/` | `launcher.cs` → `dytool.exe` (build bằng csc.exe có sẵn của .NET 4, `/codepage:65001`); `dytool.iss` (Inno Setup 7, tiếng Việt `Vietnamese.isl`, cài `C:\dytool`, `PrivilegesRequired=lowest`, gỡ cài giữ `data\` + `config.yaml`;
`WizardStyle=modern dynamic windows11` + hình `installer/wiz-*.png` vẽ bằng `scripts/make_wizard_images.py`; ô tick
ShardBrowser / VoiceStudio chỉ hiện khi máy chưa có; `PrepareToInstall` đóng dytool đang chạy; bước thư viện chạy
`setup_app.ps1 -Wizard` ẩn qua `ExecAndLogOutput`, dòng `▸ N/7` điều khiển thanh tiến độ — tên bước lấy từ
`[CustomMessages]` vì chữ UTF-8 trong log có thể vỡ); `build.ps1` → `dist\dytool-Setup-<ver>.exe`. Bước tải thư viện: `scripts/setup_app.ps1` (ASCII + CRLF; Python vào `.python`, FFmpeg vào `tools\ffmpeg`, TikTokDownloader tải zip không cần git). Icon: `scripts/make_icon.py` |
| `web/app.py` + `web/templates/` | FastAPI + Jinja2 + HTMX (htmx vendored ở `web/static`). Trang: Tổng quan (dải "dây chuyền" số video từng bước), Duyệt video, Tiến trình, Tài khoản & kênh (Facebook / TikTok 2 cột), Cài đặt kênh (mục lục + thanh Lưu dính), **Cài đặt** chung (`/settings`, cuối menu, Ctrl+5: Cập nhật, API key + VoiceStudio, ShardBrowser, Douyin, Thông tin — Tổng quan / Tài khoản chỉ còn dòng nhắc trỏ sang đây; macro dùng chung ở `_macros.html`). Giao diện kiểu **app Windows (Fluent 2 / WinUI)**: khung cố định (menu trái có icon SVG + huy hiệu Chờ duyệt / Lỗi, vùng nội dung bo góc trên-trái, chỉ vùng này cuộn), **thanh trạng thái** dưới đáy (`_services.html`, poll 5s: chạy tự động, việc đang làm, Douyin, Tải Douyin, VoiceStudio), font hệ thống Segoe UI, accent xanh Windows, nút / ô nhập 32px góc 4px, công tắc `input.switch`, `hx-boost` trên body (chuyển trang không nháy; script trong template phải chống gắn lặp bằng cờ `window.__dy…`), Ctrl+1..5 chuyển trang, cửa sổ < 900px thu menu còn icon. CSS: tránh tên class trùng `.done` chung; lưới dùng `minmax(0, 1fr)` để không tràn ngang; sửa template bằng PowerShell thì ghi UTF-8 **không BOM** (BOM làm trang vào quirks mode) |
| `updates.py` + `web/templates/_updates.html` | **Tự cập nhật trong app** dytool / VoiceStudio / ShardBrowser: hỏi GitHub `releases/latest` (mở app + 6 giờ/lần), so với bản đang cài (dytool: metadata gói; 2 app kia: `DisplayVersion` trong Uninstall registry). Tải file cài chính thức, **bắt buộc khớp `asset.digest` SHA256** (không có / sai thì không cài). VoiceStudio: đóng app (không khi đang edit) → `/S` → mở lại; ShardBrowser: không khi có hồ sơ chạy (`GET /running`) → tắt launcher → `/S` → mở lại; dytool: trình cài `/SILENT /RELAUNCH` (tách tiến trình) — `PrepareToInstall` đóng app, `ssDone` mở lại. Khoá dict trong template đừng tên `update` / `items` (trùng hàm dict) |
| `__init__.py` | Không có console (app chạy bằng pythonw) → mọi `subprocess.Popen` mặc định `CREATE_NO_WINDOW`: hết cửa sổ cmd nhảy ra khi gọi ffmpeg / backend Douyin… |
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
| `browsers.py` | **Trình duyệt duy nhất = ShardBrowser** (github.com/ProxyShard/ShardBrowser, MIT; người dùng yêu cầu bỏ hẳn Chromium). Mỗi tài khoản FB / TikTok gắn 1 hồ sơ (`data/accounts/<id>/browser.json`), đăng nhập Douyin dùng `data/browser.json`. API cục bộ `127.0.0.1:40325` (Bearer token trong `config.yaml` mục `browser:`): `POST /profiles/{id}/start` → CDP ws → Playwright `connect_over_cdp` (chỉ cần driver Playwright, không tải Chromium); `ctx.close()` gọi `/stop`. API không chạy → tự mở ShardBrowser Launcher (tìm trong Uninstall registry) rồi thử lại. Chưa có hồ sơ → bấm Đăng nhập (hoặc thêm tài khoản) **tự tạo** hồ sơ `dytool · <Facebook|TikTok> · <tên>` /
`dytool · Douyin` trong thư mục `dytool` (`browsers.ensure_profile`: `GET /fingerprint/new` → `POST /profiles`, lưu
nguyên cấu hình ShardBrowser sinh — dytool không sửa / không viết logic giả dấu vân tay). Người dùng chỉ dán token 1 lần. Hồ sơ không nhận `locale`/`timezone_id` của Playwright → để hồ sơ TikTok ở tiếng Anh (bộ chọn của tiktok-uploader). Lỗi ShardBrowser trong lúc đăng nhập → `fail()` ghi vào thanh tiến trình |
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

- **Không bao giờ để 1 video bị đăng 2 lần.** Worker nhận việc bằng `store.set_video_if(... expect=(trạng thái lúc
  đọc,), status="uploading")`; đăng lỗi / app bị tắt giữa lúc đăng → về `review` kèm lời nhắc kiểm tra trên nền tảng
  (`_upload_failed`, `_recover_uploading`), **không** `failed` (Thử lại = edit + đăng lại = trùng). Nút trên web
  (`video_action`) chỉ đổi từ trạng thái hợp lệ. Chỉ 1 worker / máy (`Local\dytool-worker` mutex) và 1 app desktop
  (`Local\dytool-desktop`); `start()` khi luồng cũ còn sống thì dùng lại luồng đó.
- **Server cục bộ không có đăng nhập**: `_request_guard` chặn Host lạ (DNS rebinding) và POST có Origin/Referer khác
  dytool (CSRF). Test bằng `TestClient(..., base_url="http://127.0.0.1:8000")`. `/media/edited` là thư mục duy nhất
  được phục vụ tĩnh — đừng mount cả `data/` (cookie FB, app.db). Ghi vào config.yaml: lọc giá trị bằng regex chặt.
- `onsubmit="return confirm()"` vô tác dụng khi body có `hx-boost` → dùng `hx-confirm`. htmx bỏ qua phản hồi
  4xx/5xx → `base.html` hiện thông báo nổi `#dy-toast` (poll `every …` thì không).
- Trình cài / gỡ cài / "Cài đặt lại thư viện" đóng mọi tiến trình có **đường dẫn file chạy** trong `.venv|.python|vendor|tools`
  của thư mục cài (cả cây con, kể cả backend tải) — không lọc theo dòng lệnh. `RedirectionGuard=no` (Inno 7 truyền
  sang uv). `setup_app.ps1` ghi `data/setup_failed.txt` khi lỗi → Tổng quan / Cài đặt hiện nút Cài lại thư viện;
  ShardBrowser / VoiceStudio lỗi chỉ cảnh báo; file tải từ GitHub phải khớp `asset.digest`.
- **Giờ lịch đăng = giờ VN cố định UTC+7** (`store.vn_time / vn_ts / vn_fmt`, template `vnt(ts, fmt)`), không dùng
  `fromtimestamp` / `time.localtime` theo múi giờ máy. Hạn mức ngày tính theo ngày VN, gồm cả bài hẹn giờ.
- Worker chọn việc bằng SQL lọc sẵn Page đang bật (`_top_video`), không `LIMIT 20` rồi mới lọc. Backend tải hỏng → chờ
  10 phút (`_tdl_retry_at`). `_cleanup` (6 giờ/lần khi rảnh) dọn work/, raw, edited, ảnh log, nhật ký cũ.

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
- **torch CPU / GPU**: pyproject có 2 extra loại trừ nhau — `cpu` (torch mới nhất từ PyPI) và `gpu` (torch **2.14** cu126
  từ download.pytorch.org: bản cuối còn chạy card Pascal GTX 10xx; torch 2.15 bỏ Maxwell / Pascal / Volta).
  `setup_app.ps1` tự chọn: `nvidia-smi` thấy card kiến trúc ≥ 5.0 → `--extra gpu` (lỗi thì lùi `cpu`); chọn tay bằng
  `DYTOOL_TORCH`. **Mọi `uv run` phải `--no-sync`** (warm_models, run_ui.bat) — không thì uv đồng bộ về bản không
  extra và gỡ mất torch GPU. Demucs tự dùng CUDA; lỗi GPU (hết VRAM, card 4 GB chung VoiceStudio) → `dub.separate` chạy
  lại `-d cpu`. Đo GTX 1050 Ti: 30 giây âm thanh 18 s (GPU) vs 60 s (CPU i5-8300H). Whisper (CTranslate2) vẫn CPU.
- Chống ngủ máy: worker tự gọi `SetThreadExecutionState` khi chạy.
- Máy Windows của người dùng (10 LTSC, không có winget) **chặn chạy exe/junction trong `AppData`**: chrome.exe báo lỗi
  side-by-side, `uv python install` báo "Missing expected target directory for Python minor version link". Cùng file
  đặt ở `C:\` thì chạy được. Vì vậy runtime VoiceStudio ở `C:\VoiceStudio\runtime`. ShardBrowser đặt nhân trình duyệt
  ở `%APPDATA%\shardx-launcher\runtime\ShardX-Windows` → **bị chặn thật** (side-by-side). Cách chữa đang dùng: chép nhân
  ra `C:\ShardX\ShardX-Windows` + đặt `browser_path` trong `%APPDATA%\shardx-launcher\settings.json` (tắt launcher
  trước khi sửa); `browsers.sync_engine()` tự chép lại khi ShardBrowser cập nhật nhân. Token API = JWT HS256
  `{sub:"shardx-api", iat, exp}` ký bằng `api_secret` trong settings.json (giống `mint` ở src-tauri/src/api.rs) →
  `browsers.shardx_settings` tự tạo mỗi lần gọi, người dùng không phải dán (token trong config chỉ dùng cho API ở máy khác).
  Đã thử thật: hồ sơ tạm → start → CDP → example.com → stop OK; FFmpeg (bản BtbN, có libass) chép vào `~\.local\bin`.
- `page.goto` douyin.com / facebook.com dùng `wait_until="domcontentloaded"` + timeout dài: chờ "load" bị quá 30s
  trên mạng VN làm cửa sổ đăng nhập tự đóng.
- Phiên Facebook / cookie Douyin **không mang từ Mac sang** — đăng nhập lại trên Windows (tránh FB nghi ngờ đăng nhập lạ).
