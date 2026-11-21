# dytool - buoc cai thu vien sau khi chep file (file cai dytool-Setup.exe goi script nay).
# Chay lai bat cu luc nao de sua / cap nhat: Start Menu > dytool > "Cai dat lai thu vien".
# Giu file nay CHI ASCII + CRLF: Windows PowerShell 5.1 doc file theo bang ma ANSI.
#
# Moi thu nam trong thu muc cai (o C:): Python (.python), thu vien (.venv), Chromium (ms-playwright),
# FFmpeg (tools\ffmpeg), backend tai Douyin (vendor). Khong dung AppData vi co may Windows chan chay exe o do.
param(
    [switch]$Launch,     # cai xong thi mo dytool
    [switch]$NoPrompt    # khong hoi gi (cai VoiceStudio thi bo qua)
)
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
New-Item -ItemType Directory -Force -Path "$Root\data" | Out-Null
try { Start-Transcript -Path "$Root\data\setup.log" -Append | Out-Null } catch {}
try { $Host.UI.RawUI.WindowTitle = "dytool - cai dat thu vien" } catch {}

function Say($m) { Write-Host ""; Write-Host ">> $m" -ForegroundColor Cyan }
function Ok($m) { Write-Host "   [OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "   [!] $m" -ForegroundColor Yellow }
function Done-Or-Fail($what) { if ($LASTEXITCODE -ne 0) { throw "$what that bai (ma loi $LASTEXITCODE)" } }

$env:UV_PYTHON_INSTALL_DIR = "$Root\.python"
$env:UV_PYTHON_INSTALL_BIN = "0"
$env:PYTHONIOENCODING = "utf-8"
if (Get-ChildItem "C:\ms-playwright\chromium-*" -ErrorAction SilentlyContinue) { $env:PLAYWRIGHT_BROWSERS_PATH = "C:\ms-playwright" }
else { $env:PLAYWRIGHT_BROWSERS_PATH = "$Root\ms-playwright" }
function Refresh-Path {
    $env:Path = "$env:USERPROFILE\.local\bin;$Root\tools\ffmpeg\bin;" +
        [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
Refresh-Path

Write-Host "============================================================"
Write-Host "  dytool - cai thu vien (lan dau 15-30 phut, can mang)"
Write-Host "  Thu muc: $Root"
Write-Host "============================================================"

try {
    # ---------- 1. uv (quan ly Python) ----------
    Say "1/7 uv (quan ly Python)"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        Refresh-Path
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "Khong cai duoc uv" }
    }
    Ok (& uv --version)

    # ---------- 2. Python + thu vien ----------
    Say "2/7 Python 3.12 + thu vien (torch, whisper, demucs... ~3 GB, lau nhat)"
    & uv sync --frozen --python 3.12
    Done-Or-Fail "Cai thu vien Python"
    Ok "Thu vien Python xong"

    # ---------- 3. FFmpeg ban day du (co bo loc phu de) ----------
    Say "3/7 FFmpeg"
    $ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
    $hasSubs = $false
    if ($ff) { $hasSubs = ((& ffmpeg -hide_banner -filters 2>&1 | Out-String) -match " subtitles ") }
    if (-not $hasSubs) {
        $zip = "$env:TEMP\dytool-ffmpeg.zip"
        Invoke-WebRequest "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" -OutFile $zip -UseBasicParsing
        $tmp = "$Root\tools\ffmpeg-tmp"
        if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
        Expand-Archive $zip -DestinationPath $tmp -Force
        Remove-Item $zip
        $bin = (Get-ChildItem $tmp -Recurse -Filter ffmpeg.exe | Select-Object -First 1).DirectoryName
        if (Test-Path "$Root\tools\ffmpeg") { Remove-Item -Recurse -Force "$Root\tools\ffmpeg" }
        Move-Item (Split-Path -Parent $bin) "$Root\tools\ffmpeg"
        Remove-Item -Recurse -Force $tmp
        Refresh-Path
    }
    Ok ((& ffmpeg -hide_banner -version | Select-Object -First 1) -replace "Copyright.*", "")

    # ---------- 4. Chromium cho Playwright (dang nhap / dang bai) ----------
    Say "4/7 Trinh duyet Chromium ($env:PLAYWRIGHT_BROWSERS_PATH)"
    & uv run --frozen playwright install chromium
    Done-Or-Fail "Cai Chromium"
    Ok "Chromium xong"

    # ---------- 5. Backend tai Douyin (TikTokDownloader) ----------
    Say "5/7 Backend tai Douyin (TikTokDownloader)"
    $vendor = "$Root\vendor\TikTokDownloader"
    if (-not (Test-Path "$vendor\main.py")) {
        New-Item -ItemType Directory -Force -Path "$Root\vendor" | Out-Null
        $zip = "$env:TEMP\dytool-tdl.zip"
        Invoke-WebRequest "https://codeload.github.com/JoeanAmier/TikTokDownloader/zip/refs/heads/master" -OutFile $zip -UseBasicParsing
        Expand-Archive $zip -DestinationPath "$Root\vendor" -Force
        Remove-Item $zip
        if (Test-Path $vendor) { Remove-Item -Recurse -Force $vendor }
        Rename-Item "$Root\vendor\TikTokDownloader-master" "TikTokDownloader"
    }
    if (-not (Test-Path "$vendor\.venv")) {
        # uv.lock cua du an nay tro toi mirror Trung Quoc (bi chan 403) -> giai lai voi PyPI chinh thuc
        if (Test-Path "$vendor\uv.lock") { Move-Item "$vendor\uv.lock" "$vendor\uv.lock.mirror-bak" -Force }
        Push-Location $vendor
        & uv sync --python 3.12 --default-index https://pypi.org/simple
        $rc = $LASTEXITCODE
        Pop-Location
        if ($rc -ne 0) { throw "Cai backend tai Douyin that bai (ma loi $rc)" }
    }
    Ok "Backend tai Douyin xong"

    # ---------- 6. Cau hinh + model AI ----------
    Say "6/7 Cau hinh va model AI (~1.5 GB)"
    if (-not (Test-Path "$Root\config.yaml")) { Copy-Item "$Root\config.example.yaml" "$Root\config.yaml"; Ok "Da tao config.yaml" }
    & uv run --frozen python "$Root\scripts\warm_models.py"
    if ($LASTEXITCODE -ne 0) { Warn "Mot so model chua tai xong - tool tu tai khi dung lan dau" } else { Ok "Model AI xong" }

    # ---------- 7. VoiceStudio (long tieng) ----------
    Say "7/7 VoiceStudio (app long tieng Viet)"
    $vs = @("$env:LOCALAPPDATA\Programs\VoiceStudio\VoiceStudio.exe", "$env:ProgramFiles\VoiceStudio\VoiceStudio.exe") | Where-Object { Test-Path $_ }
    if ($vs) { Ok "VoiceStudio da cai" }
    elseif ($NoPrompt) { Warn "Chua co VoiceStudio - cai sau tai https://github.com/debpalash/VoiceStudio/releases" }
    else {
        $a = Read-Host "   Cai VoiceStudio bang trinh cai chinh thuc (voicestudio.sh)? (Y/N)"
        if ($a -match '^[YyCc]') {
            Invoke-RestMethod https://voicestudio.sh/install.ps1 | Invoke-Expression
            Warn "Mo VoiceStudio > Model Catalogue > chap nhan license OmniVoice + higgs-audio-v2-tokenizer"
            Warn "Neu VoiceStudio bao loi tai Python: bam 'Thay doi...' va chon thu muc runtime o o C:, vd C:\VoiceStudio\runtime"
        } else { Warn "Bo qua - long tieng can VoiceStudio, cai sau tai https://github.com/debpalash/VoiceStudio/releases" }
    }

    Say "CAI DAT XONG"
    Write-Host "   Mo dytool: bieu tuong dytool tren Desktop hoac Start Menu." -ForegroundColor Green
    if ($Launch) { Start-Process "$Root\dytool.exe" -WorkingDirectory $Root }
    $code = 0
}
catch {
    Write-Host ""
    Write-Host "LOI: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Kiem tra mang roi chay lai: Start Menu > dytool > Cai dat lai thu vien." -ForegroundColor Yellow
    Write-Host "Nhat ky: $Root\data\setup.log" -ForegroundColor Yellow
    $code = 1
}
try { Stop-Transcript | Out-Null } catch {}
if (-not $NoPrompt) { Write-Host ""; Read-Host "Nhan Enter de dong cua so nay" | Out-Null }
exit $code
