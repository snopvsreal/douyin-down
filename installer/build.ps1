# Build dytool.exe (launcher C#) + dytool-Setup-<version>.exe (Inno Setup).
# Chay: powershell -ExecutionPolicy Bypass -File installer\build.ps1
# Can: Inno Setup 7 (ISCC.exe) - mac dinh C:\Tools\InnoSetup, hoac dat bien ISCC.
# Giu file nay CHI ASCII + CRLF (Windows PowerShell 5.1).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$ver = (Select-String -Path pyproject.toml -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
Write-Host "dytool $ver"

# 1) launcher dytool.exe: csc.exe co san trong .NET Framework 4 cua Windows
$csc = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
New-Item -ItemType Directory -Force -Path build | Out-Null
& $csc /nologo /codepage:65001 /target:winexe /optimize+ /win32icon:installer\dytool.ico `
    /reference:System.Windows.Forms.dll /out:build\dytool.exe installer\launcher.cs
if ($LASTEXITCODE -ne 0) { throw "Build dytool.exe that bai" }
Write-Host "[OK] build\dytool.exe"

# 2) file cai
$iscc = $env:ISCC
if (-not $iscc) { $iscc = "C:\Tools\InnoSetup\ISCC.exe" }
if (-not (Test-Path $iscc)) { throw "Khong thay Inno Setup (ISCC.exe). Cai tu https://github.com/jrsoftware/issrc/releases" }
& $iscc /Q "/DAppVer=$ver" installer\dytool.iss
if ($LASTEXITCODE -ne 0) { throw "Build file cai that bai" }
Get-Item "dist\dytool-Setup-$ver.exe" | Select-Object Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } }
