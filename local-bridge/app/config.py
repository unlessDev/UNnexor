"""Configuración del puente local de UNnexor.

Lee la configuración desde variables de entorno y desde el archivo
``local-bridge/.env`` (creado a partir de ``.env.example``). Ningún valor
sensible va hardcodeado: el token se genera en la primera ejecución.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import dotenv_values

from . import paths

BASE_DIR = paths.BASE_DIR  # local-bridge/
PROJECT_ROOT = paths.PROJECT_ROOT  # UNnexor/
DEFAULT_ENV_FILE = paths.env_file()
LOG_DIR = paths.logs_dir()

_TRUE = {"1", "true", "yes", "on", "sí", "si"}
_FALSE = {"0", "false", "no", "off"}


class ConfigError(ValueError):
    """Configuración inválida (mensaje pensado para el usuario)."""


@dataclass
class Config:
    """Parámetros del puente. Todos tienen valores por defecto razonables."""

    # Servidor HTTP local
    host: str = "127.0.0.1"
    port: int = 5000
    auth_required: bool = True
    token: str = ""
    allowed_origins: List[str] = field(default_factory=lambda: ["chrome-extension://*"])
    max_text_length: int = 32_000
    rate_limit_per_minute: int = 30

    # Ollama
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3"
    ollama_system_prompt: str = (
        "Eres un asistente personal útil y conciso. Responde en el idioma "
        "del usuario, salvo que te pida explícitamente otro idioma. Usa Markdown "
        "cuando ayude a leer la respuesta y escribe las fórmulas en LaTeX "
        "entre $…$ (en línea) o $$…$$ (en bloque)."
    )
    ollama_temperature: Optional[float] = 0.7
    ollama_keep_alive: str = "5m"
    ollama_timeout: float = 300.0
    # Habilita/deshabilita el "thinking" de los modelos que lo soportan
    # (p. ej. qwen3, deepseek-r1). None = no enviar el parámetro (el
    # modelo usa su comportamiento por defecto).
    ollama_think: Optional[bool] = None
    # Opciones extra para la API de Ollama (options del /api/chat), p. ej.
    # top_p, top_k, repeat_penalty, num_ctx, num_predict.
    ollama_extra_options: Dict[str, Any] = field(default_factory=dict)

    # Conversación
    max_history_messages: int = 20

    # Ventana flotante
    window_width: int = 480
    window_height: int = 600
    window_opacity: int = 95
    always_on_top: bool = True
    hover_scroll_hook: bool = True

    # Ollama: iniciar `ollama serve` si no está en ejecución (y detener al
    # salir SOLO ese proceso; un Ollama ya abierto nunca se toca).
    ollama_autostart: bool = True
    # Consultar al iniciar la biblioteca de ollama.com y la última versión
    # de Ollama (solo lectura; nunca descarga modelos).
    online_catalog: bool = True

    # Logs
    log_level: str = "INFO"
    log_privacy: bool = True  # jamás registrar el contenido de las selecciones

    def validate(self) -> None:
        if not (1 <= self.port <= 65535):
            raise ConfigError(f"Puerto inválido: {self.port}")
        if self.max_text_length < 64:
            raise ConfigError("MAX_TEXT_LENGTH debe ser al menos 64 caracteres.")
        if self.max_history_messages < 2:
            raise ConfigError("MAX_HISTORY_MESSAGES debe ser al menos 2.")
        if self.rate_limit_per_minute < 1:
            raise ConfigError("RATE_LIMIT_PER_MINUTE debe ser al menos 1.")
        if self.window_width < 320 or self.window_height < 320:
            raise ConfigError("La ventana no puede medir menos de 320x320.")
        if self.ollama_temperature is not None and not (
            0.0 <= self.ollama_temperature <= 2.0
        ):
            raise ConfigError("OLLAMA_TEMPERATURE debe estar entre 0.0 y 2.0.")
        if self.ollama_timeout <= 0:
            raise ConfigError("OLLAMA_TIMEOUT debe ser positivo.")
        if self.host not in ("127.0.0.1", "localhost"):
            raise ConfigError(
                "BRIDGE_HOST debe ser 127.0.0.1: el puente nunca debe "
                "exponerse a la red local ni a Internet."
            )


def _bool(raw: str, name: str) -> bool:
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{name} debe ser 0/1 o true/false (recibido: {raw!r}).")


def _int(raw: str, name: str) -> int:
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} debe ser un entero (recibido: {raw!r}).") from exc


def _float(raw: str, name: str) -> float:
    try:
        return float(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} debe ser un número (recibido: {raw!r}).") from exc


def _origins(raw: str) -> List[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _extra_options(raw: str, name: str) -> Dict[str, Any]:
    """Parsea OLLAMA_EXTRA_OPTIONS (JSON de opciones para /api/chat).

    Vacío → sin opciones extra. JSON inválido → error descriptivo.
    """
    raw = raw.strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{name} debe ser un objeto JSON válido "
            f'(ej. {{"num_ctx":8192}}): {exc.msg} en la posición {exc.pos}.'
        ) from exc
    if not isinstance(parsed, dict):
        raise ConfigError(f'{name} debe ser un objeto JSON (ej. {{"num_ctx":8192}}).')
    clean: Dict[str, Any] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not key.strip():
            raise ConfigError(
                f"{name}: las claves deben ser nombres de opción válidos."
            )
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ConfigError(
                f"{name}: el valor de '{key}' debe ser número o texto "
                "(los parámetros de Ollama no aceptan objetos ni listas)."
            )
        clean[key.strip()] = value
    return clean


def load_config(env_file: Optional[Path] = None) -> Config:
    """Construye la configuración desde ``.env`` + variables de entorno.

    Sin efectos secundarios: el archivo se lee con ``dotenv_values`` (no se
    muta ``os.environ``). Las variables de entorno del proceso tienen
    prioridad sobre el .env.
    """
    path = Path(env_file) if env_file else DEFAULT_ENV_FILE
    file_values: Dict[str, str] = {}
    if path.exists():
        file_values = {
            key: value
            for key, value in dotenv_values(dotenv_path=path).items()
            if value is not None
        }
    env = {**file_values, **os.environ}
    cfg = Config(
        host=env.get("BRIDGE_HOST", Config.host).strip(),
        port=_int(env.get("BRIDGE_PORT", str(Config.port)), "BRIDGE_PORT"),
        auth_required=_bool(env.get("AUTH_REQUIRED", "1"), "AUTH_REQUIRED"),
        token=env.get("BRIDGE_TOKEN", "").strip(),
        allowed_origins=_origins(env.get("ALLOWED_ORIGINS", "chrome-extension://*")),
        max_text_length=_int(
            env.get("MAX_TEXT_LENGTH", str(Config.max_text_length)), "MAX_TEXT_LENGTH"
        ),
        rate_limit_per_minute=_int(
            env.get("RATE_LIMIT_PER_MINUTE", str(Config.rate_limit_per_minute)),
            "RATE_LIMIT_PER_MINUTE",
        ),
        ollama_host=env.get("OLLAMA_HOST", Config.ollama_host).strip().rstrip("/"),
        ollama_model=env.get("OLLAMA_MODEL", Config.ollama_model).strip(),
        ollama_system_prompt=env.get(
            "OLLAMA_SYSTEM_PROMPT", Config.ollama_system_prompt
        ),
        ollama_temperature=(
            None
            if env.get("OLLAMA_TEMPERATURE", "").strip() == ""
            else _float(env.get("OLLAMA_TEMPERATURE", ""), "OLLAMA_TEMPERATURE")
        ),
        ollama_keep_alive=env.get(
            "OLLAMA_KEEP_ALIVE", Config.ollama_keep_alive
        ).strip(),
        ollama_timeout=_float(
            env.get("OLLAMA_TIMEOUT", str(Config.ollama_timeout)), "OLLAMA_TIMEOUT"
        ),
        ollama_think=(
            None
            if env.get("OLLAMA_THINK", "").strip() == ""
            else _bool(env.get("OLLAMA_THINK", ""), "OLLAMA_THINK")
        ),
        ollama_extra_options=_extra_options(
            env.get("OLLAMA_EXTRA_OPTIONS", ""), "OLLAMA_EXTRA_OPTIONS"
        ),
        max_history_messages=_int(
            env.get("MAX_HISTORY_MESSAGES", str(Config.max_history_messages)),
            "MAX_HISTORY_MESSAGES",
        ),
        window_width=_int(
            env.get("WINDOW_WIDTH", str(Config.window_width)), "WINDOW_WIDTH"
        ),
        window_height=_int(
            env.get("WINDOW_HEIGHT", str(Config.window_height)), "WINDOW_HEIGHT"
        ),
        window_opacity=_int(
            env.get("WINDOW_OPACITY", str(Config.window_opacity)), "WINDOW_OPACITY"
        ),
        always_on_top=_bool(env.get("ALWAYS_ON_TOP", "1"), "ALWAYS_ON_TOP"),
        hover_scroll_hook=_bool(env.get("HOVER_SCROLL_HOOK", "1"), "HOVER_SCROLL_HOOK"),
        ollama_autostart=_bool(env.get("OLLAMA_AUTOSTART", "1"), "OLLAMA_AUTOSTART"),
        online_catalog=_bool(env.get("ONLINE_CATALOG", "1"), "ONLINE_CATALOG"),
        log_level=env.get("LOG_LEVEL", Config.log_level).strip().upper(),
        log_privacy=_bool(env.get("LOG_PRIVACY", "1"), "LOG_PRIVACY"),
    )
    if cfg.window_opacity < 30 or cfg.window_opacity > 100:
        raise ConfigError("WINDOW_OPACITY debe estar entre 30 y 100.")
    if cfg.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ConfigError(f"LOG_LEVEL inválido: {cfg.log_level}")
    cfg.validate()
    return cfg
