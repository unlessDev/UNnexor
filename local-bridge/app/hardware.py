"""Detección de hardware del equipo (Windows y fallback multiplataforma).

Diseño:
  * Sin permisos administrativos y sin WMI lento: la CPU se lee del registro
    (``CentralProcessor\\0``) y la VRAM de las claves de clase de pantalla
    (``HardwareInformation.qwMemorySize``, valor de 64 bits fiable; el campo
    ``AdapterRAM`` de WMI está limitado a 4 GB por ser de 32 bits).
  * RAM y discos con ``psutil`` (multiplataforma, en rueda binaria).
  * Aceleración real de Ollama: se consulta por separado con
    ``OllamaClient.gpu_acceleration_active()`` (``GET /api/ps``); una GPU
    detectada no garantiza que el runtime la use (p. ej. ROCm/CUDA sin
    soporte), por eso se informa como dato aparte.

Los datos se obtienen en <200 ms normalmente; ``snapshot()`` no bloquea la
GUI si se llama desde un QThread.
"""

from __future__ import annotations

import platform
import socket
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .ollama_client import OllamaClient


# --------------------------------------------------------------------- #
# Estructuras
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class GPUInfo:
    """Una GPU detectada. VRAM en MB (None = desconocida)."""

    name: str
    vendor: str  # "NVIDIA" | "AMD" | "Intel" | "desconocido"
    vram_total_mb: Optional[int] = None
    vram_source: str = ""  # de dónde salió el dato (registro, WMI, estimado)


@dataclass
class HardwareInfo:
    """Instantánea completa del hardware relevante."""

    cpu_name: str
    cpu_cores: int  # núcleos físicos
    cpu_threads: int  # hilos lógicos
    cpu_arch: str  # AMD64, ARM64...
    cpu_freq_mhz: Optional[int]
    ram_total_mb: int
    ram_available_mb: int
    ram_used_percent: float
    gpus: List[GPUInfo] = field(default_factory=list)
    disk_path: str = ""
    disk_total_gb: Optional[float] = None
    disk_free_gb: Optional[float] = None
    models_dir: str = ""
    models_dir_size_gb: Optional[float] = None
    hostname: str = ""
    os_name: str = ""
    detected_at: str = ""  # ISO-8601 de la detección
    detection_ms: int = 0
    ollama_gpu_active: Optional[bool] = None  # dato de terreno (GET /api/ps)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["gpus"] = [asdict(g) for g in self.gpus]
        return data


# --------------------------------------------------------------------- #
# Lecturas del registro de Windows (instantáneas, sin WMI)
# --------------------------------------------------------------------- #
_CPU_KEY = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
_DISPLAY_CLASS = (
    r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
)


def _winreg_cpu() -> Dict[str, Any]:
    """Nombre y frecuencia base de la CPU desde el registro (solo Windows)."""
    import winreg

    out: Dict[str, Any] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _CPU_KEY) as key:
            for value, label in (("ProcessorNameString", "name"), ("~MHz", "freq")):
                try:
                    data, _ = winreg.QueryValueEx(key, value)
                    out[label] = data
                except OSError:
                    continue
    except OSError:
        pass
    return out


def _vendor_from_name(name: str) -> str:
    lowered = name.lower()
    if "nvidia" in lowered or "geforce" in lowered or "quadro" in lowered:
        return "NVIDIA"
    if "amd" in lowered or "radeon" in lowered or "ati " in lowered:
        return "AMD"
    if "intel" in lowered or "iris" in lowered or "uhd" in lowered:
        return "Intel"
    if "apple" in lowered:
        return "Apple"
    return "desconocido"


def _winreg_gpus() -> List[GPUInfo]:
    """GPUs desde las claves de clase de pantalla del registro.

    ``HardwareInformation.qwMemorySize`` es el tamaño real de VRAM (uint64);
    es la vía fiable en Windows sin WMI y sin límite de 4 GB.
    """
    import winreg

    gpus: List[GPUInfo] = []
    try:
        base = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS)
    except OSError:
        return gpus
    with base:
        index = 0
        while True:  # claves 0000, 0001, ... (hasta ~8 adaptadores)
            try:
                subkey_name = winreg.EnumKey(base, index)
            except OSError:
                break
            index += 1
            if not subkey_name.isdigit():
                continue
            try:
                with winreg.OpenKey(base, subkey_name) as key:
                    name = ""
                    vram_bytes: Optional[int] = None
                    try:
                        name, _ = winreg.QueryValueEx(key, "DriverDesc")
                    except OSError:
                        continue
                    for value in (
                        "HardwareInformation.qwMemorySize",
                        "HardwareInformation.MemorySize",
                    ):
                        try:
                            data, _ = winreg.QueryValueEx(key, value)
                            if isinstance(data, int) and data > 1_000_000:
                                vram_bytes = data
                                break
                            if isinstance(data, bytes) and len(data) >= 8:
                                vram_bytes = int.from_bytes(data[:8], "little")
                                if vram_bytes > 1_000_000:
                                    break
                                vram_bytes = None
                        except (OSError, ValueError):
                            continue
                    if not name:
                        continue
                    vendor = _vendor_from_name(name)
                    vram_mb = int(vram_bytes / (1024 * 1024)) if vram_bytes else None
                    gpus.append(
                        GPUInfo(
                            name=name,
                            vendor=vendor,
                            vram_total_mb=vram_mb,
                            vram_source=(
                                "registro (qwMemorySize)"
                                if vram_mb
                                else "VRAM desconocida"
                            ),
                        )
                    )
            except OSError:
                continue
    return gpus


