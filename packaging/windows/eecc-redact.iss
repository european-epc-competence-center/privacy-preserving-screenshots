; Inno Setup script for eecc-redact. Built by packaging\windows\build.ps1.
;
; Per-user install: no administrator prompt, lands in
; %LOCALAPPDATA%\Programs\eecc-redact, and appears under Settings > Apps with a
; working uninstaller.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; Never change AppId: it is how a newer installer recognises an installed copy.
AppId={{A610BB21-D463-4D38-BA97-B4868A618BE6}
AppName=eecc-redact
AppVersion={#AppVersion}
AppVerName=eecc-redact {#AppVersion}
AppPublisher=European EPC Competence Center
AppPublisherURL=https://github.com/european-epc-competence-center/privacy-preserving-screenshots
DefaultDirName={localappdata}\Programs\eecc-redact
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
LicenseFile=..\..\LICENSE
OutputDir=..\..\dist
OutputBaseFilename=eecc-redact-setup-{#AppVersion}
SetupIconFile=..\..\build\eecc-redact.ico
UninstallDisplayIcon={app}\eecc-redact.exe
UninstallDisplayName=eecc-redact
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Close a running eecc-redact so its files can be replaced by a newer version.
CloseApplications=force
RestartApplications=no

[InstallDelete]
; Whatever an earlier version shipped and this one no longer does.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\..\dist\eecc-redact\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\eecc-redact"; Filename: "{app}\eecc-redact.exe"

[Run]
; First start: the tray, which opens the setup window.
Filename: "{app}\eecc-redact.exe"; Description: "Start eecc-redact"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Parameters: String;
  ResultCode: Integer;
begin
  { usUninstall runs before any file is removed, so eecc-redact.exe can still undo
    what it did: stop the tray, remove the sign-in entry, delete the settings and,
    if asked, the key in Credential Manager. }
  if CurUninstallStep = usUninstall then
  begin
    Parameters := 'uninstall';
    if not UninstallSilent then
      if MsgBox('Also remove your ShinrAI key from this computer?' + #13#10#13#10 +
                'Choose No if you plan to install eecc-redact again.',
                mbConfirmation, MB_YESNO) = IDYES then
        Parameters := Parameters + ' --forget-key';
    Exec(ExpandConstant('{app}\eecc-redact.exe'), Parameters, '', SW_HIDE,
         ewWaitUntilTerminated, ResultCode);
  end;
end;
