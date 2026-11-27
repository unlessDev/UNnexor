# =====================================================================
# UNnexor — install.ps1
# Comprueba Python, crea el entorno virtual, instala dependencias,
# genera la configuración inicial (.env + token) y verifica Ollama.
# =====================================================================
#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$Force   # regenerar .env desde .env.example
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$root   = Split-Path -Parent $PSScriptRoot
$bridge = Join-Path $root "local-bridge"
$venv   = Join-Path $bridge ".venv"

function Info($m) { Write-Host "[install] $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[ok] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[warn] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[error] $m" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "=== UNnexor: instalación del puente local ===" -ForegroundColor Magenta
Write-Host ""

# ---------------------------------------------------------------- Python
Info "Buscando Python 3.10+..."
$candidates = @()
$pyCmd = Get-Command py -ErrorAction SilentlyContinue
if ($pyCmd) { $candidates += @{ exe = "py";    args = @("-3") } }
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCmd) { $candidates += @{ exe = "python"; args = @() } }

$chosen = $null
foreach ($c in $candidates) {
    try { $versionLine = (& $c.exe @($c.args) --version) 2>$null } catch { $versionLine = $null }
    if ($versionLine -match "Python\s+(\d+)\.(\d+)") {
        $major = [int]$matches[1]; $minor = [int]$matches[2]
        if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 10)) {
            $chosen = $c; $chosenVersion = $versionLine; break
        }
    }
}
if (-not $chosen) {
    Fail "Se requiere Python 3.10 o superior. Instálalo desde https://www.python.org/downloads/"
}
Ok "Python encontrado: $chosenVersion"

# ---------------------------------------------------------------- venv
if (-not (Test-Path $venv)) {
    Info "Creando entorno virtual en $venv ..."
    & $chosen.exe @($chosen.args) -m venv $venv
    if ($LASTEXITCODE -ne 0) { Fail "No se pudo crear el entorno virtual." }
} else {
    Ok "El entorno virtual ya existe."
}
$venvPython = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $venvPython)) { Fail "No se encontró $venvPython" }

# ---------------------------------------------------------------- dependencias
Info "Instalando dependencias (PySide6 puede tardar unos minutos)..."
& $venvPython -m pip install --upgrade pip --quiet --disable-pip-version-check
& $venvPython -m pip install -r (Join-Path $bridge "requirements.txt") --quiet --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Fail "Fallo la instalación de dependencias. Revisa tu conexión." }
Ok "Dependencias instaladas."

# ---------------------------------------------------------------- .env
$envFile = Join-Path $bridge ".env"
if (-not (Test-Path $envFile) -or $Force) {
    Copy-Item (Join-Path $bridge ".env.example") $envFile -Force
    Info "Se creó $envFile desde .env.example"
} else {
    Ok "El archivo .env ya existe (no se toca)."
}

Info "Generando token compartido si falta..."
Push-Location $bridge
try {
    & $venvPython -m app.env_setup | ForEach-Object { Write-Host "[env] $_" }
    if ($LASTEXITCODE -ne 0) { Fail "No se pudo preparar el .env." }
} finally {
    Pop-Location
}

# ---------------------------------------------------------------- Ollama
Info "Comprobando Ollama..."
$ollamaPath = $null
$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollamaCmd) { $ollamaPath = $ollamaCmd.Source }
elseif (Test-Path "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe") { $ollamaPath = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" }

if ($ollamaPath) {
    Ok "Ollama instalado: $ollamaPath"
} else {
    Warn "Ollama no se encontró. Instálalo desde https://ollama.com/download"
}

$ollamaUp = $false
try {
    $version = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/version" -TimeoutSec 3
    $ollamaUp = $true
    Ok "Ollama está en ejecución (versión $($version.version))."
} catch {
    Warn "Ollama NO responde en http://127.0.0.1:11434."
    Warn "Inícialo: abre la aplicación de Ollama o ejecuta 'ollama serve'."
}

# ---------------------------------------------------------------- modelo
$modelLine = Select-String -Path $envFile -Pattern "^OLLAMA_MODEL=(.*)$" -ErrorAction SilentlyContinue
$model = if ($modelLine) { $modelLine.Matches[0].Groups[1].Value.Trim() } else { "llama3" }

if ($ollamaUp) {
    try {
        $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 5
        $names = @($tags.models | ForEach-Object { ($_.name -split ":")[0] })
        if ($names -contains $model) {
            Ok "El modelo configurado '$model' está disponible."
        } else {
            Warn "El modelo '$model' NO está instalado."
            Warn "Modelos disponibles: $($names -join ', ')"
            Warn "Instálalo con: ollama pull $model  —  o edita OLLAMA_MODEL en $envFile"
        }
    } catch {
        Warn "No se pudo listar los modelos de Ollama."
    }
}

# ---------------------------------------------------------------- resumen
Write-Host ""
Write-Host "=== Pasos que te quedan ===" -ForegroundColor Magenta
Write-Host " 1. Ejecuta  scripts\start.ps1  para arrancar el puente y el panel flotante."
Write-Host " 2. Carga la extensión:  chrome://extensions  (o brave://extensions, edge://extensions)"
Write-Host "    → activa 'Modo de desarrollador' → 'Cargar descomprimida' → selecciona"
Write-Host "    la carpeta: $root\browser-extension"
Write-Host " 3. Copia el token de $envFile (línea BRIDGE_TOKEN)"
Write-Host "    y pégalo en las opciones de la extensión."
Write-Host " 4. (Opcional, más seguro) Copia el ID de la extensión y ponlo en ALLOWED_ORIGINS"
Write-Host "    del .env, después reinicia con scripts\stop.ps1 + scripts\start.ps1."
Write-Host " 5. Verifica la conexión con el botón 'Probar conexión' de las opciones."
Write-Host ""
Ok "Instalación completada."
