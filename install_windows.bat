<# : batch portion
@REM Phan dau la lenh cmd; phan PowerShell nam sau dau dong ket thuc comment ben duoi.
@echo off
title dytool - Cai dat
echo ============================================================
echo   dytool - cai dat tat ca de chay du an tren Windows 10/11
echo ============================================================
powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:DYTOOL_BAT='%~f0'; iex ((Get-Content -LiteralPath '%~f0' -Raw))"
echo.
pause
goto :EOF
#>
# ----- phan POWERSHELL -----
# File nay tu chua toan bo trinh cai dat: dat trong thu muc du an (cai tai cho) hoac dung rieng
# (tu clone repo private snopvsreal/douyin-down ve %USERPROFILE%\douyin-down).
# "Continue": lenh ngoai (uv, gh, git) ghi ra stderr la binh thuong; tu kiem tra $LASTEXITCODE
$ErrorActionPreference = "Continue"
$Repo = "snopvsreal/douyin-down"

function Say($msg, $color = "Cyan") { Write-Host ""; Write-Host ">> $msg" -ForegroundColor $color }
function Ok($msg) { Write-Host "   [OK] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "   [!] $msg" -ForegroundColor Yellow }
function Ask($q) { $a = Read-Host "$q (Y/N)"; return ($a -match '^[YyCc]') }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User") + ";$env:USERPROFILE\.local\bin"
}
function Need($cmd, $id, $name) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) { Ok "$name da co"; return }
    Write-Host "   [..] Cai $name ..." -ForegroundColor Yellow
    winget install --id $id -e --silent --accept-source-agreements --accept-package-agreements | Out-Host
    Refresh-Path
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "Cai $name that bai - hay cai tay roi chay lai file nay" }
    Ok "$name da cai xong"
}

try {
    # ---------- 0. winget ----------
    Say "Kiem tra winget (trinh cai dat cua Windows)"
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Khong co winget. Cai 'App Installer' tu Microsoft Store roi chay lai file nay."
    }
    Ok "winget san sang"

    # ---------- 1. cong cu he thong ----------
    Say "Cai cong cu he thong"
    Need git    "Git.Git"      "Git"
    Need uv     "astral-sh.uv" "uv (quan ly Python)"
    Need ffmpeg "Gyan.FFmpeg"  "FFmpeg ban full"
    $filters = (& ffmpeg -hide_banner -filters 2>&1 | Out-String)
    if ($filters -match " subtitles ") { Ok "FFmpeg co bo loc phu de (libass)" }
    else { Warn "FFmpeg thieu bo loc 'subtitles' - tai ban full tai https://www.gyan.dev/ffmpeg/builds/" }

    # ---------- 2. thu muc du an ----------
    Say "Tim thu muc du an"
    $here = Split-Path -Parent $env:DYTOOL_BAT
    if (Test-Path (Join-Path $here "pyproject.toml")) {
        $proj = $here
    } else {
        $proj = Join-Path $env:USERPROFILE "douyin-down"
        if (-not (Test-Path (Join-Path $proj "pyproject.toml"))) {
            Need gh "GitHub.cli" "GitHub CLI"
            & gh auth status *> $null
            if ($LASTEXITCODE -ne 0) {
                Warn "Repo la private: hay dang nhap GitHub (chon GitHub.com > HTTPS > Login with a web browser)"
                & gh auth login --hostname github.com --git-protocol https --web
            }
            & gh repo clone $Repo $proj
            if ($LASTEXITCODE -ne 0) { throw "Clone repo that bai" }
        }
    }
    Set-Location $proj
    Ok "Du an: $proj"

    # ---------- 3. thu vien Python + Chromium ----------
    Say "Cai thu vien Python (lan dau mat 5-15 phut: torch, whisper, demucs...)"
    & uv sync --python 3.12
    if ($LASTEXITCODE -ne 0) { throw "uv sync that bai" }
    Ok "Thu vien Python xong"
    Say "Cai trinh duyet Chromium cho Playwright (dang Facebook / dang nhap Douyin)"
    & uv run playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw "Cai Chromium that bai" }
    Ok "Chromium xong"

    # ---------- 4. backend tai Douyin (TikTokDownloader) ----------
    Say "Cai backend tai Douyin (TikTokDownloader)"
    $vendor = Join-Path $proj "vendor\TikTokDownloader"
    if (-not (Test-Path $vendor)) {
        New-Item -ItemType Directory -Force -Path (Join-Path $proj "vendor") | Out-Null
        & git clone --depth 1 https://github.com/JoeanAmier/TikTokDownloader $vendor
        if ($LASTEXITCODE -ne 0) { throw "Clone TikTokDownloader that bai" }
    }
    Push-Location $vendor; & uv sync --python 3.12; Pop-Location
    Ok "Backend tai Douyin xong"

    # ---------- 5. tai truoc model AI ----------
    Say "Tai truoc model AI (Whisper, Demucs, OCR, nhan giong nam/nu) ~1.5 GB"
    $warm = @'
