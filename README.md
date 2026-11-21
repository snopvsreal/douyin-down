# dytool — Douyin → edit FFmpeg → Facebook Fanpage

Quét kênh / từ khóa Douyin → tải video → edit (che sub Trung, lật, phủ màu) → dịch + phụ đề + **lồng tiếng Việt**
(VoiceStudio, tự chọn giọng nam/nữ) → duyệt trên giao diện web → đăng / **lên lịch** Reels lên nhiều Page, nhiều tài khoản.
Ghi chú cho lập trình viên / Claude: xem [CLAUDE.md](CLAUDE.md).

## Cài đặt trên Windows 10/11 — app dytool

1. Chạy **`dytool-Setup-<phiên bản>.exe`** (không cần quyền admin, mặc định cài vào `C:\dytool`).
   Bước cuối tự tải thư viện (~4 GB: Python, torch, Chromium, FFmpeg, backend Douyin, model AI) — lần đầu 15–30 phút,
   xem tiến độ ở cửa sổ màu xanh. Windows SmartScreen có thể cảnh báo vì file chưa ký số: bấm *More info → Run anyway*.
2. Mở **dytool** (Desktop / Start Menu): app có cửa sổ riêng, không cần trình duyệt. Lần đầu dán Anthropic API key
   ngay trên trang Tổng quan. VoiceStudio mở bằng nút **Mở** ở thanh bên.
3. **Đóng cửa sổ = thu xuống khay** (cạnh đồng hồ), tool vẫn chạy để edit / đăng theo lịch. Thoát hẳn: chuột phải
   icon dytool ở khay → *Thoát dytool*.
4. Lỗi thư viện / muốn cập nhật: Start Menu → **dytool - Cài đặt lại thư viện**. Cài bản mới: chạy file Setup mới
   đè lên — dữ liệu (`data\`, `config.yaml`) được giữ. Gỡ cài đặt cũng giữ 2 mục này.

Build file cài (lập trình viên): `powershell -ExecutionPolicy Bypass -File installer\build.ps1` → `dist\` (cần Inno Setup 7).

### Cách cũ: chạy từ mã nguồn

**Bấm đúp `install_windows.bat`.** File này tự làm hết: cài Git, uv (Python 3.12), FFmpeg bản full, thư viện Python,
Chromium, backend tải Douyin, tải trước model AI (~1.5 GB), tạo `config.yaml` (hỏi Anthropic API key), cài VoiceStudio
(hỏi trước), tạo shortcut **dytool** trên Desktop, hỏi đăng nhập Douyin và mở tool.

- Đặt trong thư mục dự án → cài tại chỗ.
- Dùng riêng (chỉ copy mỗi file này sang máy mới) → tự đăng nhập GitHub và clone repo private về `%USERPROFILE%\douyin-down`.
- Lỗi giữa chừng: sửa rồi chạy lại, các bước đã xong sẽ bỏ qua. Lần đầu mất khoảng 15–30 phút.

Cài từng bước (thay cho file trên): `powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1`.

Sau khi cài:

1. Mở **VoiceStudio** → Model Catalogue → chấp nhận license **OmniVoice** và **higgs-audio-v2-tokenizer**.
2. Chưa nhập API key lúc cài → điền `anthropic_api_key` trong `config.yaml` (mục `edit > subtitles`).
3. Cookie Douyin hết hạn → nút **Đăng nhập Douyin** trên giao diện (hoặc `uv run dytool douyin-login`).
4. Mở tool: shortcut **dytool** trên Desktop (= `scripts\run_ui.bat`) → http://127.0.0.1:8000. **Đóng cửa sổ đen = tắt tool.**
   Tự chạy khi bật máy: tạo shortcut của `run_ui.bat` trong thư mục `shell:startup`.
5. Trên giao diện: **Tài khoản & Page** → thêm tài khoản → *Đăng nhập Facebook* → bật Page → thêm nguồn.

## Cài đặt trên macOS (Apple Silicon)

```bash
brew tap homebrew-ffmpeg/ffmpeg && brew install homebrew-ffmpeg/ffmpeg/ffmpeg uv   # ffmpeg có libass để vẽ phụ đề
uv sync --python 3.12 && uv run playwright install chromium
cp config.example.yaml config.yaml
```

## Cấu hình `config.yaml`

1. **Cookie Douyin**: `uv run dytool douyin-login` (mọi hệ điều hành) hoặc trên macOS `uv run dytool cookie`
   (đọc từ Chrome đã đăng nhập).
2. **Kênh / từ khóa**: thêm link `https://www.douyin.com/user/<sec_uid>` vào `douyin.channels`, từ khóa (tiếng Trung) vào `douyin.keywords`.
3. **Dịch phụ đề**: `export ANTHROPIC_API_KEY=...` (Google Translate miễn phí hay bị chặn captcha).
4. **Facebook**: `facebook.page_id` + `facebook.page_token` (Page Access Token có quyền `pages_manage_posts`, `pages_read_engagement`). Kiểm tra: `uv run dytool fb-check`.

## Giao diện web (nhiều tài khoản / nhiều Page)

```bash
uv run dytool ui            # mở http://127.0.0.1:8000   (--lan để mở từ điện thoại cùng wifi)
uv run dytool ui --autostart   # mở giao diện và tự bật chạy tự động

# chạy nền, không phụ thuộc cửa sổ Terminal / app nào (log ở data/ui.log):
nohup caffeinate -i uv run dytool ui --autostart > data/ui.log 2>&1 &
```

1. **Tài khoản & Page** → thêm tài khoản Facebook → *Đăng nhập Facebook* (cửa sổ Chromium mở ra, bạn tự đăng nhập) → tool tự tìm các Page.
2. Bấm **Cài đặt** ở Page muốn dùng → thêm nguồn (từ khóa / kênh Douyin), chỉnh mẫu edit, giọng, caption, giới hạn đăng → tick *Bật Page này*.
3. **Tổng quan** → *Bật* backend tải Douyin, mở app VoiceStudio → *Bật chạy tự động*.
4. **Duyệt video** → xem video đã edit, sửa caption → *Duyệt* (đăng theo lịch) hoặc *Đăng ngay*.
5. **Tiến trình** → xem từng video đang ở bước nào, lỗi gì, *Thử lại*.

Dữ liệu: `data/app.db`; phiên Facebook từng tài khoản: `data/accounts/<id>/`.

## Chạy bằng dòng lệnh

Cửa sổ 1 — backend tải Douyin (lần đầu tự clone TikTokDownloader vào `vendor/`):

```bash
uv run dytool server
```

Cửa sổ 2:

```bash
uv run dytool edit-file video.mp4      # edit thử 1 file để duyệt mẫu
uv run dytool run --no-post            # 1 lượt: quét + tải + edit, chưa đăng
uv run dytool watch                    # chạy liên tục, tự bắt video mới và đăng theo lịch
uv run dytool status                   # thống kê
```

## Mẫu edit (`edit:` trong config)

| Bước | Tham số |
|---|---|
| Che sub Trung | `hide_hardsub` — làm mờ dải ngang `y_from`→`y_to` (% chiều cao) |
| Lật ngang | `hflip: true` |
| Nghiêng | `rotate_deg: 1` — tự phóng to để không lộ góc đen |
| Phủ màu | `tint.color: 0x0066FF`, `tint.opacity: 0.12` |
| Sub Việt | Whisper nhận dạng giọng Trung → dịch → vẽ phụ đề nền mờ |

Video: `data/raw` (gốc), `data/edited` (đã edit). Trạng thái: `data/dytool.db`.
