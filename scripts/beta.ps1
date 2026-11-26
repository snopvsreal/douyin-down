# Tao / cap nhat APP BETA (ban thu nghiem) chay song song voi app chinh - khong dung toi C:\dytool lan nhanh main.
#   powershell -ExecutionPolicy Bypass -File scripts\beta.ps1                 # tao lan dau / cap nhat code
#   powershell -ExecutionPolicy Bypass -File scripts\beta.ps1 -FreshData      # chep lai du lieu tu app chinh
#
# - Code: git worktree (checkout rieng, khong gan nhanh) cua nhanh -Branch o -Target (mac dinh C:\dytool-beta).
# - Thu vien: junction .venv / vendor / tools -> app chinh (khong tai lai Python, FFmpeg, backend tai Douyin).
# - instance.json: cong 8001, khoa rieng -> mo cung luc voi app chinh; khong tu cap nhat.
# - Du lieu: BAN SAO (kenh, cai dat, video da tai / da edit, giong); KHONG chep phien dang nhap
#   (data\accounts, douyin_profile, ho so trinh duyet) -> beta khong dang bai duoc. Moi kenh trong beta bi TAT.
#   Model giong noi + nhan trinh duyet: lien ket cung (hard link, khong ton o dia).
# Giu file nay CHI ASCII + CRLF (Windows PowerShell 5.1).
param(
    [string]$Target = "C:\dytool-beta",
    [string]$Main = "C:\dytool",
    [string]$Branch = "exp/editor-template",
    [int]$Port = 8001,
    [switch]$FreshData
)
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot

function Step($t) { Write-Host "> $t" -ForegroundColor Cyan }

if (-not (Test-Path "$Main\.venv\Scripts\pythonw.exe")) { throw "Khong thay app chinh o $Main (can .venv da cai)" }
$running = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($running) { throw "App beta dang chay (cong $Port) - thoat app beta (khay he thong > Thoat) roi chay lai" }

# 1) code
if (-not (Test-Path "$Target\.git")) {
    Step "Tao ban checkout nhanh $Branch o $Target"
    git -C $Repo worktree add --detach $Target $Branch
    if ($LASTEXITCODE -ne 0) { throw "git worktree add that bai" }
} else {
    Step "Cap nhat code beta theo nhanh $Branch"
    git -C $Target checkout --detach $Branch
    if ($LASTEXITCODE -ne 0) { throw "git checkout that bai (co file sua tay trong $Target ?)" }
}
git -C $Target log -1 --format="  ban: %h %s"

# 2) thu vien dung chung voi app chinh
foreach ($d in @(".venv", "vendor", "tools")) {
    if ((Test-Path "$Main\$d") -and -not (Test-Path "$Target\$d")) {
        New-Item -ItemType Junction -Path "$Target\$d" -Target "$Main\$d" | Out-Null
        Write-Host "  lien ket $d -> $Main\$d"
    }
}
Set-Content -Path "$Target\instance.json" -Encoding ASCII -Value "{`"name`": `"beta`", `"port`": $Port}"

# 3) du lieu (ban sao)
$data = "$Target\data"
if ($FreshData -or -not (Test-Path "$data\app.db")) {
    Step "Chep du lieu tu app chinh (khong chep phien dang nhap)"
    if (Test-Path $data) { Remove-Item -Recurse -Force $data }
    New-Item -ItemType Directory -Force -Path $data | Out-Null
    # app.db: sao luu bang sqlite (app chinh co the dang ghi) + tat moi kenh trong ban beta
    $py = "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); " +
          "d.execute('UPDATE pages SET enabled=0'); d.commit(); print('  app.db:', d.execute('select count(*) from pages').fetchone()[0], 'kenh (da tat)')"
    & "$Main\.venv\Scripts\python.exe" -c $py "$Main\data\app.db" "$data\app.db"
    if ($LASTEXITCODE -ne 0) { throw "Chep app.db that bai" }
    if (Test-Path "$Main\config.yaml") { Copy-Item "$Main\config.yaml" "$Target\config.yaml" -Force }
    foreach ($f in @("ai.json", "vs_profiles.json", "voicestudio.json", "voice_model.json")) {
        if (Test-Path "$Main\data\$f") { Copy-Item "$Main\data\$f" "$data\$f" }
    }
    foreach ($d in @("avatars", "raw", "edited", "voice")) {
        if (Test-Path "$Main\data\$d") { Copy-Item "$Main\data\$d" "$data\$d" -Recurse }
    }
    # file lon chi doc: lien ket cung (cung o C:)
    function HardLinkTree($src, $dst) {
        if (-not (Test-Path $src)) { return }
        Get-ChildItem $src -Recurse -File | ForEach-Object {
            $rel = $_.FullName.Substring($src.Length).TrimStart("\")
            $to = Join-Path $dst $rel
            New-Item -ItemType Directory -Force -Path (Split-Path $to) | Out-Null
            New-Item -ItemType HardLink -Path $to -Target $_.FullName | Out-Null
        }
    }
    HardLinkTree "$Main\data\models" "$data\models"
    foreach ($d in @("ShardX-Windows", "fingerprints", "widevine-cdm")) { HardLinkTree "$Main\data\shardx\$d" "$data\shardx\$d" }
    foreach ($f in @("manifest.json", "widevine-cdm.checked")) {
        if (Test-Path "$Main\data\shardx\$f") { Copy-Item "$Main\data\shardx\$f" "$data\shardx\$f" }
    }
    Write-Host "  xong: $data"
} else {
    Write-Host "  giu du lieu beta cu (them -FreshData de chep lai tu app chinh)"
}

# 4) trinh khoi chay + loi tat
Copy-Item "$Main\dytool.exe" "$Target\dytool-beta.exe" -Force
$sh = New-Object -ComObject WScript.Shell
$menu = Join-Path ([Environment]::GetFolderPath("Programs")) "dytool Beta.lnk"
$desk = Join-Path ([Environment]::GetFolderPath("Desktop")) "dytool Beta.lnk"
foreach ($p in @($menu, $desk)) {
    $l = $sh.CreateShortcut($p)
    $l.TargetPath = "$Target\dytool-beta.exe"
    $l.WorkingDirectory = $Target
    $l.IconLocation = "$Target\installer\dytool.ico"
    $l.Description = "dytool Beta - ban thu nghiem"
    $l.Save()
}
Step "Xong. Mo bang loi tat 'dytool Beta' (Desktop / Start) - giao dien o http://127.0.0.1:$Port"
