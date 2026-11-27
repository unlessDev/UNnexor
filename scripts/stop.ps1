# =====================================================================
# UNnexor — stop.ps1
# Detiene el puente de forma segura: SOLO procesos cuyo ejecutable es el
# python.exe del venv de este proyecto y cuyo comando es run.py.
# =====================================================================
#requires -Version 5.1
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$root   = Split-Path -Parent $PSScriptRoot
$bridge = Join-Path $root "local-bridge"
$venvPython = (Join-Path $bridge ".venv\Scripts\python.exe")
$pidFile = Join-Path $bridge "logs\bridge.pid"

function Info($m) { Write-Host "[stop] $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "[warn] $m" -ForegroundColor Yellow }

$targets = @()

# 1) PID guardado por start.ps1 (se valida antes de matar nada).
if (Test-Path $pidFile) {
    $savedPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
    if ($savedPid -match '^\d+$') {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$savedPid" -ErrorAction SilentlyContinue
        if ($proc -and $proc.ExecutablePath -and
            ($proc.ExecutablePath -ieq $venvPython) -and
            ($proc.CommandLine -match "run\.py")) {
            $targets += [int]$savedPid
        } else {
            Warn "El PID guardado ya no corresponde a UNnexor; se ignora."
        }
    }
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}

# 2) Respaldo: cualquier run.py lanzado con ESTE venv (por si se perdió el pid).
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $_.ExecutablePath -ieq $venvPython -and $_.CommandLine -match "run\.py"
    } | ForEach-Object { $targets += [int]$_.ProcessId }

$targets = $targets | Sort-Object -Unique

if (-not $targets) {
    Write-Host "[stop] No hay procesos de UNnexor en ejecución." -ForegroundColor Cyan
    exit 0
}

foreach ($targetPid in $targets) {
    try {
        taskkill /PID $targetPid /T /F 2>$null | Out-Null
        Write-Host "[ok] Proceso $targetPid detenido." -ForegroundColor Green
    } catch {
        Warn "No se pudo detener el proceso $targetPid (¿ya terminó?)."
    }
}
Write-Host "[stop] UNnexor detenido. (Si UNnexor había iniciado Ollama, se detiene con él.)" -ForegroundColor Green
