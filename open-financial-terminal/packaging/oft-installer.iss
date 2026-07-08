; Inno Setup script — packages the frozen Open Financial Terminal into a Windows installer.
;
; Prerequisites:
;   1. Build the WINDOWED frozen app first (build_desktop.ps1, or pyinstaller without OFT_CONSOLE):
;        dist_desktop\oft-backend\oft-backend.exe   must exist.
;   2. Install Inno Setup 6 (free):   winget install JRSoftware.InnoSetup
;
; Compile:
;   & "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" packaging\oft-installer.iss
; Output:  packaging\dist_installer\OpenFinancialTerminal-Setup-<version>.exe
;
; The frozen app opens its own WebView2 window (pywebview) — no extra runtime is bundled; WebView2
; ships with Windows 11. The app writes all state under %APPDATA%\OpenFinancialTerminal (see
; run_desktop.py), so it installs cleanly under Program Files with no write-back to its own dir.

#define AppName "Open Financial Terminal"
; Overridable from the command line: ISCC.exe /DAppVersion=1.0.3 oft-installer.iss
#ifndef AppVersion
  #define AppVersion "1.0.3"
#endif
#define AppPublisher "Open Financial Terminal"
#define AppExeName "oft-backend.exe"
; Path to the PyInstaller onedir output, relative to this .iss file (packaging/ is under the project root).
#define SourceDir "..\backend\dist_desktop\oft-backend"

[Setup]
; A stable AppId so upgrades replace in place. (Generated once; keep it constant across releases.)
AppId={{8F2C5E14-3A6B-4D29-9C71-OFTERMINAL2026}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
OutputDir=dist_installer
OutputBaseFilename=OpenFinancialTerminal-Setup-{#AppVersion}
SetupIconFile=oft.ico
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
; Default to an all-users (admin) install, but allow a per-user install with no elevation via the
; wizard's prompt or `/CURRENTUSER` on the command line (also enables non-admin/CI validation).
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Recurse the entire onedir bundle (oft-backend.exe + _internal\ with Python, deps, SPA, qhfi config).
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Leave user data (%APPDATA%\OpenFinancialTerminal) intact on uninstall by default — only the
; install dir is removed. Users can delete the data dir manually if they want a full wipe.
