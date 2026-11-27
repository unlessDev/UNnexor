# =====================================================================
# UNnexor AI Bridge — start.ps1  (by UnlessDev)
# Herramienta de desarrollo/diagnóstico: valida el entorno y arranca el
# puente (servidor + panel). La lógica de arranque real (Ollama incluido)
# vive en Python (run.py → app.main), la misma que usa UNnexor.exe.
#
#   .\scripts\start.ps1            → arranca sin ventana de terminal
#   .\scripts\start.ps1 -Console   → muestra la terminal con los registros
#   .\scripts\start.ps1 -ServerOnly→ solo servidor HTTP, sin panel
# =====================================================================
#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$ServerOnly,   # sin panel flotante
    [switch]$Console,      # mostrar la terminal del puente (registros HTTP)
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

function Tag($label, $color, $m) {
    Write-Host "[" -NoNewline -ForegroundColor DarkGray
    Write-Host $label -NoNewline -ForegroundColor $color
    Write-Host "] $m"
}
function Info($m) { if (-not $Quiet) { Tag " info " Cyan $m } }
function Ok($m)   { Tag "  ok  " Green $m }
function Warn($m) { Tag " aviso" Yellow $m }
function Fail($m) { Tag "error " Red $m; exit 1 }

# ---------------------------------------------------------------- venv
if (-not (Test-Path $venvPython)) {
    Info "Entorno virtual no encontrado; ejecutando install.ps1..."
    & (Join-Path $PSScriptRoot "install.ps1")
    if (-not (Test-Path $venvPython)) { Fail "La instalación no creó el entorno virtual." }
}

# Presentación (misma implementación que la consola del puente).
Push-Location $bridge
try { & $venvPython -m app.banner } catch {} finally { Pop-Location }

Ok "Entorno virtual presente."
if (-not (Test-Path $envFile)) {
    Info ".env no existe; ejecutando install.ps1 para generarlo..."
    & (Join-Path $PSScriptRoot "install.ps1")
}
Ok "Configuración: $envFile"

# ---------------------------------------------------------------- ¿ya en marcha?
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:5000/health" -TimeoutSec 2
    if ($health.app -eq "extdator-bridge") {
        Warn "UNnexor ya está en ejecución. Nada que hacer (usa stop.ps1 para detenerlo)."
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
    Info "Ollama no responde: UNnexor intentará iniciarlo (OLLAMA_AUTOSTART=1)"
    Info "y lo detendrá al cerrarse. Si no está instalado: https://ollama.com/download"
}

# ---------------------------------------------------------------- lanzar
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
$arguments = @("run.py")
if ($ServerOnly) { $arguments += "--server-only" }
# Sin -Console la terminal del puente queda oculta: no hay ventana ni icono
# en la barra de tareas. Los registros siguen en logs\bridge.log y en
# ⚙ → "Consola del puente" dentro de la aplicación.
$style = if ($Console -or $ServerOnly) { "Normal" } else { "Hidden" }

Info "Iniciando UNnexor ($($arguments -join ' '), terminal: $style)..."
$process = Start-Process -FilePath $venvPython `
    -ArgumentList $arguments `
    -WorkingDirectory $bridge `
    -WindowStyle $style `
    -PassThru
Set-Content -Path $pidFile -Value $process.Id -Encoding Ascii
Ok "Proceso iniciado con PID $($process.Id) (guardado en $pidFile)."

# ---------------------------------------------------------------- esperar health
$ready = $false
for ($i = 0; $i -lt 40; $i++) {
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
        Warn "Puente activo en http://127.0.0.1:$port; Ollama aún no responde."
    }
    if (-not $ServerOnly) {
        Ok "Panel de UNnexor visible (esquina inferior derecha)."
    }
    Ok "Registros: $logsDir\bridge.log  ·  ⚙ → Consola del puente"
} else {
    Warn "El servidor no respondió en 20 s. Revisa $logsDir\bridge.log"
    exit 1
}
