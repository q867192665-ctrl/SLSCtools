; ChinanTool Inno Setup 安装脚本
; 使用前请先运行 build.bat 生成 exe 文件

#define AppName "ChinanTool"
#define AppVersion "1.0.0"
#define AppPublisher "ChinanTool"
#define AppExeName "ChinanTool.exe"
#define BackendExeName "chinantool_backend.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=ChinanTool_Setup_v{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupIconFile=logo.ico
UninstallDisplayIcon={app}\{#AppExeName}
PrivilegesRequired=admin
; 允许在已安装时覆盖安装
UsePreviousAppDir=yes

[Languages]
Name: "chinese"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"

[Files]
; 后端主程序
Source: "dist\setup_files\{#BackendExeName}"; DestDir: "{app}"; Flags: ignoreversion
; 启动器（快捷方式指向此文件）
Source: "dist\setup_files\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; 配置文件
Source: "dist\setup_files\config.yaml"; DestDir: "{app}"; Flags: ignoreversion
; 图标
Source: "dist\setup_files\logo.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\setup_files\Logo.jpg"; DestDir: "{app}"; Flags: ignoreversion
; 前端静态文件
Source: "dist\setup_files\static\*"; DestDir: "{app}\static"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\logo.ico"; WorkingDir: "{app}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\logo.ico"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; 安装完成后启动程序
Filename: "{app}\{#AppExeName}"; Description: "立即启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时删除临时文件
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\*.log"

[UninstallRun]
; 卸载前关闭后端进程
Filename: "taskkill"; Parameters: "/f /im {#BackendExeName}"; Flags: runhidden
Filename: "taskkill"; Parameters: "/f /im {#AppExeName}"; Flags: runhidden

[Code]
function InitializeUninstall: Boolean;
begin
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataPath: string;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    // 删除安装目录中的临时文件
    DataPath := ExpandConstant('{app}');
    if DirExists(DataPath) then
    begin
      DelTree(DataPath + '\__pycache__', True, True, True);
    end;
    // 删除用户数据目录中的日志文件
    DataPath := ExpandConstant('{localappdata}\ChinanTool');
    if DirExists(DataPath) then
    begin
      DelTree(DataPath, True, True, True);
    end;
  end;
end;