def _wmi_gpus_fallback() -> List[GPUInfo]:
    """Respaldo con WMI (lento, ~1-2 s): se usa solo si el registro falla."""
    import subprocess

    try:
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_VideoController).Name",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        names = [
            line.strip() for line in (proc.stdout or "").splitlines() if line.strip()
        ]
    except (OSError, subprocess.SubprocessError):
        return []
    return [
        GPUInfo(
            name=name,
            vendor=_vendor_from_name(name),
            vram_total_mb=None,
            vram_source="WMI (VRAM desconocida)",
        )
        for name in names
    ]


# --------------------------------------------------------------------- #
# Detección principal
# --------------------------------------------------------------------- #
def default_models_dir() -> Path:
    """Carpeta de modelos de Ollama (~/.ollama/models por defecto)."""
    custom = Path.home() / ".ollama" / "models"
    return custom


def directory_size_bytes(path: Path, limit_entries: int = 20_000) -> int:
    """Tamaño acumulado de una carpeta (con tope de entradas por seguridad)."""
    total = 0
    try:
        count = 0
        for item in path.rglob("*"):
            count += 1
            if count > limit_entries:
                break
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


def detect(models_dir: Optional[Path] = None) -> HardwareInfo:
    """Recolecta una instantánea del hardware. No requiere admin."""
    import time

    import psutil

    started = time.perf_counter()
    notes: List[str] = []

    # --- CPU -------------------------------------------------------- #
    cpu_name = platform.processor() or ""
    cpu_freq: Optional[int] = None
    if platform.system() == "Windows":
        reg = _winreg_cpu()
        cpu_name = reg.get("name") or cpu_name
        freq = reg.get("freq")
        if isinstance(freq, int) and freq > 100:
            cpu_freq = freq
    if not cpu_name:
        cpu_name = "desconocido"
        notes.append("No se pudo leer el nombre de la CPU.")

    # --- RAM -------------------------------------------------------- #
    vm = psutil.virtual_memory()
    ram_total_mb = int(vm.total / (1024 * 1024))
    ram_available_mb = int(vm.available / (1024 * 1024))
    ram_used_percent = round(vm.percent, 1)

    # --- GPU -------------------------------------------------------- #
    gpus: List[GPUInfo] = []
    if platform.system() == "Windows":
        gpus = _winreg_gpus() or _wmi_gpus_fallback()
        if not gpus:
            notes.append("No se detectó ninguna GPU (se asume solo CPU).")
        if any(g.vram_total_mb is None for g in gpus):
            notes.append(
                "La VRAM de alguna GPU no pudo leerse; las estimaciones de "
                "memoria serán más conservadoras."
            )
    else:
        notes.append("Detección de GPU por registro solo disponible en Windows.")

    # --- Almacenamiento --------------------------------------------- #
    models_path = models_dir or default_models_dir()
    disk_path = models_path.anchor or str(Path.home().anchor)
    disk_total = disk_free = models_size = None
    try:
        usage = psutil.disk_usage(str(models_path))
        disk_total = round(usage.total / (1024**3), 1)
        disk_free = round(usage.free / (1024**3), 1)
    except OSError:
        disk_path = ""
        notes.append("No se pudo leer el disco donde residen los modelos.")
    if models_path.exists():
        models_size = round(directory_size_bytes(models_path) / (1024**3), 2)

    elapsed = int((time.perf_counter() - started) * 1000)
    return HardwareInfo(
        cpu_name=cpu_name,
        cpu_cores=psutil.cpu_count(logical=False) or psutil.cpu_count() or 0,
        cpu_threads=psutil.cpu_count(logical=True) or 0,
        cpu_arch=platform.machine() or "desconocida",
        cpu_freq_mhz=cpu_freq,
        ram_total_mb=ram_total_mb,
        ram_available_mb=ram_available_mb,
        ram_used_percent=ram_used_percent,
        gpus=gpus,
        disk_path=disk_path,
        disk_total_gb=disk_total,
        disk_free_gb=disk_free,
        models_dir=str(models_path),
        models_dir_size_gb=models_size,
        hostname=socket.gethostname(),
        os_name=f"{platform.system()} {platform.release()}",
        detected_at=datetime.now(timezone.utc).isoformat(),
        detection_ms=elapsed,
        notes=notes,
    )


# --------------------------------------------------------------------- #
def gpu_acceleration_active(ollama: OllamaClient) -> Optional[bool]:
    """¿Ollama está usando GPU de verdad?

    Dato de terreno: ``GET /api/ps`` expone ``size_vram`` por modelo cargado.
    True = hay (o hubo) un modelo con parte en VRAM. None = no hay modelos
    cargados todavía (no se puede afirmar nada). False = hay modelos cargados
    y ninguno usa VRAM (runtime en CPU).
    """
    try:
        running = ollama.running_models()
    except Exception:
        return None
    if not running:
        return None
    return any(entry.get("size_vram", 0) > 0 for entry in running)
