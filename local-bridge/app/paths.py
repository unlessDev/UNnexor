"""Rutas de runtime para desarrollo y para la aplicación instalada.

En desarrollo se conservan las rutas históricas (``local-bridge/.env``,
``local-bridge/logs`` y ``local-bridge/app/data``) para no romper datos ni
pruebas existentes. En un ejecutable PyInstaller se usa un directorio
escribible por usuario (``%LOCALAPPDATA%\\UNnexor``) porque el directorio
del programa puede ser de solo lectura.

Las instalaciones anteriores al cambio de nombre guardaban los datos en
``%LOCALAPPDATA%\\Extdator``; ``app/migration.py`` los copia a la ruta
nueva al arrancar (sin borrar la carpeta antigua).

Variables de entorno (las ``EXTDATOR_*`` se aceptan como alias heredados):
  * ``UNNEXOR_DATA_DIR``: fuerza el directorio de datos (tests, portable).
  * ``UNNEXOR_ENV_FILE``: fuerza la ruta del archivo de configuración.
  * ``UNNEXOR_MODELS_DIR``: fuerza la carpeta de modelos locales.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "UNnexor"
# Nombre histórico: solo se usa para migrar datos de versiones anteriores.
LEGACY_APP_DIR_NAME = "Extdator"
BASE_DIR = Path(__file__).resolve().parent.parent  # local-bridge/
PROJECT_ROOT = BASE_DIR.parent


def env_override(name: str) -> str:
    """Valor de ``UNNEXOR_<name>`` o, si falta, del alias ``EXTDATOR_<name>``."""
    for prefix in ("UNNEXOR_", "EXTDATOR_"):
        value = os.environ.get(prefix + name, "").strip()
        if value:
            return value
    return ""


def is_frozen() -> bool:
    """¿La aplicación corre empaquetada por PyInstaller?"""
    return bool(getattr(sys, "frozen", False))


def local_appdata() -> Path:
    raw = os.environ.get("LOCALAPPDATA")
    if raw:
        return Path(raw)
    return Path.home() / "AppData" / "Local"


# Alias privado conservado para el código existente.
_local_appdata = local_appdata


def app_data_root() -> Path:
    """``%LOCALAPPDATA%\\UNnexor`` (con o sin ejecutable empaquetado)."""
    return local_appdata() / APP_DIR_NAME


def legacy_data_root() -> Path:
    """``%LOCALAPPDATA%\\Extdator``: datos de versiones anteriores."""
    return local_appdata() / LEGACY_APP_DIR_NAME


def data_dir() -> Path:
    """Directorio escribible para configuración, ajustes y logs."""
    override = env_override("DATA_DIR")
    if override:
        return Path(override).expanduser()
    if is_frozen():
        return app_data_root()
    return BASE_DIR / "app" / "data"


def logs_dir() -> Path:
    return data_dir() / "logs" if is_frozen() else BASE_DIR / "logs"


def env_file() -> Path:
    """Archivo ``.env``: junto al proyecto en desarrollo, en datos al instalar."""
    override = env_override("ENV_FILE")
    if override:
        return Path(override).expanduser()
    return data_dir() / ".env" if is_frozen() else BASE_DIR / ".env"


def settings_file() -> Path:
    return data_dir() / "user_settings.json"


def providers_file() -> Path:
    return data_dir() / "providers.json"


def models_dir() -> Path:
    """Modelos locales descargados con consentimiento (voz, embeddings…).

    Siempre en ``%LOCALAPPDATA%\\UNnexor\\models`` (también en desarrollo)
    para no duplicar cientos de MB entre el repositorio y la instalación ni
    meterlos en el árbol de Git. ``UNNEXOR_MODELS_DIR`` lo sustituye.
    La desinstalación ordinaria los conserva.
    """
    override = env_override("MODELS_DIR")
    if override:
        return Path(override).expanduser()
    return app_data_root() / "models"


def copilot_dir() -> Path:
    """Datos del copiloto: perfiles, base de conocimiento, sesiones."""
    return data_dir() / "copilot"


def resource_path(relative: str) -> Path:
    """Recurso empaquetado (``_MEIPASS``) o archivo junto al código en dev."""
    bundle = getattr(sys, "_MEIPASS", None)
    root = Path(bundle) if bundle else BASE_DIR
    return root / relative


def ensure_data_dir() -> Path:
    """Crea el directorio de datos si no existe y lo devuelve."""
    path = data_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path
