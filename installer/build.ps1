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
# phien ban cua dytool.exe (Properties > Details) lay theo pyproject.toml, khong ghi cung trong launcher.cs
$v4 = (($ver -split '[^0-9]+' | Where-Object { $_ -ne '' } | Select-Object -First 3) + @('0', '0', '0', '0'))[0..3] -join '.'
Set-Content -Path build\version.cs -Encoding ASCII -Value @(
    "[assembly: System.Reflection.AssemblyVersion(`"$v4`")]",
    "[assembly: System.Reflection.AssemblyFileVersion(`"$v4`")]",
    "[assembly: System.Reflection.AssemblyInformationalVersion(`"$ver`")]")
& $csc /nologo /codepage:65001 /target:winexe /optimize+ /win32icon:installer\dytool.ico `
    /reference:System.Windows.Forms.dll /out:build\dytool.exe installer\launcher.cs build\version.cs
if ($LASTEXITCODE -ne 0) { throw "Build dytool.exe that bai" }
Write-Host "[OK] build\dytool.exe"

# 2) cua so cai dat (installer\setup_ui.cs): font Be Vietnam Pro nhung vao exe
$fontRes = Get-ChildItem installer\fonts\*.ttf | ForEach-Object { "/resource:$($_.FullName),$($_.Name)" }
$uiArgs = @("/nologo", "/codepage:65001", "/target:winexe", "/optimize+", "/win32icon:installer\dytool.ico",
    "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll") + $fontRes
#    a) ban khong nhung trinh cai loi -> {app}\dytool-setup.exe ("Cai dat lai thu vien", /repair)
& $csc @uiArgs /out:build\dytool-setup.exe installer\setup_ui.cs build\version.cs
if ($LASTEXITCODE -ne 0) { throw "Build dytool-setup.exe that bai" }
Write-Host "[OK] build\dytool-setup.exe"

# 3) trinh cai loi Inno (chay ngam duoi cua so cai): build\dytool-core-<ver>.exe
$iscc = $env:ISCC
if (-not $iscc) { $iscc = "C:\Tools\InnoSetup\ISCC.exe" }
if (-not (Test-Path $iscc)) { throw "Khong thay Inno Setup (ISCC.exe). Cai tu https://github.com/jrsoftware/issrc/releases" }
& $iscc /Q "/DAppVer=$ver" installer\dytool.iss
if ($LASTEXITCODE -ne 0) { throw "Build trinh cai loi that bai" }
Write-Host "[OK] build\dytool-core-$ver.exe"

#    b) file cai phat hanh = cua so cai + trinh cai loi nhung ben trong (resource "core.exe")
New-Item -ItemType Directory -Force -Path dist | Out-Null
& $csc @uiArgs "/resource:build\dytool-core-$ver.exe,core.exe" "/out:dist\dytool-Setup-$ver.exe" installer\setup_ui.cs build\version.cs
if ($LASTEXITCODE -ne 0) { throw "Build dytool-Setup.exe that bai" }
Get-Item "dist\dytool-Setup-$ver.exe" | Select-Object Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } }
