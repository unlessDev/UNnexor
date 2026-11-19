# =====================================================================
# Extdator — check_environment.ps1
# Diagnóstico completo del entorno: Python, venv, dependencias, Ollama,
# modelos, puertos y navegadores. No modifica nada.
# =====================================================================
#requires -Version 5.1
$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$root   = Split-Path -Parent $PSScriptRoot
$bridge = Join-Path $root "local-bridge"
$venvPython = Join-Path $bridge ".venv\Scripts\python.exe"

function Section($m) { Write-Host ""; Write-Host "=== $m ===" -ForegroundColor Magenta }
function Ok($m)   { Write-Host "[ok] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[warn] $m" -ForegroundColor Yellow }
function Info($m) { Write-Host "[info] $m" -ForegroundColor Cyan }

Write-Host "=== Diagnóstico del entorno para Extdator ===" -ForegroundColor Magenta

Section "Sistema"
$os = Get-CimInstance Win32_OperatingSystem
Ok "Windows: $($os.Caption) ($($os.Version))"
Ok "PowerShell: $($PSVersionTable.PSVersion)"

Section "Python"
try {
    $version = & python --version 2>&1
    Ok "python: $version"
} catch { Warn "python no está en PATH" }
try {
    $version = & py -3 --version 2>&1
    Ok "py -3: $version"
} catch { Warn "py launcher no disponible" }

if (Test-Path $venvPython) {
    Ok "Entorno virtual: $venvPython"
    foreach ($package in @("fastapi", "uvicorn", "httpx", "pydantic", "PySide6", "pytest")) {
        $line = & $venvPython -m pip show $package 2>$null | Select-String "^Version:"
        if ($line) { Ok "$package $($line.Line.Split(' ')[1])" }
        else { Warn "$package NO instalado en el venv" }
    }
} else {
    Warn "No hay entorno virtual en local-bridge\.venv (ejecuta install.ps1)"
}

Section "Ollama"
$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollamaCmd) { Ok "ollama en PATH: $($ollamaCmd.Source)" }
elseif (Test-Path "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe") {
    Ok "ollama instalado en LOCALAPPDATA\Programs\Ollama"
} else { Warn "ollama no encontrado" }

try {
    $version = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/version" -TimeoutSec 3
    Ok "Ollama en ejecución (versión $($version.version))"
    $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 5
    $names = @($tags.models | ForEach-Object { $_.name })
    if ($names) { Ok "Modelos instalados: $($names -join ', ')" }
    else { Warn "No hay modelos instalados (ollama pull llama3)" }
} catch {
    Warn "Ollama no responde en http://127.0.0.1:11434 (¿está en ejecución?)"
}

Section "Puertos"
foreach ($port in @(5000, 11434)) {
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        $procId = $listener[0].OwningProcess
        $procName = (Get-Process -Id $procId -ErrorAction SilentlyContinue).ProcessName
        Ok "Puerto $port en uso por '$procName' (PID $procId)"
    } else {
        Info "Puerto $port libre"
    }
}

Section "Puente Extdator"
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:5000/health" -TimeoutSec 2
    Ok "Puente activo (versión $($health.version), modelo $($health.model))"
} catch {
    Info "Puente no está en ejecución (normal si no usaste start.ps1)"
}

Section "Navegadores"
foreach ($b in @(
        @{ Name = "Chrome"; Path = "C:\Program Files\Google\Chrome\Application\chrome.exe" },
        @{ Name = "Edge";   Path = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" },
        @{ Name = "Brave";  Path = "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe" }
    )) {
    if (Test-Path $b.Path) { Ok "$($b.Name) instalado" }
    else { Warn "$($b.Name) no encontrado en la ruta habitual" }
}

Write-Host ""
Write-Host "=== Fin del diagnóstico ===" -ForegroundColor Magenta
