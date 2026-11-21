; File cài dytool cho Windows (Inno Setup 7). Build: powershell -File installer\build.ps1
; Chỉ chép mã nguồn + dytool.exe (~1 MB); thư viện (~4 GB) tải ở bước scripts\setup_app.ps1 lúc cài.

#ifndef AppVer
  #define AppVer "0.3.0"
#endif

[Setup]
AppId={{FDB72689-8F89-4575-A7EA-90412B92121C}
AppName=dytool
AppVersion={#AppVer}
AppVerName=dytool {#AppVer}
AppPublisher=dytool
AppComments=Douyin → edit → lồng tiếng Việt → Facebook / TikTok
SourceDir=..
OutputDir=dist
OutputBaseFilename=dytool-Setup-{#AppVer}
SetupIconFile=installer\dytool.ico
UninstallDisplayIcon={app}\dytool.exe
UninstallDisplayName=dytool
; Ổ C:, không cần quyền admin. Không dùng Program Files (cần admin, không ghi được data) hay AppData
; (một số máy Windows chặn chạy exe trong AppData).
DefaultDirName=C:\dytool
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "vi"; MessagesFile: "installer\Vietnamese.isl"

[CustomMessages]
vi.DesktopIcon=Tạo biểu tượng dytool trên Desktop
vi.SetupLibs=Đang tải và cài thư viện (lần đầu 15–30 phút, cần mạng). Xem tiến độ ở cửa sổ màu xanh…
vi.OpenApp=Mở dytool
vi.Reinstall=dytool - Cài đặt lại thư viện

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"

[Files]
Source: "build\dytool.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dytool\*"; DestDir: "{app}\dytool"; Excludes: "__pycache__,*.pyc"; Flags: recursesubdirs ignoreversion
Source: "scripts\*"; DestDir: "{app}\scripts"; Excludes: "__pycache__,*.pyc"; Flags: recursesubdirs ignoreversion
Source: "installer\dytool.ico"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "pyproject.toml"; DestDir: "{app}"; Flags: ignoreversion
Source: "uv.lock"; DestDir: "{app}"; Flags: ignoreversion
Source: "config.example.yaml"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Dirs]
Name: "{app}\data"

[Icons]
Name: "{autoprograms}\dytool"; Filename: "{app}\dytool.exe"; WorkingDir: "{app}"
Name: "{autoprograms}\{cm:Reinstall}"; Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\setup_app.ps1"""; \
  WorkingDir: "{app}"; IconFilename: "{app}\installer\dytool.ico"
Name: "{autodesktop}\dytool"; Filename: "{app}\dytool.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\setup_app.ps1"" {code:SetupArgs}"; \
  WorkingDir: "{app}"; StatusMsg: "{cm:SetupLibs}"; Flags: waituntilterminated
Filename: "{app}\dytool.exe"; Description: "{cm:OpenApp}"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; Xoá phần tải về lúc cài (vài GB). GIỮ data\ và config.yaml (tài khoản, cookie, video) — xoá tay nếu muốn.
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\.python"
Type: filesandordirs; Name: "{app}\vendor"
Type: filesandordirs; Name: "{app}\ms-playwright"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\dytool"
Type: filesandordirs; Name: "{app}\scripts"

[Code]
// Cài im lặng (/VERYSILENT) thì script cài thư viện không được hỏi gì (không có ai bấm Enter)
function SetupArgs(Param: String): String;
begin
  if WizardSilent then Result := '-NoPrompt' else Result := '';
end;
