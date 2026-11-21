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
; Giao diện kiểu Windows 11, tự theo chế độ sáng / tối của Windows. Hình: scripts\make_wizard_images.py
WizardStyle=modern dynamic windows11 includetitlebar
WizardImageFile=installer\wiz-side-light-1x.png,installer\wiz-side-light-2x.png
WizardImageFileDynamicDark=installer\wiz-side-dark-1x.png,installer\wiz-side-dark-2x.png
WizardSmallImageFile=installer\wiz-small-1x.png,installer\wiz-small-2x.png
WizardSmallImageFileDynamicDark=installer\wiz-small-1x.png,installer\wiz-small-2x.png
DisableWelcomePage=no
DisableDirPage=auto
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "vi"; MessagesFile: "installer\Vietnamese.isl"

[CustomMessages]
vi.DesktopIcon=Tạo biểu tượng dytool trên Desktop
vi.Shortcuts=Biểu tượng:
vi.Extras=Thành phần đi kèm (tải từ trang chính thức):
vi.TaskShard=ShardBrowser — trình duyệt dytool dùng để đăng nhập và đăng bài (cần có)
vi.TaskVoice=VoiceStudio — app lồng tiếng Việt (cần có để lồng tiếng)
vi.OpenApp=Mở dytool
vi.Reinstall=dytool - Cài đặt lại thư viện
vi.LibTitle=Đang cài thư viện
vi.LibDesc=Tải Python, AI, FFmpeg… (~4 GB). Lần đầu mất 15–30 phút và cần mạng — đừng tắt máy.
vi.LibStart=Đang chuẩn bị…
vi.LibStep=Bước %1/7 · %2
vi.Step1=Công cụ quản lý Python (uv)
vi.Step2=Python và thư viện AI (~3 GB, lâu nhất)
vi.Step3=FFmpeg (xử lý video)
vi.Step4=Trình duyệt ShardBrowser
vi.Step5=Bộ tải video Douyin
vi.Step6=Cấu hình và model AI (~1.5 GB)
vi.Step7=VoiceStudio (lồng tiếng Việt)
vi.Closing=Đang đóng dytool đang chạy…
vi.LibDone=Đã cài xong thư viện
vi.LibFail=Cài thư viện chưa xong (mã lỗi %1).%n%nKiểm tra mạng rồi chạy lại: Start Menu → dytool - Cài đặt lại thư viện.%nNhật ký: %2

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; GroupDescription: "{cm:Shortcuts}"
Name: "shardbrowser"; Description: "{cm:TaskShard}"; GroupDescription: "{cm:Extras}"; Check: ShardMissing
Name: "voicestudio"; Description: "{cm:TaskVoice}"; GroupDescription: "{cm:Extras}"; Check: VoiceMissing

[Files]
Source: "build\dytool.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dytool\*"; DestDir: "{app}\dytool"; Excludes: "__pycache__,*.pyc"; Flags: recursesubdirs ignoreversion
Source: "scripts\*"; DestDir: "{app}\scripts"; Excludes: "__pycache__,*.pyc"; Flags: recursesubdirs ignoreversion
Source: "installer\dytool.ico"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "pyproject.toml"; DestDir: "{app}"; Flags: ignoreversion
Source: "uv.lock"; DestDir: "{app}"; Flags: ignoreversion
Source: "config.example.yaml"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{app}\data"

