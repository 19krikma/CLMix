; Inno Setup script for DigicoMonitorMix.
; Produces a double-click installer: installs to Program Files, adds a
; Start Menu entry, offers an optional Desktop shortcut, and registers a
; normal Windows uninstaller.
;
; Requires dist\DigicoMonitorMix\ to already exist (built by PyInstaller -
; see build.ps1, which runs both steps in order).
;
; Build with: iscc /DMyAppVersion=1.0.0 installer.iss
; (build.ps1 passes the version automatically, read from version.py)

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#define MyAppName "Digico Monitor Mix"
#define MyAppPublisher "DigicoMonitorMix"
#define MyAppExeName "DigicoMonitorMix.exe"

[Setup]
AppId={{DB1ABE2F-474D-46B3-B853-D5AD0228CC7A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=dist_installer
OutputBaseFilename=DigicoMonitorMixSetup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\..\dist\DigicoMonitorMix\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
