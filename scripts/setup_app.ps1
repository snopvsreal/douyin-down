# dytool - bước cài thư viện sau khi chép file (file cài dytool-Setup.exe gọi script này).
# Chạy lại bất cứ lúc nào để sửa / cập nhật: Start Menu > dytool > "Cài đặt lại thư viện".
# File lưu UTF-8 CÓ BOM + CRLF: Windows PowerShell 5.1 chỉ đọc đúng chữ có dấu khi có BOM.
#
# Mọi thứ nằm trong thư mục cài (ổ C:): Python (.python), thư viện (.venv), nhân trình duyệt (data\shardx),
# FFmpeg (tools\ffmpeg), backend tải Douyin (vendor). Không dùng AppData vì có máy Windows chặn chạy exe ở đó.
param(
    [switch]$Launch,     # cài xong thì mở dytool
    [switch]$NoPrompt,   # không hỏi gì (thiếu VoiceStudio thì chỉ nhắc, trừ khi có cờ -WithVoiceStudio)
    [switch]$Wizard,     # chạy ẩn trong trình cài: tiến độ đọc từ các dòng "▸ N/7", không hỏi gì
    [switch]$WithShardBrowser,   # bỏ (trình duyệt đã nhúng sẵn) — giữ để trình cài cũ truyền vào không lỗi
    [switch]$WithVoiceStudio
)
if ($Wizard) { $NoPrompt = $true }
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# ---------- cửa sổ hiển thị đúng tiếng Việt ----------
# Cửa sổ PowerShell mặc định dùng bảng mã 437 (chữ có dấu từ Python / uv thành ký tự lạ) và font Lucida Console
# (thiếu chữ ạ ả ế ộ…). Chỉ đổi cho CỬA SỔ NÀY: UTF-8 + font Consolas. Không đụng cài đặt chung của máy.
try { chcp 65001 > $null } catch {}
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8
try {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class DyConsoleFont {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct FontInfo {
        public uint cbSize; public uint nFont; public short X; public short Y;
        public int FontFamily; public int FontWeight;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string FaceName;
    }
    [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr GetStdHandle(int n);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool SetCurrentConsoleFontEx(IntPtr h, bool max, ref FontInfo f);
    public static bool Use(string face, short size) {
        FontInfo f = new FontInfo();
        f.cbSize = (uint)Marshal.SizeOf(f); f.FaceName = face; f.Y = size; f.FontFamily = 54; f.FontWeight = 400;
        return SetCurrentConsoleFontEx(GetStdHandle(-11), false, ref f);
    }
}
"@ -ErrorAction Stop
    [DyConsoleFont]::Use("Consolas", 16) | Out-Null
} catch {}

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
New-Item -ItemType Directory -Force -Path "$Root\data" | Out-Null
try { Start-Transcript -Path "$Root\data\setup.log" -Append | Out-Null } catch {}
try { $Host.UI.RawUI.WindowTitle = "dytool – cài đặt thư viện" } catch {}

function Say($m) { Write-Host ""; Write-Host "▸ $m" -ForegroundColor Cyan }
function Ok($m) { Write-Host "   √ $m" -ForegroundColor Green }
function Warn($m) { Write-Host "   ! $m" -ForegroundColor Yellow }
function Done-Or-Fail($what) { if ($LASTEXITCODE -ne 0) { throw "$what thất bại (mã lỗi $LASTEXITCODE)" } }

$env:UV_PYTHON_INSTALL_DIR = "$Root\.python"
$env:UV_PYTHON_INSTALL_BIN = "0"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:HF_HUB_VERBOSITY = "error"           # ẩn cảnh báo tiếng Anh "unauthenticated requests to the HF Hub" khi tải model
$env:HF_HUB_DISABLE_PROGRESS_BARS = "1"
function Refresh-Path {
    $env:Path = "$env:USERPROFILE\.local\bin;$Root\tools\ffmpeg\bin;" +
        [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
Refresh-Path

Write-Host ""
Write-Host "  dytool – cài đặt thư viện" -ForegroundColor White
Write-Host "  ──────────────────────────────────────────────" -ForegroundColor DarkCyan
Write-Host "  Lần đầu mất 15–30 phút và cần mạng. Đừng đóng cửa sổ này."
Write-Host "  Thư mục: $Root" -ForegroundColor DarkGray

# Tải 1 file trong bản phát hành GitHub và đối chiếu SHA256 GitHub công bố (asset.digest). Sai mã -> xoá, dừng.
function Get-GitHubAsset($apiUrl, $pattern, $outDir) {
    $rel = Invoke-RestMethod $apiUrl -Headers @{ "User-Agent" = "dytool-setup" }
    $asset = $rel.assets | Where-Object { $_.name -match $pattern } | Select-Object -First 1
    if (-not $asset) { throw "Không thấy file '$pattern' trong bản phát hành $($rel.tag_name)" }
    $out = Join-Path $outDir $asset.name
    Write-Host "   Đang tải $($asset.name) ($([math]::Round($asset.size / 1MB, 1)) MB, bản $($rel.tag_name))…"
    Invoke-WebRequest $asset.browser_download_url -OutFile $out -UseBasicParsing
    if ($asset.digest -match '^sha256:([0-9a-f]{64})$') {
        if ((Get-FileHash $out -Algorithm SHA256).Hash -ne $Matches[1].ToUpper()) {
            Remove-Item $out -Force -ErrorAction SilentlyContinue
            throw "File $($asset.name) tải về không khớp mã SHA256 GitHub công bố — đã xoá, không cài"
        }
        Ok "Đã kiểm tra mã SHA256"
    } else { Warn "Bản phát hành không có mã SHA256 để đối chiếu" }
    return $out
}

# Chạy "Cài đặt lại thư viện" khi dytool đang mở: đóng hết tiến trình chạy từ thư mục này (app, backend tải, ffmpeg)
# để uv sync không bị "Access is denied". Trong trình cài thì trình cài đã đóng rồi.
if (-not $Wizard) {
    $dirs = @("\.venv\", "\.python\", "\vendor\", "\tools\")
    $run = Get-CimInstance Win32_Process | Where-Object { $p = $_; $p.ProcessId -ne $PID -and $p.ExecutablePath -and
        ($dirs | Where-Object { $p.ExecutablePath -like ($Root + $_ + "*") }) }
    if ($run) {
        Warn "Đang đóng dytool đang chạy để cài thư viện…"
        $run | ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>$null | Out-Null }
        Start-Sleep -Seconds 2
    }
}

try {
    # ---------- 1. uv (quản lý Python) ----------
    Say "1/7  uv (quản lý Python)"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        Refresh-Path
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "Không cài được uv" }
    }
    Ok (& uv --version)

    # ---------- 2. Python + thư viện ----------
    Say "2/7  Python 3.12 + thư viện (torch, whisper, demucs… ~3 GB, lâu nhất)"
    # Card NVIDIA (CUDA, kiến trúc >= 5.0) -> torch bản GPU (pyproject: extra "gpu" = torch 2.14 cu126, bản cuối còn
    # chạy card GTX 10xx). Tách giọng Demucs nhanh ~3 lần. Chọn tay: biến môi trường DYTOOL_TORCH=cpu | gpu
    $variant = "cpu"
    $gpuName = ""
    # Trình cài có thể chạy script này bằng PowerShell 32-bit: khi đó System32 bị chuyển hướng sang SysWOW64 (không có
    # nvidia-smi) -> tìm cả qua Sysnative / thư mục NVIDIA. Môi trường đang có torch GPU thì giữ GPU (không tự hạ CPU).
    $smi = @("$env:windir\Sysnative\nvidia-smi.exe", "$env:windir\System32\nvidia-smi.exe",
             "$env:ProgramFiles\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
             (Get-Command nvidia-smi -ErrorAction SilentlyContinue).Source) |
        Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    $hadGpu = [bool](Get-ChildItem "$Root\.venv\Lib\site-packages" -Filter "torch-*+cu*.dist-info" -ErrorAction SilentlyContinue)
    if ($env:DYTOOL_TORCH -in @("cpu", "gpu")) { $variant = $env:DYTOOL_TORCH }
    elseif ($smi) {
        $q = & $smi --query-gpu=name,compute_cap --format=csv,noheader 2>$null | Select-Object -First 1
        if ($q -match '^\s*(.+?),\s*(\d+\.\d+)\s*$' -and [double]$Matches[2] -ge 5.0) { $variant = "gpu"; $gpuName = $Matches[1] }
    }
    if ($variant -eq "cpu" -and $hadGpu -and -not $env:DYTOOL_TORCH) {
        $variant = "gpu"; $gpuName = "(đang dùng bản GPU)"
    }
    if ($variant -eq "gpu") { Write-Host "   Có card NVIDIA $gpuName -> cài bản tăng tốc GPU (tải thêm ~2.5 GB)" }
    & uv sync --frozen --python 3.12 --extra $variant 2>&1 | ForEach-Object { "   $_" } | Out-Host     # in vào setup.log (Start-Transcript không bắt được chữ của lệnh ngoài)
    if ($LASTEXITCODE -ne 0 -and $variant -eq "gpu") {
        Warn "Cài bản GPU chưa được (mã $LASTEXITCODE) — dùng bản CPU"
        $variant = "cpu"
        & uv sync --frozen --python 3.12 --extra cpu 2>&1 | ForEach-Object { "   $_" } | Out-Host
    }
    Done-Or-Fail "Cài thư viện Python"
    Ok ("Thư viện Python xong (" + $(if ($variant -eq "gpu") { "AI chạy bằng GPU" } else { "AI chạy bằng CPU" }) + ")")

    # ---------- 3. FFmpeg bản đầy đủ (có bộ lọc phụ đề) ----------
    Say "3/7  FFmpeg"
    $ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
    $hasSubs = $false
    if ($ff) { $hasSubs = ((& ffmpeg -hide_banner -filters 2>&1 | Out-String) -match " subtitles ") }
    if (-not $hasSubs) {
        Write-Host "   Đang tải FFmpeg (~200 MB)…"
        $zip = Get-GitHubAsset "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/tags/latest" `
            '^ffmpeg-master-latest-win64-gpl\.zip$' $env:TEMP
        $tmp = "$Root\tools\ffmpeg-tmp"
        if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp -ErrorAction Stop }
        Expand-Archive $zip -DestinationPath $tmp -Force -ErrorAction Stop
        Remove-Item $zip -ErrorAction SilentlyContinue
        $bin = (Get-ChildItem $tmp -Recurse -Filter ffmpeg.exe | Select-Object -First 1).DirectoryName
        if (-not $bin) { throw "Gói FFmpeg tải về không có ffmpeg.exe" }
        if (Test-Path "$Root\tools\ffmpeg") { Remove-Item -Recurse -Force "$Root\tools\ffmpeg" -ErrorAction Stop }
        Move-Item (Split-Path -Parent $bin) "$Root\tools\ffmpeg" -ErrorAction Stop
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        Refresh-Path
    }
    Ok ((& ffmpeg -hide_banner -version | Select-Object -First 1) -replace "Copyright.*", "")

    # ---------- 4. Trình duyệt (nhân ShardX nhúng qua SDK shardx, không cần app ShardBrowser) ----------
    Say "4/7  Trình duyệt (nhân ShardX ~200 MB)"
    # Tải sẵn vào data\shardx để lần đầu bấm Đăng nhập không phải chờ. Lỗi (mạng…) chỉ cảnh báo: dytool tự tải lại
    # khi mở app
    & uv run --no-sync python -c "from dytool import browsers; browsers.install_engine(); print('engine ok')" 2>&1 |
        ForEach-Object { "   $_" } | Out-Host
    if ($LASTEXITCODE -eq 0) { Ok "Nhân trình duyệt sẵn sàng (data\shardx)" }
    else { Warn "Chưa tải được nhân trình duyệt — dytool sẽ tự tải lại khi mở app" }

    # ---------- 5. Backend tải Douyin (TikTokDownloader) ----------
    Say "5/7  Backend tải Douyin (TikTokDownloader)"
    $vendor = "$Root\vendor\TikTokDownloader"
    if (-not (Test-Path "$vendor\main.py")) {
        New-Item -ItemType Directory -Force -Path "$Root\vendor" | Out-Null
        $zip = "$env:TEMP\dytool-tdl.zip"
        Invoke-WebRequest "https://codeload.github.com/JoeanAmier/TikTokDownloader/zip/refs/heads/master" -OutFile $zip -UseBasicParsing
        Expand-Archive $zip -DestinationPath "$Root\vendor" -Force -ErrorAction Stop
        Remove-Item $zip -ErrorAction SilentlyContinue
        if (Test-Path $vendor) { Remove-Item -Recurse -Force $vendor -ErrorAction Stop }
        Rename-Item "$Root\vendor\TikTokDownloader-master" "TikTokDownloader" -ErrorAction Stop
    }
    if (-not (Test-Path "$vendor\.venv")) {
        # uv.lock của dự án này trỏ tới mirror Trung Quốc (bị chặn 403) -> giải lại với PyPI chính thức
        if (Test-Path "$vendor\uv.lock") { Move-Item "$vendor\uv.lock" "$vendor\uv.lock.mirror-bak" -Force -ErrorAction Stop }
        Push-Location $vendor -ErrorAction Stop       # lỗi thì dừng, không chạy uv sync nhầm trong thư mục dytool
        & uv sync --python 3.12 --default-index https://pypi.org/simple 2>&1 | ForEach-Object { "   $_" } | Out-Host     # in vào setup.log (Start-Transcript không bắt được chữ của lệnh ngoài)
        $rc = $LASTEXITCODE
        Pop-Location
        if ($rc -ne 0) { throw "Cài backend tải Douyin thất bại (mã lỗi $rc)" }
    }
    Ok "Backend tải Douyin xong"

    # ---------- 6. Cấu hình + model AI ----------
    Say "6/7  Cấu hình và model AI (~1.5 GB)"
    if (-not (Test-Path "$Root\config.yaml")) { Copy-Item "$Root\config.example.yaml" "$Root\config.yaml"; Ok "Đã tạo config.yaml" }
    # --no-sync: uv run mặc định đồng bộ lại môi trường về bản không tuỳ chọn -> gỡ mất torch GPU vừa cài
    & uv run --no-sync python "$Root\scripts\warm_models.py" 2>&1 | ForEach-Object { "   $_" } | Out-Host     # in vào setup.log (Start-Transcript không bắt được chữ của lệnh ngoài)
    if ($LASTEXITCODE -ne 0) { Warn "Một số model chưa tải xong — tool tự tải khi dùng lần đầu" } else { Ok "Model AI xong" }

    # ---------- 7. VoiceStudio (lồng tiếng) ----------
    Say "7/7  VoiceStudio (app lồng tiếng Việt)"
    $vs = @("$env:LOCALAPPDATA\Programs\VoiceStudio\VoiceStudio.exe", "$env:ProgramFiles\VoiceStudio\VoiceStudio.exe") | Where-Object { Test-Path $_ }
    if ($vs) { Ok "VoiceStudio đã cài" }
    elseif ($NoPrompt -and -not $WithVoiceStudio) { Warn "Chưa có VoiceStudio — cài sau tại https://github.com/debpalash/VoiceStudio/releases" }
    else {
        $a = if ($WithVoiceStudio) { "C" } else { Read-Host "   Cài VoiceStudio bằng trình cài chính thức (voicestudio.sh)? (C/K)" }
        if ($a -match '^[CcYy]') {
            Write-Host "   Đang tải và cài VoiceStudio…"
            # trình cài chính thức, cờ -Silent = cài không hỏi. Chạy ở tiến trình PowerShell RIÊNG: script đó gọi `exit`
            # hay báo lỗi cũng không cắt ngang script này; lỗi chỉ cảnh báo (phần đi kèm, phần lõi đã cài xong)
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -Command `
                "& ([scriptblock]::Create((Invoke-RestMethod https://voicestudio.sh/install.ps1))) -Silent"
            if ($LASTEXITCODE -ne 0) {
                Warn "Chưa cài được VoiceStudio (mã $LASTEXITCODE) — cài sau tại https://github.com/debpalash/VoiceStudio/releases"
            } else {
                Warn "Mở VoiceStudio > Model Catalogue > chấp nhận license OmniVoice và higgs-audio-v2-tokenizer"
                Warn "Nếu VoiceStudio báo lỗi tải Python: bấm 'Thay đổi…' và chọn thư mục runtime ở ổ C:, vd C:\VoiceStudio\runtime"
            }
        } else { Warn "Bỏ qua — lồng tiếng cần VoiceStudio, cài sau tại https://github.com/debpalash/VoiceStudio/releases" }
    }

    Write-Host ""
    Write-Host "  √ CÀI ĐẶT XONG" -ForegroundColor Green
    Write-Host "    Mở dytool: biểu tượng dytool trên Desktop hoặc Start Menu." -ForegroundColor Green
    if ($Launch) { Start-Process "$Root\dytool.exe" -WorkingDirectory $Root }
    $code = 0
}
catch {
    Write-Host ""
    $errMsg = $_.Exception.Message
    Write-Host "  × LỖI: $errMsg" -ForegroundColor Red
    Write-Host "    Kiểm tra mạng rồi chạy lại: Start Menu > dytool > Cài đặt lại thư viện." -ForegroundColor Yellow
    Write-Host "    Nhật ký: $Root\data\setup.log" -ForegroundColor Yellow
    $code = 1
}
# dấu cho app biết lần cài thư viện gần nhất có xong không (cập nhật im lặng lỗi -> app hiện nút Cài lại thư viện)
$marker = "$Root\data\setup_failed.txt"
if ($code -eq 0) { Remove-Item $marker -ErrorAction SilentlyContinue }
else { try { Set-Content -Path $marker -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm')`n$errMsg" -Encoding UTF8 } catch {} }
try { Stop-Transcript | Out-Null } catch {}
if (-not $NoPrompt) { Write-Host ""; Read-Host "Nhấn Enter để đóng cửa sổ này" | Out-Null }
exit $code