[Icons]
Name: "{autoprograms}\dytool"; Filename: "{app}\dytool.exe"; WorkingDir: "{app}"
Name: "{autoprograms}\{cm:Reinstall}"; Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\setup_app.ps1"""; \
  WorkingDir: "{app}"; IconFilename: "{app}\installer\dytool.ico"
Name: "{autodesktop}\dytool"; Filename: "{app}\dytool.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; Bước cài thư viện chạy ẩn trong CurStepChanged (dưới), tiến độ hiện ngay trên thanh của trình cài
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
const
  STEPS = 7;
  PER_STEP = 100;

var
  LibStep: Integer;

function ShardMissing: Boolean;
begin
  Result := not FileExists(ExpandConstant('{localappdata}\ShardBrowser Launcher\shardbrowser-launcher.exe'));
end;

function VoiceMissing: Boolean;
begin
  Result := not (FileExists(ExpandConstant('{localappdata}\Programs\VoiceStudio\VoiceStudio.exe')) or
                 FileExists(ExpandConstant('{commonpf64}\VoiceStudio\VoiceStudio.exe')));
end;

// Mỗi dòng setup_app.ps1 in ra: "▸ N/7  Tên bước" -> nhảy thanh tiến độ + tiêu đề; dòng khác -> dòng chi tiết
procedure LibOnLog(const S: String; const Error, FirstLine: Boolean);
var
  T, Rest: String;
  N, P: Integer;
begin
  T := Trim(S);
  if T = '' then Exit;
  Log(T);
  if Copy(T, 1, 1) = '▸' then begin
    Rest := Trim(Copy(T, 2, Length(T)));
    P := Pos('/', Rest);
    N := StrToIntDef(Copy(Rest, 1, P - 1), LibStep);
    if (N >= 1) and (N <= STEPS) then begin
      LibStep := N;
      // tên bước lấy từ [CustomMessages] (không lấy chữ trong log: ký tự UTF-8 có thể bị cắt giữa 2 lần đọc)
      WizardForm.StatusLabel.Caption := FmtMessage(CustomMessage('LibStep'), [IntToStr(N), CustomMessage('Step' + IntToStr(N))]);
      WizardForm.FilenameLabel.Caption := '';
      WizardForm.ProgressGauge.Position := (N - 1) * PER_STEP;
    end;
  end else begin
    // bỏ dấu đầu dòng của script (√ ! ×) cho gọn
    if (Copy(T, 1, 1) = '√') or (Copy(T, 1, 1) = '!') or (Copy(T, 1, 1) = '×') then T := Trim(Copy(T, 2, Length(T)));
    WizardForm.FilenameLabel.Caption := T;
    // nhích nhẹ trong bước để thấy vẫn đang chạy (không vượt sang bước sau)
    if (LibStep > 0) and (WizardForm.ProgressGauge.Position < LibStep * PER_STEP - 10) then
      WizardForm.ProgressGauge.Position := WizardForm.ProgressGauge.Position + 1;
  end;
end;

procedure RunLibSetup;
var
  Params: String;
  RC: Integer;
  Ok: Boolean;
begin
  LibStep := 0;
  WizardForm.PageNameLabel.Caption := CustomMessage('LibTitle');
  WizardForm.PageDescriptionLabel.Caption := CustomMessage('LibDesc');
  WizardForm.StatusLabel.Caption := CustomMessage('LibStart');
  WizardForm.FilenameLabel.Caption := '';
  WizardForm.ProgressGauge.Min := 0;
  WizardForm.ProgressGauge.Max := STEPS * PER_STEP;
  WizardForm.ProgressGauge.Position := 0;
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\scripts\setup_app.ps1') + '" -Wizard';
  // cài im lặng (/VERYSILENT): không tự cài phần mềm bên thứ ba
  if (not WizardSilent) and WizardIsTaskSelected('shardbrowser') then Params := Params + ' -WithShardBrowser';
  if (not WizardSilent) and WizardIsTaskSelected('voicestudio') then Params := Params + ' -WithVoiceStudio';
  try
    Ok := ExecAndLogOutput(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params,
                           ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, RC, @LibOnLog);
  except
    Ok := False;
    RC := -1;
    Log(GetExceptionMessage);
  end;
  if Ok and (RC = 0) then begin
    WizardForm.ProgressGauge.Position := WizardForm.ProgressGauge.Max;
    WizardForm.StatusLabel.Caption := CustomMessage('LibDone');
    WizardForm.FilenameLabel.Caption := '';
  end else if not WizardSilent then
    MsgBox(FmtMessage(CustomMessage('LibFail'), [IntToStr(RC), ExpandConstant('{app}\data\setup.log')]), mbError, MB_OK);
end;

// dytool đang mở giữ file (icon, .venv\pythonw.exe) -> chép đè / uv sync lỗi. Đóng nó trước; cài xong có ô "Mở dytool".
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  RC: Integer;
begin
  Result := '';
  WizardForm.PreparingLabel.Caption := CustomMessage('Closing');
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
       '-NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like ''*dytool.desktop*'' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"',
       '', SW_HIDE, ewWaitUntilTerminated, RC);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then RunLibSetup;
end;