from faster_whisper import WhisperModel; WhisperModel("small", device="cpu", compute_type="int8"); print("whisper ok")
from demucs.pretrained import get_model; get_model("htdemucs"); print("demucs ok")
from rapidocr import RapidOCR; RapidOCR(); print("ocr ok")
from dytool.gender import _model; _model(); print("gender ok")
'@
    $warm | & uv run python -
    if ($LASTEXITCODE -ne 0) { Warn "Mot so model chua tai xong - se tu tai khi chay lan dau" } else { Ok "Model AI xong" }

    # ---------- 6. config.yaml ----------
    Say "Tao file cau hinh config.yaml"
    $cfg = Join-Path $proj "config.yaml"
    if (-not (Test-Path $cfg)) { Copy-Item (Join-Path $proj "config.example.yaml") $cfg; Ok "Da tao config.yaml" }
    else { Ok "config.yaml da co (giu nguyen)" }
    $text = [IO.File]::ReadAllText($cfg)
    if ($text -match 'anthropic_api_key:\s*""') {
        $key = Read-Host "   Nhap Anthropic API key (sk-ant-..., de trong neu chua co)"
        if ($key) {
            $text = $text -replace 'anthropic_api_key:\s*""', ('anthropic_api_key: "' + $key.Trim() + '"')
            [IO.File]::WriteAllText($cfg, $text, (New-Object Text.UTF8Encoding $false))
            Ok "Da luu API key vao config.yaml"
        } else { Warn "Chua co API key: dien sau vao config.yaml (edit > subtitles > anthropic_api_key)" }
    } else { Ok "API key da co" }

    # ---------- 7. VoiceStudio ----------
    Say "VoiceStudio (long tieng Viet)"
    $vs = @("$env:LOCALAPPDATA\Programs\VoiceStudio", "$env:ProgramFiles\VoiceStudio") | Where-Object { Test-Path $_ }
    if ($vs) { Ok "VoiceStudio da cai" }
    elseif (Ask "   Cai VoiceStudio bang trinh cai chinh thuc (voicestudio.sh)?") {
        Invoke-RestMethod https://voicestudio.sh/install.ps1 | Invoke-Expression
        Warn "Mo VoiceStudio > Model Catalogue > chap nhan license OmniVoice + higgs-audio-v2-tokenizer"
    } else { Warn "Bo qua. Tai tai https://github.com/debpalash/VoiceStudio/releases/latest" }

    # ---------- 8. shortcut ----------
    Say "Tao shortcut"
    $run = Join-Path $proj "scripts\run_ui.bat"
    $sh = New-Object -ComObject WScript.Shell
    $lnk = $sh.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "dytool.lnk"))
    $lnk.TargetPath = $run; $lnk.WorkingDirectory = $proj; $lnk.Save()
    Ok "Shortcut 'dytool' tren Desktop"
    if (Ask "   Tu mo tool moi khi dang nhap Windows?") {
        $s2 = $sh.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Startup")) "dytool.lnk"))
        $s2.TargetPath = $run; $s2.WorkingDirectory = $proj; $s2.WindowStyle = 7; $s2.Save()
        Ok "Da them vao Startup"
    }

    # ---------- 9. dang nhap + chay ----------
    if (Ask "   Dang nhap Douyin ngay bay gio (mo trinh duyet, quet QR)?") {
        & uv run dytool douyin-login
    }
    Say "CAI DAT XONG" "Green"
    Write-Host "   - Mo tool: bam dup shortcut 'dytool' tren Desktop (hoac scripts\run_ui.bat)"
    Write-Host "   - Trong giao dien: Tai khoan & Page > them tai khoan > Dang nhap Facebook > bat Page"
    Write-Host "   - Huong dan / ghi chu cho Claude: README.md, CLAUDE.md"
    if (Ask "   Mo tool ngay?") { Start-Process -FilePath $run -WorkingDirectory $proj }
}
catch {
    Write-Host ""
    Write-Host "LOI: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Chay lai file nay sau khi sua loi - cac buoc da xong se duoc bo qua." -ForegroundColor Yellow
}
