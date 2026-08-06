; Inno Setup script for CLMix.
; Produces a double-click installer: installs to Program Files, adds a
; Start Menu entry, offers an optional Desktop shortcut, and registers a
; normal Windows uninstaller.
;
; Requires dist\CLMix\ to already exist (built by PyInstaller -
; see build.ps1, which runs both steps in order).
;
; Build with: iscc /DMyAppVersion=1.0.0 installer.iss
; (build.ps1 passes the version automatically, read from version.py)

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#define MyAppName "CLMix"
#define MyAppPublisher "CLMix"
#define MyAppExeName "CLMix.exe"

[Setup]
AppId={{DC6D78C3-D88E-463A-A2EA-40247B2A846A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=dist_installer
OutputBaseFilename=CLMixSetup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\..\dist\CLMix\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
