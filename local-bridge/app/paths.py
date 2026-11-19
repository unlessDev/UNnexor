"""Rutas de runtime para desarrollo y para la aplicación instalada.

En desarrollo se conservan las rutas históricas (``local-bridge/.env``,
``local-bridge/logs`` y ``local-bridge/app/data``) para no romper datos ni
pruebas existentes. En un ejecutable PyInstaller se usa un directorio
escribible por usuario (``%LOCALAPPDATA%\\Extdator``) porque el directorio
del programa puede ser de solo lectura.

Variables de entorno:
  * ``EXTDATOR_DATA_DIR``: fuerza el directorio de datos (tests, portable).
  * ``EXTDATOR_ENV_FILE``: fuerza la ruta del archivo de configuración.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "Extdator"
BASE_DIR = Path(__file__).resolve().parent.parent  # local-bridge/
PROJECT_ROOT = BASE_DIR.parent


def is_frozen() -> bool:
    """¿La aplicación corre empaquetada por PyInstaller?"""
    return bool(getattr(sys, "frozen", False))


def _local_appdata() -> Path:
    raw = os.environ.get("LOCALAPPDATA")
    if raw:
        return Path(raw)
    return Path.home() / "AppData" / "Local"


def data_dir() -> Path:
    """Directorio escribible para configuración, ajustes y logs."""
    override = os.environ.get("EXTDATOR_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if is_frozen():
        return _local_appdata() / APP_DIR_NAME
    return BASE_DIR / "app" / "data"


def logs_dir() -> Path:
    return data_dir() / "logs" if is_frozen() else BASE_DIR / "logs"


def env_file() -> Path:
    """Archivo ``.env``: junto al proyecto en desarrollo, en datos al instalar."""
    override = os.environ.get("EXTDATOR_ENV_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return data_dir() / ".env" if is_frozen() else BASE_DIR / ".env"


def settings_file() -> Path:
    return data_dir() / "user_settings.json"


def providers_file() -> Path:
    return data_dir() / "providers.json"


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
