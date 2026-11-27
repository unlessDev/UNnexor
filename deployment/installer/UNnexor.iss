; Instalador de UNnexor para Windows (Inno Setup 6).
;
; Compilar con:
;   "C:\Users\<usuario>\AppData\Local\Programs\Inno Setup 6\ISCC.exe" ^
;       /DMyAppVersion=2.0.0 deployment\installer\UNnexor.iss
;
; O, preferiblemente, con deployment\installer\build.py, que valida el
; ejecutable, compila, prueba la instalación y genera SHA-256 e informe.
;
; Instalación POR USUARIO (sin administrador) en
; %LOCALAPPDATA%\Programs\UNnexor. Los datos viven en
; %LOCALAPPDATA%\Extdator (nombre histórico conservado a propósito: una
; actualización desde Extdator mantiene ajustes, credenciales y modelos)
; y se conservan al desinstalar salvo que el
; usuario elija la limpieza completa.

#ifndef MyAppVersion
  #define MyAppVersion "2.0.0"
#endif
#define MyAppName "UNnexor"
#define MyAppExeName "UNnexor.exe"

[Setup]
AppId={{753e940c-269a-459a-9a09-1ff607161317}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=UnlessDev
DefaultDirName={localappdata}\Programs\UNnexor
DefaultGroupName={#MyAppName}
; Tras el cambio de nombre (Extdator -> UNnexor) el grupo del menú Inicio
; debe llamarse UNnexor aunque una instalación previa usara "Extdator".
UsePreviousGroup=no
DisableProgramGroupPage=yes
AllowNoIcons=yes
OutputDir=..\..\release\installer
OutputBaseFilename=UNnexor-{#MyAppVersion}-win64-setup
SetupIconFile=..\..\local-bridge\assets\unnexor.ico
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
VersionInfoCompany=UnlessDev
VersionInfoDescription=Instalador de UNnexor
VersionInfoProductName=UNnexor
VersionInfoProductVersion={#MyAppVersion}
VersionInfoVersion={#MyAppVersion}

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"; Flags: unchecked

[Files]
Source: "..\..\release\windows\UNnexor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "INSTALL_README.txt"; DestDir: "{app}"; DestName: "LEEME.txt"; Flags: ignoreversion isreadme

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
; Actualización desde Extdator (mismo AppId): retirar el ejecutable y los
; accesos directos con el nombre anterior. Los DATOS no se tocan.
Type: files; Name: "{app}\Extdator.exe"
Type: files; Name: "{autodesktop}\Extdator.lnk"
Type: filesandordirs; Name: "{userprograms}\Extdator"

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
    '¿Quieres eliminar también tus ajustes, registros y credenciales de UNnexor?'
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
    // Seguridad: solo borrar si parece un directorio de datos de UNnexor.
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
