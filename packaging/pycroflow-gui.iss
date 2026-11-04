; Inno Setup script for the standalone Windows PycroFlow GUI installer.
;
; Produces PycroFlow-Setup-<version>.exe, which *installs* the portable
; pycroflow-gui.exe (from packaging/pycroflow-gui.spec) per-user — no admin
; rights — and creates a proper "PycroFlow GUI" Start-menu shortcut, an
; uninstaller, and an Add/Remove Programs entry. After running the installer
; the GUI launches from the Start menu by name.
;
; Build locally (after dist\pycroflow-gui.exe exists):
;     pip install ".[gui,hardware,monitoring]" pyinstaller
;     pyinstaller --clean --noconfirm packaging/pycroflow-gui.spec
;     iscc /DMyAppVersion=0.1.0 packaging\pycroflow-gui.iss
;
; The CI release workflow (.github/workflows/release-windows.yml) runs exactly
; this on a v* tag and attaches PycroFlow-Setup-<version>.exe to the Release.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

[Setup]
; A fixed AppId so a new version upgrades the previous install in place
; (rather than installing side-by-side). Never change this GUID.
AppId={{3F9A1C7E-6D52-4B38-A0E4-9C7B2F1D8E63}
AppName=PycroFlow
AppVersion={#MyAppVersion}
AppVerName=PycroFlow {#MyAppVersion}
AppPublisher=Jungmann Lab, MPI of Biochemistry
AppPublisherURL=https://github.com/jungmannlab/PycroFlow
AppSupportURL=https://github.com/jungmannlab/PycroFlow/issues
; Per-user install by default (no admin prompt); an admin may opt into an
; all-users install via the privileges dialog.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\PycroFlow
DefaultGroupName=PycroFlow
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE.md
OutputDir=..\dist
OutputBaseFilename=PycroFlow-Setup-{#MyAppVersion}
UninstallDisplayIcon={app}\pycroflow-gui.exe
UninstallDisplayName=PycroFlow {#MyAppVersion}
WizardStyle=modern
Compression=lzma2
SolidCompression=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\pycroflow-gui.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\PycroFlow GUI"; Filename: "{app}\pycroflow-gui.exe"
Name: "{autodesktop}\PycroFlow GUI"; Filename: "{app}\pycroflow-gui.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\pycroflow-gui.exe"; Description: "Launch PycroFlow GUI now"; Flags: nowait postinstall skipifsilent
