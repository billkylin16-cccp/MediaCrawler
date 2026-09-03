#ifndef AppVersion
  #define AppVersion "0.2.1-beta.1"
#endif
#ifndef SourceDir
  #error SourceDir must be supplied by build_release.ps1
#endif
#ifndef OutputDir
  #error OutputDir must be supplied by build_release.ps1
#endif

[Setup]
AppId={{6EBE5D59-41E2-47B4-84A3-6344AC7331E4}
AppName=抖音舆情监测
AppVersion={#AppVersion}
AppPublisher=MediaCrawler learning fork
AppPublisherURL=https://github.com/billkylin16-cccp/MediaCrawler
AppSupportURL=https://github.com/billkylin16-cccp/MediaCrawler/issues
DefaultDirName={localappdata}\DouyinOpinionMonitor
DefaultGroupName=抖音舆情监测
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#OutputDir}
OutputBaseFilename=DouyinOpinionMonitor-{#AppVersion}-win-x64-setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\..\LICENSE
UninstallDisplayIcon={app}\DouyinOpinionMonitor.exe
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "third_party\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: checkedonce

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\抖音舆情监测"; Filename: "{app}\DouyinOpinionMonitor.exe"
Name: "{autodesktop}\抖音舆情监测"; Filename: "{app}\DouyinOpinionMonitor.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\DouyinOpinionMonitor.exe"; Description: "启动抖音舆情监测"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
