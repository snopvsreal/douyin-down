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
VoiceStudio chỉ hiện khi máy chưa có; `PrepareToInstall` đóng dytool đang chạy; bước thư viện chạy
`setup_app.ps1 -Wizard` ẩn qua `ExecAndLogOutput`, dòng `▸ N/7` điều khiển thanh tiến độ — tên bước lấy từ
`[CustomMessages]` vì chữ UTF-8 trong log có thể vỡ); `build.ps1` → `dist\dytool-Setup-<ver>.exe`. Bước tải thư viện: `scripts/setup_app.ps1` (ASCII + CRLF; Python vào `.python`, FFmpeg vào `tools\ffmpeg`, TikTokDownloader tải zip không cần git). Icon: `scripts/make_icon.py` |
| `web/app.py` + `web/templates/` | FastAPI + Jinja2 + HTMX (htmx vendored ở `web/static`). **Bố cục (đợt "dễ dùng & hiện đại", thiết kế đã duyệt)** — menu trái 3 nhóm: LÀM VIỆC **Hôm nay** `/` (Ctrl+1: "Cần bạn xử lý" gom video chờ duyệt / lỗi / tài khoản cần đăng nhập / Douyin / VoiceStudio / API key, dây chuyền, lịch hôm nay, kênh đang bật bài hôm nay / giới hạn), **Hộp duyệt** `/review` (Ctrl+2: hàng chờ | video edit / gốc `/media/raw/<id>` / cạnh nhau | caption + "Đăng lúc": khung trống tiếp theo / theo cài đặt kênh / đăng ngay / chọn giờ → `POST /review/act` rồi tự mở video kế; phím J K S A X E Space), **Lịch đăng** `/calendar` (Ctrl+3: 7 ngày từ hôm nay × khung giờ các kênh, kéo bài chờ gửi lịch / video đã duyệt vào ô → `/calendar/move` (chặn trùng giờ cùng kênh, < 20 phút), "Tự xếp" → `plan.auto_place`, xem Danh sách), **Video** `/queue` (Ctrl+4, tìm + lọc kênh); KÊNH: mỗi Page / kênh TikTok 1 dòng chấm màu (`plan.nav_pages`, màu theo id) → **trang kênh** `/pages/<id>` (tab Nguồn / Đăng & lịch / Edit & lồng tiếng / Caption / Đăng kèm · nhóm / Lọc, **tự lưu** khi sửa — form `hx-trigger="autosave"`, `page_save` trả HTML khi có `HX-Request`; công tắc Bật kênh `form="settings"`; Sao chép cài đặt sang kênh khác); HỆ THỐNG Tài khoản (Ctrl+5) / Trình duyệt (Ctrl+6) / Cài đặt (Ctrl+7). Cuối menu thẻ **Chạy tự động** (`_runcard.html`, công tắc `/worker/toggle`). Thanh trên cùng: đường dẫn (`{% block crumb %}`) + chip Douyin / Tải Douyin / VoiceStudio / cập nhật (`_services.html`) — bỏ thanh trạng thái dưới đáy. **Ctrl+K** hộp tìm (`/partials/palette`: trang, lệnh, kênh, video). Dữ liệu các trang mới ở `web/plan.py`. **Trình duyệt** (`/browsers`, như app ShardBrowser — tab Hồ sơ / Proxy / Tiện ích / Thùng rác + trang chi tiết `/browsers/<id>`: tên, ghi chú, nhãn (lọc), ngôn ngữ, proxy + WebRTC, dấu vân tay (chọn mẫu / làm mới, nhiễu), cookie nhập / xuất, tiện ích, bookmark, tham số khởi động, nhân bản, Mở / Đóng, xoá vào thùng rác 30 ngày — chặn khi đang gắn tài khoản / đang mở; logic ở `browser_mgr.py`). Template trang con của base.html **đừng đặt biến `p`**: base.html `{% set p = request.url.path %}` đè mất, Cài đặt kênh (mục lục + thanh Lưu dính), **Cài đặt** chung (`/settings`, cuối menu, Ctrl+6: Cập nhật, API key + VoiceStudio, Trình duyệt, Douyin, Thông tin — Tổng quan / Tài khoản chỉ còn dòng nhắc trỏ sang đây; macro dùng chung ở `_macros.html`). Giao diện theo phong cách **ShardBrowser Launcher** (token màu / bo góc lấy từ ui-kit `tokens.css` của ShardBrowser, MIT): nền xám `--canvas`, 2 tấm `--surface` bo 12px (menu trái + trang, cách 8px; chỉ tấm trang cuộn), thẻ `.card` bo 10px viền mảnh, màu nhấn chàm `#535efd`, font **Inter đóng gói** `static/fonts` (OFL, có bộ tiếng Việt), nhãn in hoa nhỏ (nhóm menu `.nav-sec` Làm việc / Kênh, tiêu đề cột bảng, nhãn ô thống kê `.flow`), nhãn trạng thái `.pill.ok/warn/err` có chấm, tab lọc `.filters` gạch chân, nút / ô nhập 32–34px bo 8px. **Sáng / tối / tự động** ở cuối menu (`html[data-theme]`, localStorage `dy-theme`; script trong `<head>` đặt trước khi vẽ) — màu khai báo theo vai trò (`--t1/--t2/--t3`, `--tile`, `--line`, `--primary-*`, `--ok-*`…) ở `:root` + `[data-theme=dark]` + `@media (prefers-color-scheme: dark) :root:not([data-theme=light])`; tên cũ `--text/--muted/--ok/--warn/--err` giữ làm bí danh (template dùng trong style). mọi `hx-trigger` `every …` có thêm `[!document.hidden]` (cửa sổ ẩn xuống khay thì không hỏi máy chủ; thêm trigger mới cũng phải có), `hx-boost` trên body (script trong template phải chống gắn lặp bằng cờ `window.__dy…`), Ctrl+1..7 chuyển trang, cửa sổ < 900px thu menu còn icon. CSS: tránh tên class trùng `.done` chung; lưới dùng `minmax(0, 1fr)` để không tràn ngang; sửa template bằng PowerShell thì ghi UTF-8 **không BOM** (BOM làm trang vào quirks mode) |
| `updates.py` + `web/templates/_updates.html` | **Tự cập nhật trong app** dytool / VoiceStudio: hỏi GitHub `releases/latest` (mở app + 6 giờ/lần), so với bản đang cài (dytool: metadata gói; VoiceStudio: `DisplayVersion` trong Uninstall registry). Tải file cài chính thức, **bắt buộc khớp `asset.digest` SHA256** (không có / sai thì không cài). VoiceStudio: đóng app (không khi đang edit) → `/S` → mở lại; dytool: trình cài `/SILENT /RELAUNCH` (tách tiến trình) — `PrepareToInstall` đóng app, `ssDone` mở lại. Khoá dict trong template đừng tên `update` / `items` (trùng hàm dict) |
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
| `browsers.py` | **Trình duyệt = nhân ShardX nhúng qua SDK Python `shardx`** (github.com/ProxyShard/ShardBrowser/tree/main/sdks/python, MIT; người dùng yêu cầu nhúng thay cho app ShardBrowser — **không cần cài / mở ShardBrowser**). Nhân + thư viện dấu vân tay tải **từ bản sao trên GitHub của người dùng** `snopvsreal/shardx-engine` (bản phát hành tag `shardx-<chromium>-b<build>` mới nhất, mỗi file **bắt buộc khớp SHA256** `asset.digest`; `_use_mirror` thay `_fetch_manifest` / `_download` / `_install_fingerprints` của SDK — vì thế **ghim `shardx==2.1.7`**, nâng SDK thì thử lại). Bản sao **tự động**: GitHub Actions trong kho shardx-engine (`mirror.yml`, 6 giờ / lần, `mirror.py`) tải từ R2 + runtime.json của ProxyShard, chép nguyên, đăng bản phát hành mới khi có nhân mới (không chép Widevine — SDK tự lấy từ Google); mỗi tháng commit `last-check.txt` để GitHub không tắt lịch. Nhân mới đòi SDK > `SDK_PIN` trong workflow → workflow lỗi (GitHub gửi email), không đăng: nâng shardx ở dytool, thử lại, sửa `SDK_PIN`. Về `data/shardx` (~440 MB, `install_engine_background` lúc mở app + bước 4/7 của `setup_app.ps1`; ở C:\ vì máy người dùng chặn exe trong AppData). Hồ sơ: `data/shardx/profiles/<uuid hex>/` (profile.json + cookie, tên dytool đặt ở `dytool.json`); mỗi tài khoản FB / TikTok gắn 1 hồ sơ (`data/accounts/<id>/browser.json` `{"engine":"sdk",…}`), Douyin dùng `data/browser.json`. browser.json kiểu cũ (hồ sơ app ShardBrowser, không có `engine`) = chưa có hồ sơ → trang Tài khoản hiện "Cần đăng nhập lại", worker **bỏ qua (chờ)** tài khoản chưa có hồ sơ thay vì đăng lỗi. `create_profile` = `sdk.create_profile(platform="Windows")` rồi chỉ đặt `timezone`/`navigator.language` = `"auto"` (SDK lấy theo IP mỗi lần mở — mẫu ngẫu nhiên có thể là Ba Lan); TikTok: ngôn ngữ cố định `en-US` (bộ chọn tiếng Anh của tiktok-uploader). Không tự viết logic giả dấu vân tay. `attach`: `sdk.launch(cdp=True)` → Playwright `connect_over_cdp`; hồ sơ đang mở sẵn → nối vào phiên đó (không mở lần 2, không đóng khi xong). Đang chạy = `lockfile` trong thư mục hồ sơ bị khoá (đừng gọi cổng trong `DevToolsActivePort` — file còn sót sau khi đóng, gọi cổng đã đóng chờ ~2 s). SDK `print` ký tự "→" → `_quiet()` chuyển stdout sang UTF-8 1 lần (log cp1252). **Đừng dùng `contextlib.redirect_stdout`** trong app nhiều luồng: nó đổi `sys.stdout` cả tiến trình → luồng nền nuốt log của luồng khác. Mã hồ sơ kiểm tra regex 32 hex trước khi ghép đường dẫn |
| `browser_mgr.py` | Quản lý hồ sơ (dữ liệu trong `data/shardx`): `proxies.json` (dán nhiều dòng: `scheme://u:p@h:port`, `h:port`, `h:port:u:p`, `u:p@h:port`; kiểm tra = `sdk.check_proxy` → IP / nước / UDP), cài đặt hồ sơ ở `profiles/<id>/dytool.json` (`browsers.read_meta/write_meta`: notes, tags, proxy_id, webrtc, extensions, bookmarks, extra_args). `browsers._launch_kwargs` áp khi mở: proxy (kiểm tra TCP trước — chết thì `BrowserError`, không đăng), WebRTC, `--load-extension` (tiện ích tải .crx từ máy chủ cập nhật Google vào `extensions/<id>`, chặn đường dẫn lạ khi giải nén), bookmark = thư mục "dytool" trong `Default/Bookmarks`, tham số thêm (chặn tham số dytool tự đặt). Cookie nhập / xuất qua Playwright (route async → `run_in_threadpool`: Playwright đồng bộ không chạy trên vòng lặp async); tên file tải về chỉ ASCII. Làm mới dấu vân tay giữ ngôn ngữ / nhiễu; nhân bản = cùng cài đặt, dấu vân tay mới, chưa đăng nhập. Thùng rác `trash/<id>` tự xoá sau 30 ngày. Dung lượng hồ sơ (`browsers.profile_size_mb`) tính ở luồng nền, nhớ 5 phút — đừng duyệt thư mục hồ sơ lúc vẽ trang (hồ sơ dùng lâu hàng nghìn file cache); 220 mẫu dấu vân tay đọc sẵn lúc mở app |
| `ai.py` + `web/templates/_ai.html` | **AI qua CLI, không dùng API key** (người dùng yêu cầu): Claude CLI (`@anthropic-ai/claude-code`), Codex CLI (`@openai/codex`), Gemini CLI (`@google/gemini-cli`) — Cài đặt › AI có Cài (npm `--prefix tools/ai-cli`, máy chưa có Node thì tải Node zip vào `tools/node`, kiểm SHA256), Đăng nhập (mở console riêng `CREATE_NEW_CONSOLE`: `claude auth login`, `codex login`, `gemini`), Dùng cái này (`data/ai.json`), Thử. Đã đăng nhập = hỏi chính CLI (`claude auth status` JSON `loggedIn`, `codex login status`; Gemini xem `~/.gemini/oauth_creds.json`) — **file đăng nhập cũ còn trên đĩa nhưng hết hạn**, chỉ xem file là sai; nhớ 20 s, hỏi song song. Gọi: đề bài qua stdin, chạy trong thư mục tạm trống (`claude -p --output-format text`, `codex exec --skip-git-repo-check --ephemeral -s read-only -o <file> -`, `gemini -p …`), bỏ `ANTHROPIC_API_KEY` khỏi env. `subtitles._claude` (dịch phụ đề / lồng tiếng / rút gọn) và `keywords` đều đi qua `ai.ask` |
| `keywords.py` | Nguồn **từ khoá tiếng Việt** → cụm tìm Douyin tiếng Trung: AI (`ai.py`) viết cụm người Trung hay gõ + 2 cụm khác; chưa có AI / lỗi → dịch máy Google (cổng `translate.googleapis.com/translate_a/single?client=gtx` — `deep_translator.GoogleTranslator` hay bị chặn "too many requests") → MyMemory. Nhớ kết quả trong bộ nhớ. Trang kênh: gõ ô từ khoá → `/partials/kw-translate` xem trước (`_kw_preview.html`, hidden `zh`, bấm cụm khác để đổi); thêm / sửa nguồn (`app._keyword`) lưu `value` = tiếng Trung, `params.vi` = câu gốc (hiện cạnh nguồn). Gõ sẵn tiếng Trung / nguồn Kênh → giữ nguyên |
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
- **Cập nhật trực tiếp toàn app**: `store.rev()` = `conn.total_changes` (mọi lần ghi của app + worker qua 1 kết nối) + trạng thái chạy tự động → `/partials/rev`; trang vẽ kèm `.app[data-rev]`, script trong base.html hỏi 3 s / lần, khác thì lấy lại trang, thay `<main>` + `nav.side` bằng `htmx.swap` (giữ `<details>` mở, thẻ Chạy tự động, vị trí cuộn). **Hoãn** khi đang gõ / chọn trong ô, có sửa chưa lưu (input ngoài form tự lưu, xoá khi POST xong), video đang phát, kéo thả, Ctrl+K mở. Script trong `<main>` chạy lại mỗi lần vẽ → phải chịu được chạy lặp. Trang kênh: phản hồi tự lưu gửi kèm `#ch-pill`, `#ch-summary`, `#nav-ch-<id>` bằng `hx-swap-oob` (`_ch_live.html` / `_ch_saved.html`) để đổi ngay cả khi đang gõ. Thay đổi chỉ ở file (hồ sơ trình duyệt, AI) không làm đổi rev.
- `hx-boost` chỉ thay `<body>`, giữ `<head>` cũ → cửa sổ đang mở vẫn dùng `app.css` cũ khi CSS đổi (trang mới vỡ bố cục: svg không có kích thước CSS vẽ 300×150). `.app[data-css]` mang phiên bản CSS, script đầu body tự đổi `<link>` khi khác. Svg mới: ghi sẵn `width`/`height`.
- `onsubmit="return confirm()"` vô tác dụng khi body có `hx-boost` → dùng `hx-confirm`. htmx bỏ qua phản hồi
  4xx/5xx → `base.html` hiện thông báo nổi `#dy-toast` (poll `every …` thì không) — lỗi trả `PlainTextResponse(msg, 400)` để hiện đúng lời nhắn.
