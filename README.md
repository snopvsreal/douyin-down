# dytool — Douyin → edit FFmpeg → Facebook Fanpage

Quét kênh / từ khóa Douyin → tải video → edit (che sub Trung, lật, phủ màu) → dịch + phụ đề + **lồng tiếng Việt**
(VoiceStudio, tự chọn giọng nam/nữ) → duyệt trên giao diện web → đăng / **lên lịch** Reels lên nhiều Page, nhiều tài khoản.
Ghi chú cho lập trình viên / Claude: xem [CLAUDE.md](CLAUDE.md).

## Cài đặt trên Windows 10/11

```powershell
git clone https://github.com/snopvsreal/douyin-down.git
cd douyin-down
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1   # cài Git, uv, FFmpeg (full), thư viện, Chromium
```

1. Cài **VoiceStudio** cho Windows: https://github.com/debpalash/VoiceStudio/releases/latest → mở app →
   Model Catalogue → chấp nhận license **OmniVoice** và **higgs-audio-v2-tokenizer**.
2. Mở `config.yaml`, điền `anthropic_api_key` (mục `edit > subtitles`).
3. `uv run dytool douyin-login` → đăng nhập douyin.com trong cửa sổ hiện ra (quét QR) → cookie tự lưu.
4. Chạy `scripts\run_ui.bat` → mở http://127.0.0.1:8000. **Đóng cửa sổ đen = tắt tool.**
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
