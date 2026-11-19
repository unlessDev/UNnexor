# =====================================================================
# Extdator — start.ps1
# Valida la configuración, comprueba Ollama, arranca el puente
# (servidor + panel flotante) y guarda el PID para stop.ps1.
# =====================================================================
#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$ServerOnly,   # sin panel flotante
    [switch]$Quiet         # menos mensajes
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$root   = Split-Path -Parent $PSScriptRoot
$bridge = Join-Path $root "local-bridge"
$venv   = Join-Path $bridge ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
$envFile = Join-Path $bridge ".env"
$logsDir = Join-Path $bridge "logs"
$pidFile = Join-Path $logsDir "bridge.pid"

function Info($m) { Write-Host "[start] $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[ok] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[warn] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[error] $m" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- venv
if (-not (Test-Path $venvPython)) {
    Info "Entorno virtual no encontrado; ejecutando install.ps1..."
    & (Join-Path $PSScriptRoot "install.ps1")
    if (-not (Test-Path $venvPython)) { Fail "La instalación no creó el entorno virtual." }
} else {
    Ok "Entorno virtual presente."
}

if (-not (Test-Path $envFile)) {
    Info ".env no existe; ejecutando install.ps1 para generarlo..."
    & (Join-Path $PSScriptRoot "install.ps1")
}
Ok "Configuración: $envFile"

# ---------------------------------------------------------------- ¿ya en marcha?
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:5000/health" -TimeoutSec 2
    if ($health.app -eq "extdator-bridge") {
        Warn "Extdator ya está en ejecución. Nada que hacer (usa stop.ps1 para detenerlo)."
        exit 0
    }
} catch {}

# ---------------------------------------------------------------- puerto libre
# Detecta el puerto configurado leyendo el .env (simple y explícito).
$port = 5000
$portLine = Select-String -Path $envFile -Pattern "^BRIDGE_PORT=(\d+)" -ErrorAction SilentlyContinue
if ($portLine) { $port = [int]$portLine.Matches[0].Groups[1].Value }

$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    Fail "El puerto $port está ocupado por otro proceso (PID $($listener.OwningProcess))."
}

# ---------------------------------------------------------------- Ollama
try {
    $version = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/version" -TimeoutSec 3
    Ok "Ollama en ejecución (versión $($version.version))."
} catch {
    Warn "Ollama no responde: el puente arrancará, pero la generación fallará"
    Warn "hasta que abras la aplicación de Ollama o ejecutes 'ollama serve'."
}

# ---------------------------------------------------------------- lanzar
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
$arguments = @("run.py")
if ($ServerOnly) { $arguments += "--server-only" }

Info "Iniciando Extdator (argumentos: $($arguments -join ' '))..."
$process = Start-Process -FilePath $venvPython `
    -ArgumentList $arguments `
    -WorkingDirectory $bridge `
    -WindowStyle Minimized `
    -PassThru
Set-Content -Path $pidFile -Value $process.Id -Encoding Ascii
Ok "Proceso iniciado con PID $($process.Id) (guardado en $pidFile)."

# ---------------------------------------------------------------- esperar health
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 2
        if ($health.app -eq "extdator-bridge") { $ready = $true; break }
    } catch {}
}

if ($ready) {
    if ($health.ollama.reachable) {
        Ok "Puente activo en http://127.0.0.1:$port (Ollama conectado)."
    } else {
        Warn "Puente activo en http://127.0.0.1:$port, pero Ollama no responde."
    }
    if (-not $ServerOnly) {
        Ok "Panel flotante visible en la esquina inferior derecha."
    }
    Ok "Logs: $logsDir\bridge.log"
} else {
    Warn "El servidor no respondió en 15 s. Revisa $logsDir\bridge.log"
    exit 1
}