- Trình cài / gỡ cài / "Cài đặt lại thư viện" đóng mọi tiến trình có **đường dẫn file chạy** trong `.venv|.python|vendor|tools`
  của thư mục cài (cả cây con, kể cả backend tải) — không lọc theo dòng lệnh. `RedirectionGuard=no` (Inno 7 truyền
  sang uv). `setup_app.ps1` ghi `data/setup_failed.txt` khi lỗi → Tổng quan / Cài đặt hiện nút Cài lại thư viện;
  ShardBrowser / VoiceStudio lỗi chỉ cảnh báo; file tải từ GitHub phải khớp `asset.digest`.
- **Giờ lịch đăng = giờ VN cố định UTC+7** (`store.vn_time / vn_ts / vn_fmt`, template `vnt(ts, fmt)`), không dùng
  `fromtimestamp` / `time.localtime` theo múi giờ máy. Hạn mức ngày tính theo ngày VN, gồm cả bài hẹn giờ.
- `vendor/TikTokDownloader/Volume/settings.json` có thể có BOM (sửa bằng Notepad / PowerShell) → đọc bằng `utf-8-sig` (trước: `cmd_server` chết ngay, backend tải Douyin không bao giờ bật, thanh trạng thái luôn "Tải Douyin tắt"). Đọc file JSON người dùng có thể sửa tay: dùng `utf-8-sig`.
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
  đặt ở `C:\` thì chạy được. Vì vậy runtime VoiceStudio ở `C:\VoiceStudio\runtime`. Nhân trình duyệt ShardX
  (SDK) để ở `C:\dytool\data\shardx` vì lý do này — trước đây app ShardBrowser để nhân trong AppData nên phải chép ra
  `C:\ShardX` (code đó đã bỏ cùng app ShardBrowser).
  FFmpeg (bản BtbN, có libass) chép vào `~\.local\bin`.
- `page.goto` douyin.com / facebook.com dùng `wait_until="domcontentloaded"` + timeout dài: chờ "load" bị quá 30s
  trên mạng VN làm cửa sổ đăng nhập tự đóng.
- Phiên Facebook / cookie Douyin **không mang từ Mac sang** — đăng nhập lại trên Windows (tránh FB nghi ngờ đăng nhập lạ).
