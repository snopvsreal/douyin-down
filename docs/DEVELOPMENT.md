# Dành cho lập trình viên

Ghi chú kiến trúc chi tiết (từng module, bài học, quy tắc an toàn): xem [CLAUDE.md](../CLAUDE.md).
Người dùng bình thường chỉ cần [trình cài Windows](../README.md#-cài-đặt).

## Chạy từ mã nguồn

Cần [uv](https://docs.astral.sh/uv/) và FFmpeg **bản full** (có libass để vẽ phụ đề).

```bash
uv sync --extra cpu            # hoặc --extra gpu: torch 2.14 CUDA 12.6, chạy được card GTX 10xx
uv run --no-sync playwright install chromium
cp config.example.yaml config.yaml
uv run --no-sync dytool ui --autostart      # giao diện http://127.0.0.1:8000, tự bật chạy tự động
```

> [!IMPORTANT]
> Sau khi cài bản GPU, mọi lệnh phải có `--no-sync` — không thì uv đồng bộ lại về bản không extra và gỡ mất torch GPU.

**macOS (Apple Silicon):**

```bash
brew tap homebrew-ffmpeg/ffmpeg && brew install homebrew-ffmpeg/ffmpeg/ffmpeg uv
uv sync --python 3.12 && uv run playwright install chromium
cp config.example.yaml config.yaml
nohup caffeinate -i uv run dytool ui --autostart > data/ui.log 2>&1 &   # chạy nền, log ở data/ui.log
```

**Windows từ mã nguồn (cách cũ):** bấm đúp `install_windows.bat` (cần winget — Windows 10 LTSC không có) hoặc
`powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1`.

## Lệnh dòng lệnh

| Lệnh | Việc |
|---|---|
| `dytool ui [--autostart] [--lan]` | Giao diện web (`--lan`: mở từ điện thoại cùng wifi) |
| `dytool douyin-login` | Đăng nhập douyin.com, lưu cookie vào `config.yaml` |
| `dytool edit-file video.mp4` | Edit thử 1 file theo `config.yaml` |
| `dytool tt-dryrun <id tài khoản> video.mp4` | Thử đăng TikTok tới trước nút Post (không đăng thật) |
| `dytool server` | Backend TikTokDownloader (cổng 5555) |
| `dytool scan` / `run --no-post` / `watch` / `status` | Chế độ CLI cũ 1 Page (cấu hình trong `config.yaml`) |

## Mẫu edit (`edit:` trong `config.yaml` / Cài đặt kênh)

| Bước | Tham số |
|---|---|
| Che sub Trung | `hide_hardsub` — tự dò dải chữ bằng OCR, làm mờ có mép mờ dần |
| Lật ngang | `hflip: true` |
| Nghiêng | `rotate_deg: 1` — tự phóng to để không lộ góc đen |
| Phủ màu | `tint.color: 0x0066FF`, `tint.opacity: 0.12` |
| Phụ đề Việt | Whisper nghe → AI dịch → phụ đề ASS kiểu video ngắn |
| Lồng tiếng | `dub` — Demucs tách giọng → AI dịch theo thời lượng → OmniVoice đọc |

## Dữ liệu

| Đường dẫn | Nội dung |
|---|---|
| `data/app.db` | SQLite: tài khoản, kênh, nguồn, video, nhật ký |
| `data/raw`, `data/edited` | Video gốc / đã edit |
| `data/accounts/<id>/` | Trạng thái từng tài khoản (hồ sơ trình duyệt gắn với tài khoản) |
| `data/shardx/` | Nhân trình duyệt ShardX + hồ sơ, proxy |
| `data/models/omnivoice`, `data/voice/` | Model giọng nói + giọng đã lưu, lịch sử |

`config.yaml` và `data/` chứa cookie / phiên đăng nhập — **không đưa lên git**.

## Build trình cài Windows

```powershell
powershell -ExecutionPolicy Bypass -File installer\build.ps1     # -> dist\dytool-Setup-<ver>.exe (cần Inno Setup 7)
```

Xem 4 màn hình trình cài mà không cần cài: `build\dytool-setup.exe /SNAPSHOT=<thư mục>` (chạy từ PowerShell).
Ảnh README: banner + sơ đồ `uv run --no-sync python scripts/make_readme_images.py`; ảnh chụp app (dữ liệu mẫu, không lộ
kênh thật) xem đầu file `scripts/readme_demo.py`.

## Phát hành bản mới

1. Tăng `version` trong `pyproject.toml`, ghi mục mới ở đầu [CHANGELOG.md](../CHANGELOG.md).
2. Build trình cài, tạo release `v<ver>` trên GitHub kèm `dytool-Setup-<ver>.exe` — app đang cài tự thấy bản mới
   (bắt buộc khớp `asset.digest` SHA256).
