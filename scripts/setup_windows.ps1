# Cài đặt dytool trên Windows 10/11 (64-bit).
# Chạy trong PowerShell tại thư mục dự án:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Need($cmd, $wingetId, $name) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        Write-Host "[OK] $name da co" -ForegroundColor Green
    } else {
        Write-Host "[..] Cai $name ($wingetId)..." -ForegroundColor Yellow
        winget install --id $wingetId -e --accept-source-agreements --accept-package-agreements
    }
}

# 1) Công cụ hệ thống
Need git    "Git.Git"        "Git"
Need uv     "astral-sh.uv"   "uv (quan ly Python)"
Need ffmpeg "Gyan.FFmpeg"    "FFmpeg (ban full, co libass de ve phu de)"

# winget vua cai thi PATH chua cap nhat trong cua so nay -> nap lai PATH
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")

# 2) Kiểm tra FFmpeg có bộ lọc phụ đề (libass)
$filters = (& ffmpeg -hide_banner -filters 2>$null) -join "`n"
if ($filters -notmatch " subtitles ") {
    Write-Warning "FFmpeg khong co bo loc 'subtitles' (libass). Hay cai ban 'full' tu https://www.gyan.dev/ffmpeg/builds/"
}

# 3) Thư viện Python + trình duyệt Chromium cho Playwright
uv sync --python 3.12
uv run playwright install chromium

# 4) File cấu hình
if (-not (Test-Path config.yaml)) {
    Copy-Item config.example.yaml config.yaml
    Write-Host "[..] Da tao config.yaml tu config.example.yaml - hay dien anthropic_api_key" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "XONG. Buoc tiep theo:" -ForegroundColor Cyan
Write-Host "  1. Cai VoiceStudio cho Windows: https://github.com/debpalash/VoiceStudio/releases/latest"
Write-Host "     mo app, chap nhan license OmniVoice + higgs-audio-v2-tokenizer trong Model Catalogue"
Write-Host "  2. Dien anthropic_api_key vao config.yaml (edit > subtitles)"
Write-Host "  3. uv run dytool douyin-login     (dang nhap douyin.com, luu cookie)"
Write-Host "  4. scripts\run_ui.bat              (mo giao dien http://127.0.0.1:8000)"
Write-Host "  5. Trong giao dien: Tai khoan & Page -> them tai khoan -> Dang nhap Facebook"
