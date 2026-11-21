; Instalador de Extdator para Windows (Inno Setup 6).
;
; Compilar con:
;   "C:\Users\<usuario>\AppData\Local\Programs\Inno Setup 6\ISCC.exe" ^
;       /DMyAppVersion=1.2.0 deployment\installer\Extdator.iss
;
; O, preferiblemente, con deployment\installer\build.py, que valida el
; ejecutable, compila, prueba la instalación y genera SHA-256 e informe.
;
; Instalación POR USUARIO (sin administrador) en
; %LOCALAPPDATA%\Programs\Extdator. Los datos viven en
; %LOCALAPPDATA%\Extdator y se conservan al desinstalar salvo que el
; usuario elija la limpieza completa.

#ifndef MyAppVersion
  #define MyAppVersion "1.2.0"
#endif
#define MyAppName "Extdator"
#define MyAppExeName "Extdator.exe"

[Setup]
AppId={{753e940c-269a-459a-9a09-1ff607161317}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Extdator
DefaultDirName={localappdata}\Programs\Extdator
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
OutputDir=..\..\release\installer
OutputBaseFilename=Extdator-{#MyAppVersion}-win64-setup
SetupIconFile=..\..\local-bridge\assets\extdator.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
VersionInfoCompany=Extdator
VersionInfoDescription=Instalador de Extdator
VersionInfoProductName=Extdator
VersionInfoProductVersion={#MyAppVersion}
VersionInfoVersion={#MyAppVersion}

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"; Flags: unchecked

[Files]
Source: "..\..\release\windows\Extdator\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "INSTALL_README.txt"; DestDir: "{app}"; DestName: "LEEME.txt"; Flags: ignoreversion isreadme

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Desinstalar {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--purge-secrets"; Flags: runhidden waituntilterminated; RunOnceId: "PurgeSecrets"; Check: PurgeDataRequested

[Code]
var
  PurgeUserData: Boolean;

function PurgeDataRequested(): Boolean;
begin
  Result := PurgeUserData;
end;

function InitializeUninstall(): Boolean;
var
  Answer: Integer;
begin
  Result := True;
  PurgeUserData := False;
  if UninstallSilent then
  begin
    // Para pruebas y automatización: /PURGEDATA elimina también los datos.
    PurgeUserData := Pos('/PURGEDATA', Uppercase(GetCmdTail)) > 0;
    Exit;
  end;
  Answer := MsgBox(
    '¿Quieres eliminar también tus ajustes, registros y credenciales de Extdator?'
    + #13#10 + #13#10
    + 'Si eliges No, los datos se conservarán en %LOCALAPPDATA%\Extdator '
    + 'para una futura reinstalación.',
    mbConfirmation, MB_YESNO or MB_DEFBUTTON2);
  PurgeUserData := Answer = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if (CurUninstallStep = usPostUninstall) and PurgeUserData then
  begin
    DataDir := GetEnv('EXTDATOR_DATA_DIR');
    if DataDir = '' then
      DataDir := ExpandConstant('{localappdata}\Extdator');
    // Seguridad: solo borrar si parece un directorio de datos de Extdator.
    if DirExists(DataDir) and (
      FileExists(DataDir + '\.env') or
      FileExists(DataDir + '\user_settings.json') or
      FileExists(DataDir + '\providers.json') or
      FileExists(DataDir + '\self-check.json') or
      DirExists(DataDir + '\logs')
    ) then
      DelTree(DataDir, True, True, True);
  end;
end;
