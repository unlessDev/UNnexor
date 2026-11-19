"""Almacenamiento centralizado de ajustes del usuario (configuración desde UI).

Capa sobre ``.env``: los ajustes modificados desde la interfaz viven en
``local-bridge/data/user_settings.json`` y se aplican sobre ``Config`` al
arrancar (``.env`` sigue siendo la base; nada del usuario se pierde).

Reglas:
  * Solo claves de la lista blanca (``ALLOWED_KEYS``); el resto se rechaza.
  * Nada de secretos: las credenciales de proveedores viven en el keyring
    del sistema (``providers/manager.py``), jamás en este archivo.
  * Cada clave declara si su cambio requiere reiniciar el componente.
  * Archivo corrupto → se respalda como ``.bak`` y se parte de valores por
    defecto (nunca se rompe el arranque).
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Config

log = logging.getLogger("extdator.settings")

SETTINGS_FILE = Path(__file__).resolve().parent / "data" / "user_settings.json"

# Clave → (tipo esperado, requiere reinicio, validador opcional)
ALLOWED_KEYS: Dict[str, Dict[str, Any]] = {
    "ollama_model": {"type": str, "restart": False},
    "ollama_system_prompt": {"type": str, "restart": False},
    "ollama_temperature": {"type": float, "restart": False, "min": 0.0, "max": 2.0},
    "ollama_keep_alive": {"type": str, "restart": False},
    "ollama_think": {"type": bool, "restart": True},
    "ollama_timeout": {"type": float, "restart": True, "min": 10.0, "max": 7200.0},
    "ollama_host": {"type": str, "restart": True},
    "ollama_extra_options": {"type": dict, "restart": True},
    "max_history_messages": {"type": int, "restart": True, "min": 2, "max": 200},
    "max_text_length": {"type": int, "restart": True, "min": 64, "max": 200_000},
    "rate_limit_per_minute": {"type": int, "restart": True, "min": 1, "max": 600},
    "window_opacity": {"type": int, "restart": False, "min": 40, "max": 100},
    "always_on_top": {"type": bool, "restart": False},
    "hover_scroll_hook": {"type": bool, "restart": True},
    # Proveedor de IA activo (Fase 4): "ollama" o id de proveedor remoto.
    "provider": {"type": str, "restart": False},
    "remote_model": {"type": str, "restart": False},
    "log_level": {
        "type": str,
        "restart": True,
        "choices": ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
    },
    "log_privacy": {"type": bool, "restart": False},
}

# Claves que el worker/generador puede aplicar en caliente (POST /api/config).
LIVE_KEYS = {
    "ollama_model",
    "ollama_system_prompt",
    "ollama_temperature",
    "ollama_keep_alive",
    "provider",
    "remote_model",
}


class SettingsValidationError(ValueError):
    """Valor rechazado con mensaje orientado al usuario."""


class SettingsStore:
    """Persistencia y aplicación de los ajustes del usuario."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else SETTINGS_FILE
        self._values: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    def load(self) -> Dict[str, Any]:
        """Lee el archivo (con recuperación ante corrupción)."""
        self._values = {}
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                self._values = {
                    key: value for key, value in data.items() if key in ALLOWED_KEYS
                }
        except FileNotFoundError:
            pass
        except (json.JSONDecodeError, OSError) as exc:
            backup = self.path.with_suffix(".json.bak")
            try:
                shutil.copy2(self.path, backup)
                log.warning(
                    "user_settings.json corrupto; respaldo en %s (%s)", backup, exc
                )
            except OSError:
                log.warning("user_settings.json corrupto y sin respaldo (%s)", exc)
            self._values = {}
        return dict(self._values)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._values, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)

    # ------------------------------------------------------------------ #
    def apply_to(self, cfg: Config) -> List[str]:
        """Aplica los ajustes guardados sobre ``Config``. Devuelve las claves."""
        applied: List[str] = []
        for key, value in self._values.items():
            try:
                _validate_key(key, value)
            except SettingsValidationError as exc:
                log.warning("Ajuste inválido en %s (se ignora): %s", key, exc)
                continue
            setattr(cfg, key, value)
            applied.append(key)
        return applied

    def update(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        """Valida, guarda y devuelve lo aplicado. Lanza SettingsValidationError."""
        applied: Dict[str, Any] = {}
        for key, value in changes.items():
            _validate_key(key, value)
        for key, value in changes.items():
            self._values[key] = value
            applied[key] = value
        if applied:
            self.save()
        return applied

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def as_dict(self) -> Dict[str, Any]:
        return dict(self._values)

    def reset(self) -> Dict[str, Any]:
        """Borra los ajustes del usuario (restaura los valores del .env)."""
        removed = dict(self._values)
        self._values = {}
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError as exc:
            log.warning("No se pudo borrar %s: %s", self.path, exc)
        return removed


# --------------------------------------------------------------------- #
def _validate_key(key: str, value: Any) -> None:
    spec = ALLOWED_KEYS.get(key)
    if spec is None:
        raise SettingsValidationError(f"Ajuste desconocido: '{key}'.")
    expected = spec["type"]
    if expected is bool:
        if not isinstance(value, bool):
            raise SettingsValidationError(f"'{key}' debe ser verdadero/falso.")
    elif expected is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsValidationError(f"'{key}' debe ser un número.")
        value = float(value)
    elif expected is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingsValidationError(f"'{key}' debe ser un entero.")
    elif expected is dict:
        if not isinstance(value, dict):
            raise SettingsValidationError(f"'{key}' debe ser un objeto JSON.")
        for sub_key, sub_value in value.items():
            if not isinstance(sub_value, (int, float, str, bool)):
                raise SettingsValidationError(
                    f"'{key}.{sub_key}': solo se aceptan números, textos y "
                    "verdadero/falso."
                )
    else:
        if not isinstance(value, str):
            raise SettingsValidationError(f"'{key}' debe ser texto.")
        if key in ("ollama_model", "ollama_host") and not value.strip():
            raise SettingsValidationError(f"'{key}' no puede quedar vacío.")
    if "min" in spec and value < spec["min"]:
        raise SettingsValidationError(f"'{key}' debe ser ≥ {spec['min']}.")
    if "max" in spec and value > spec["max"]:
        raise SettingsValidationError(f"'{key}' debe ser ≤ {spec['max']}.")
    if "choices" in spec and value not in spec["choices"]:
        raise SettingsValidationError(
            f"'{key}' debe ser uno de: {', '.join(spec['choices'])}."
        )


def restart_required_for(keys: List[str]) -> List[str]:
    """Subconjunto de ``keys`` que exige reiniciar para aplicarse."""
    return [key for key in keys if ALLOWED_KEYS.get(key, {}).get("restart")]
