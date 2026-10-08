; Starfront's Windows installer: one file people download and run.
;
;     ISCC.exe /DVersion=0.2.35 packaging\starfront.iss
;
; packaging\build.ps1 runs this after PyInstaller has made packaging\dist\Starfront.
; It installs for the current user, under their own Programs folder, so no
; administrator prompt stands between a customer and the program; puts
; Starfront in the Start menu and, if asked, on the desktop; and uninstalls
; cleanly from Settings > Apps. Nothing about a person's observatory is in the
; program folder - settings, targets and the plan live under their home
; folder - so an upgrade over the top never touches them.

#ifndef Version
  #define Version "0.0.0"
#endif
#define AppName "Starfront"
#define Publisher "Starfront Observatories"
#define Dist "dist\Starfront"

[Setup]
AppId={{7B1C2E64-4D6A-4F3B-9C1E-0A5B3C7D9E11}
AppName={#AppName}
AppVersion={#Version}
AppVerName={#AppName} {#Version}
AppPublisher={#Publisher}
AppPublisherURL=https://starfront.space
AppSupportURL=https://github.com/bray-sfro/starfront
AppUpdatesURL=https://github.com/bray-sfro/starfront/releases
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=dist
OutputBaseFilename=Starfront-Setup-{#Version}
SetupIconFile=build\starfront.ico
UninstallDisplayIcon={app}\Starfront.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#Version}
VersionInfoCompany={#Publisher}
VersionInfoDescription={#AppName} installer

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Put a shortcut on the desktop"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#Dist}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\Starfront.exe"; WorkingDir: "{app}"
Name: "{group}\{#AppName} Console"; Filename: "{app}\Starfront Console.exe"; WorkingDir: "{app}"; Comment: "The same program with a console, for seeing what a window that will not open is saying"
Name: "{group}\Read me first"; Filename: "{app}\READ ME FIRST.txt"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\Starfront.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Starfront.exe"; Description: "Start {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The folder PyInstaller unpacks into; everything in it is the program's own.
Type: filesandordirs; Name: "{app}\_internal"
