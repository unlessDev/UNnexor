"""Paquete de diagnóstico saneado para soporte.

Genera un ZIP con un resumen legible, comprobaciones locales y los logs
redactados. No incluye el token del puente, claves API, ``.env``, historial
de conversación ni capturas. La redacción es defensiva: elimina secretos
conocidos y patrones habituales (tokens, claves, correos, rutas de usuario,
IDs de extensión y parámetros de URL) antes de escribir nada en el archivo.

El módulo no depende de Qt para poder probarse y reutilizarse sin interfaz.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote, quote_plus

from . import __version__, paths
from .config import Config
from .ollama_client import OllamaClient, OllamaError

log = logging.getLogger("extdator.diagnostics")

ARCHIVE_FORMAT = 1
MAX_LOG_FILES = 4
MAX_LOG_BYTES_PER_FILE = 200_000
MAX_TOTAL_LOG_BYTES = 600_000
MAX_TEXT_LINE = 2_000
MAX_MODELS_LISTED = 40

_SECRET_ENV_MARKERS = ("TOKEN", "API_KEY", "APIKEY", "SECRET", "PASSWORD")

_REDACTION_MARK = "[REDACTADO]"
_CONTENT_MARK = "[ORIGEN Y CONTENIDO OMITIDOS]"
_IMAGE_ORIGIN_MARK = "[ORIGEN OMITIDO]"

# El orden importa: primero credenciales explícitas y luego patrones de
# claves, rutas y parámetros. Todo se aplica antes de escribir el archivo.
_REDACTION_PATTERNS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?"
            r"-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[CLAVE PRIVADA REDACTADA]",
    ),
    (re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [REDACTADO]"),
    (
        re.compile(
            r"(?i)\b(BRIDGE_TOKEN|[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD))"
            r"\s*=\s*([^\s#]+)"
        ),
        r"\1=[REDACTADO]",
    ),
    (
        re.compile(
            r"""(?i)(["']?(?:token|api[_-]?key|secret|password|authorization)"""
            r"""["']?\s*[:=]\s*["']?)([^"'\s,;}]{4,})"""
        ),
        r"\1[REDACTADO]",
    ),
    (re.compile(r"(?i)\b(cookie|set-cookie)\s*:\s*[^\n]+"), r"\1: [REDACTADO]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"), "[CLAVE REDACTADA]"),
    (re.compile(r"\bgsk_[A-Za-z0-9_-]{8,}\b"), "[CLAVE REDACTADA]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"), "[CLAVE REDACTADA]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[CLAVE REDACTADA]"),
    (
        re.compile(r"(?i)\bchrome-extension://[a-p]{32}\b"),
        "chrome-extension://[ID REDACTADO]",
    ),
    (
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "[CORREO REDACTADO]",
    ),
    (re.compile(r"(?i)(https?://)[^/@\s]+@"), r"\1[CREDENCIALES]@"),
    (re.compile(r"(https?://[^\s?#\"']+)\?[^\s\"']+"), r"\1?[PARÁMETROS REDACTADOS]"),
    (re.compile(r"(?i)([A-Z]:\\Users\\)[^\\\s]+"), r"\1[USUARIO]"),
    (re.compile(r"(?i)([A-Z]:\\\\Users\\\\)[^\\\s]+"), r"\1[USUARIO]"),
    (re.compile(r"(?i)(/home/)[^/\s]+"), r"\1[USUARIO]"),
    (re.compile(r"(?i)(/Users/)[^/\s]+"), r"\1[USUARIO]"),
    (re.compile(r"(?i)(Selección aceptada de )[^\n]*"), r"\1" + _CONTENT_MARK),
    (
        re.compile(r"(?i)(Imagen aceptada \(\d+ b64 chars\) de )[^\n]*"),
        r"\1" + _IMAGE_ORIGIN_MARK,
    ),
)


def redact_text(text: str, secrets: Iterable[str] = ()) -> str:
    """Redacta secretos conocidos y patrones sensibles de un texto."""
    if not text:
        return ""

    result = str(text)
    variants = set()
    for secret in secrets:
        value = str(secret or "")
        if len(value) < 6 or any(ch.isspace() for ch in value):
            continue
        variants.add(value)
        variants.add(quote(value, safe=""))
        variants.add(quote_plus(value, safe=""))
    for value in sorted(variants, key=len, reverse=True):
        if value:
            result = result.replace(value, _REDACTION_MARK)

    for pattern, replacement in _REDACTION_PATTERNS:
        result = pattern.sub(replacement, result)

    lines = []
    for line in result.splitlines():
        if len(line) > MAX_TEXT_LINE:
            line = line[:MAX_TEXT_LINE] + " … [línea truncada]"
        lines.append(line)
    trailing = "\n" if result.endswith("\n") else ""
    return "\n".join(lines) + trailing


def default_archive_name(now: Optional[datetime] = None) -> str:
    """Nombre de archivo por defecto para el ZIP de diagnóstico."""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    return f"UNnexor-diagnostico-{stamp}.zip"


def ensure_zip_suffix(path: Path | str) -> Path:
    """Añade ``.zip`` si el usuario no lo escribió (sin cambiar otras extensiones)."""
    target = Path(path)
    if target.suffix.lower() == ".zip":
        return target
    return Path(str(target) + ".zip")


def _known_secrets(
    cfg: Config, environ: Optional[Mapping[str, str]] = None
) -> Tuple[str, ...]:
    """Valores que deben desaparecer del paquete (solo se usan en memoria)."""
    values = set()
    token = str(getattr(cfg, "token", "") or "")
    if 6 <= len(token) <= 4096 and not any(ch.isspace() for ch in token):
        values.add(token)
    for key, raw in (environ or os.environ).items():
        upper = str(key).upper()
        if not any(marker in upper for marker in _SECRET_ENV_MARKERS):
            continue
        value = str(raw or "")
        if (
            8 <= len(value) <= 4096
            and not any(ch.isspace() for ch in value)
            and value not in ("true", "false", "none", "null")
        ):
            values.add(value)
    return tuple(sorted(values, key=len, reverse=True))


def _display_path(path: Path) -> str:
    text = str(path)
    try:
        home = str(Path.home())
    except OSError:
        home = ""
    if home and text.lower().startswith(home.lower()):
        text = "%USERPROFILE%" + text[len(home) :]
    return redact_text(text)


def _is_writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".extdator-diag-", dir=directory):
            pass
        return True
    except OSError:
        return False


def _settings_summary() -> Dict[str, Any]:
    path = paths.settings_file()
    info: Dict[str, Any] = {
        "file": _display_path(path),
        "present": path.is_file(),
        "valid_json": None,
        "keys": [],
    }
    if not path.is_file():
        return info
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        info["valid_json"] = False
        return info
    info["valid_json"] = True
    if isinstance(data, dict):
        info["keys"] = sorted(str(key) for key in data)[:40]
    return info


def _providers_summary() -> Dict[str, Any]:
    path = paths.providers_file()
    info: Dict[str, Any] = {
        "file": _display_path(path),
        "present": path.is_file(),
        "connected_count": 0,
    }
    if not path.is_file():
        return info
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        info["valid_json"] = False
        return info
    providers = data.get("providers") if isinstance(data, dict) else None
    info["valid_json"] = True
    if isinstance(providers, dict):
        info["connected_count"] = len(providers)
    return info


def _model_installed(installed: Iterable[str], wanted: str) -> bool:
    wanted = (wanted or "").strip()
    if not wanted:
        return False
    base = wanted.split(":", 1)[0]
    return any(name == wanted or name.split(":", 1)[0] == base for name in installed)


def _ollama_summary(
    cfg: Config, ollama: Optional[OllamaClient], check_ollama: bool
) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "checked": False,
        "reachable": None,
        "detail": "",
        "models_count": None,
        "models": [],
        "models_error": "",
        "configured_model": cfg.ollama_model,
        "configured_model_installed": None,
    }
    if not check_ollama or ollama is None:
        return info
    info["checked"] = True
    try:
        reachable, detail = ollama.is_reachable(timeout=2.0)
    except Exception as exc:
        info["reachable"] = False
        info["detail"] = f"Error inesperado al comprobar Ollama: {exc}"
        return info
    info["reachable"] = bool(reachable)
    info["detail"] = str(detail or "")
    if not reachable:
        return info
    try:
        models = ollama.list_models()
    except OllamaError as exc:
        info["models_error"] = exc.message
        return info
    except Exception as exc:
        info["models_error"] = f"Error inesperado al listar modelos: {exc}"
        return info
    cleaned = sorted({str(name) for name in models if str(name).strip()})
    info["models_count"] = len(cleaned)
    info["models"] = cleaned[:MAX_MODELS_LISTED]
    info["configured_model_installed"] = _model_installed(cleaned, cfg.ollama_model)
    return info


def _safe_call(func, default=None):
    try:
        return func()
    except Exception:
        return default


def _runtime_summary(state: Any, bus: Any) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "available": state is not None,
        "generating": None,
        "pending": None,
        "history_messages": None,
        "uptime_seconds": None,
        "model": "",
        "provider": "ollama",
        "remote_model": "",
    }
    if state is not None:
        info["generating"] = bool(_safe_call(state.is_generating, False))
        info["history_messages"] = int(_safe_call(state.history_count, 0) or 0)
        info["uptime_seconds"] = int(_safe_call(state.uptime_seconds, 0) or 0)
        info["model"] = str(_safe_call(state.model_name, "") or "")
        provider = _safe_call(state.provider, ("ollama", ""))
        if isinstance(provider, (tuple, list)) and len(provider) == 2:
            info["provider"] = str(provider[0] or "ollama")
            info["remote_model"] = str(provider[1] or "")
    if bus is not None:
        info["pending"] = int(_safe_call(bus.qsize, 0) or 0)
    return info


def _build_checks(
    cfg: Config,
    data_writable: bool,
    ollama_info: Mapping[str, Any],
    log_count: int,
) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []

    def add(check_id: str, status: str, detail: str) -> None:
        checks.append({"id": check_id, "status": status, "detail": detail})

    try:
        cfg.validate()
        add("config", "ok", "Configuración válida.")
    except Exception as exc:
        add("config", "error", f"Configuración inválida: {exc}")

    if cfg.host in ("127.0.0.1", "localhost"):
        add("loopback", "ok", f"El servidor escucha solo en {cfg.host}.")
    else:
        add(
            "loopback",
            "error",
            f"El servidor no está limitado a loopback ({cfg.host}).",
        )

    if cfg.auth_required and cfg.token:
        add("auth", "ok", "Autenticación activa y token configurado.")
    elif cfg.auth_required:
        add("auth", "error", "Autenticación activa pero falta el token del puente.")
    else:
        add(
            "auth",
            "warn",
            "La autenticación está desactivada; no se recomienda en uso normal.",
        )

    if cfg.log_privacy:
        add("log_privacy", "ok", "Los logs no registran el contenido de selecciones.")
    else:
        add(
            "log_privacy",
            "warn",
            "LOG_PRIVACY=0: los logs pueden contener contenido de usuario. "
            "El exportador elimina las líneas conocidas, pero revisa el ZIP.",
        )

    if data_writable:
        add("data_dir", "ok", "El directorio de datos permite escritura.")
    else:
        add("data_dir", "error", "No se puede escribir en el directorio de datos.")

    if not ollama_info.get("checked"):
        add("ollama", "warn", "No se comprobó Ollama en esta exportación.")
    elif not ollama_info.get("reachable"):
        add(
            "ollama",
            "warn",
            "Ollama no responde: " + str(ollama_info.get("detail") or "sin detalle"),
        )
    else:
        add("ollama", "ok", "Ollama responde: " + str(ollama_info.get("detail") or ""))
        if ollama_info.get("models_error"):
            add(
                "model",
                "warn",
                "No se pudieron listar los modelos: "
                + str(ollama_info["models_error"]),
            )
        elif ollama_info.get("models_count") is not None:
            if not str(ollama_info.get("configured_model") or "").strip():
                add("model", "warn", "No hay un modelo configurado.")
            elif ollama_info.get("configured_model_installed"):
                add(
                    "model",
                    "ok",
                    f"El modelo '{ollama_info['configured_model']}' está instalado.",
                )
            else:
                add(
                    "model",
                    "warn",
                    f"El modelo '{ollama_info['configured_model']}' no está instalado.",
                )

    if log_count:
        add("logs", "ok", f"{log_count} archivo(s) de log encontrados.")
    else:
        add("logs", "warn", "No se encontraron logs en el directorio de datos.")
    return checks


def collect_snapshot(
    cfg: Config,
    *,
    state: Any = None,
    bus: Any = None,
    pairing: Any = None,
    ollama: Optional[OllamaClient] = None,
    environ: Optional[Mapping[str, str]] = None,
    now: Optional[datetime] = None,
    check_ollama: bool = True,
    log_paths: Optional[Sequence[Path]] = None,
    log_dir: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Recopila la instantánea no sensible que se guarda en ``resumen.json``."""
    data_dir = paths.data_dir()
    logs_dir = Path(log_dir) if log_dir is not None else paths.logs_dir()
    if log_paths is None:
        log_paths = _log_paths(logs_dir)
    data_writable = _is_writable(data_dir)
    try:
        free_mb = shutil.disk_usage(data_dir).free // (1024 * 1024)
    except OSError:
        free_mb = None

    ollama_info = _ollama_summary(cfg, ollama, check_ollama)
    runtime = _runtime_summary(state, bus)
    pairing_info: Dict[str, Any] = {"available": False, "confirmed": None}
    if pairing is not None and bool(getattr(cfg, "token", "")):
        pairing_info["available"] = True
        pairing_info["confirmed"] = bool(_safe_call(pairing.is_confirmed, False))

    origins = list(cfg.allowed_origins or [])
    config_info = {
        "host": cfg.host,
        "port": cfg.port,
        "auth_required": bool(cfg.auth_required),
        "token_configured": bool(cfg.token),
        "allowed_origins_count": len(origins),
        "allowed_origins_wildcard": any("*" in origin for origin in origins),
        "max_text_length": cfg.max_text_length,
        "rate_limit_per_minute": cfg.rate_limit_per_minute,
        "max_history_messages": cfg.max_history_messages,
        "ollama_host": redact_text(str(cfg.ollama_host)),
        "ollama_model": cfg.ollama_model,
        "ollama_timeout": cfg.ollama_timeout,
        "ollama_keep_alive": cfg.ollama_keep_alive,
        "ollama_temperature": cfg.ollama_temperature,
        "ollama_think": cfg.ollama_think,
        "ollama_extra_option_keys": sorted(
            str(key) for key in cfg.ollama_extra_options
        ),
        "log_level": cfg.log_level,
        "log_privacy": bool(cfg.log_privacy),
        "window_opacity": cfg.window_opacity,
        "always_on_top": bool(cfg.always_on_top),
        "hover_scroll_hook": bool(cfg.hover_scroll_hook),
    }

    snapshot: Dict[str, Any] = {
        "archive_format": ARCHIVE_FORMAT,
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "app": {
            "name": "UNnexor",
            "version": __version__,
            "frozen": bool(paths.is_frozen()),
            "python": platform.python_version(),
            "bits": "64-bit" if sys.maxsize > 2**32 else "32-bit",
        },
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        "paths": {
            "data_dir": _display_path(data_dir),
            "logs_dir": _display_path(logs_dir),
            "env_file": _display_path(paths.env_file()),
            "settings_file": _display_path(paths.settings_file()),
            "data_writable": data_writable,
            "disk_free_mb": free_mb,
        },
        "config": config_info,
        "runtime": runtime,
        "ollama": ollama_info,
        "pairing": pairing_info,
        "settings": _settings_summary(),
        "providers": _providers_summary(),
        "logs": {
            "directory": _display_path(logs_dir),
            "files_found": len(log_paths),
            "files_included": [],
            "content_lines_omitted": 0,
            "redacted": True,
        },
        "privacy": {
            "includes_token": False,
            "includes_api_keys": False,
            "includes_conversation": False,
            "includes_screenshots": False,
            "log_redaction": (
                "tokens, claves, correos, rutas de usuario, IDs de extensión "
                "y parámetros de URL"
            ),
        },
    }
    snapshot["checks"] = _build_checks(cfg, data_writable, ollama_info, len(log_paths))
    return snapshot


def _log_paths(log_dir: Path) -> list[Path]:
    root = Path(log_dir)
    if not root.is_dir():
        return []
    found = []
    try:
        for path in root.iterdir():
            if path.is_file() and path.name.startswith("bridge.log"):
                found.append(path)
    except OSError:
        return []

    def mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    found.sort(key=mtime, reverse=True)
    return found[:MAX_LOG_FILES]


def _read_tail(path: Path, limit: int) -> str:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        if size > limit:
            handle.seek(size - limit)
        else:
            handle.seek(0)
        data = handle.read(limit)
    text = data.decode("utf-8", errors="replace")
    if size > limit:
        text = "[… inicio del archivo omitido …]\n" + text
    return text


def _sanitize_log_text(text: str, secrets: Sequence[str]) -> Tuple[str, int]:
    cleaned = redact_text(text, secrets)
    omitted = cleaned.count(_CONTENT_MARK) + cleaned.count(_IMAGE_ORIGIN_MARK)
    return cleaned, omitted


def _collect_logs(
    log_paths: Sequence[Path], secrets: Sequence[str]
) -> Tuple[list[tuple[str, str]], int]:
    entries: list[tuple[str, str]] = []
    omitted = 0
    total = 0
    for path in log_paths:
        remaining = MAX_TOTAL_LOG_BYTES - total
        if remaining <= 0:
            break
        try:
            raw = _read_tail(path, min(MAX_LOG_BYTES_PER_FILE, remaining))
        except OSError as exc:
            log.warning("No se pudo leer el log %s: %s", path.name, exc)
            continue
        text, count = _sanitize_log_text(raw, secrets)
        omitted += count
        if not text.strip():
            continue
        entries.append((f"logs/{path.name}", text))
        total += len(text.encode("utf-8"))
    return entries, omitted


def _sanitize_value(value: Any, secrets: Sequence[str]) -> Any:
    if isinstance(value, str):
        return redact_text(value, secrets)
    if isinstance(value, dict):
        return {
            redact_text(str(key), secrets): _sanitize_value(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item, secrets) for item in value]
    return value


def _yes_no(value: Any) -> str:
    """Triestado legible: sí / no / desconocido."""
    if value is None:
        return "desconocido"
    return "sí" if value else "no"


def _measure(value: Any, suffix: str = "") -> str:
    if value is None:
        return "no disponible"
    return f"{value}{suffix}"


def render_report(snapshot: Mapping[str, Any]) -> str:
    """Informe legible que acompaña al JSON dentro del ZIP."""
    app = snapshot.get("app", {})
    os_info = snapshot.get("os", {})
    config = snapshot.get("config", {})
    runtime = snapshot.get("runtime", {})
    ollama = snapshot.get("ollama", {})
    pairing = snapshot.get("pairing", {})
    logs = snapshot.get("logs", {})
    checks = snapshot.get("checks", [])
    settings = snapshot.get("settings", {})
    providers = snapshot.get("providers", {})
    path_info = snapshot.get("paths", {})

    lines = [
        "UNnexor — Diagnóstico para soporte",
        "=" * 40,
        f"Generado: {snapshot.get('generated_at', '')}",
        f"Versión: {app.get('version', '')} "
        f"(empaquetado: {'sí' if app.get('frozen') else 'no'})",
        f"Sistema: {os_info.get('system', '')} {os_info.get('release', '')} "
        f"({os_info.get('machine', '')})",
        f"Python: {app.get('python', '')} ({app.get('bits', '')})",
        "",
        "Configuración",
        "-------------",
        f"Servidor: {config.get('host', '')}:{config.get('port', '')}",
        f"Autenticación: {'sí' if config.get('auth_required') else 'no'} "
        f"(token configurado: {'sí' if config.get('token_configured') else 'no'})",
        f"Orígenes permitidos: {config.get('allowed_origins_count', 0)} "
        f"(comodín: {'sí' if config.get('allowed_origins_wildcard') else 'no'})",
        f"Ollama: {config.get('ollama_host', '')}",
        f"Modelo configurado: {config.get('ollama_model', '')}",
        f"Proveedor activo: {runtime.get('provider', 'ollama')}",
        f"Privacidad de logs: {'sí' if config.get('log_privacy') else 'no'}",
        f"Ajustes guardados: {settings.get('keys', []) if settings.get('present') else 'ninguno'}",
        f"Proveedores conectados: {providers.get('connected_count', 0)}",
        "",
        "Datos",
        "-----",
        f"Directorio: {path_info.get('data_dir', '')}",
        f"Escritura: {_yes_no(path_info.get('data_writable'))}",
        f"Espacio libre: {_measure(path_info.get('disk_free_mb'), ' MB')}",
        "",
        "Comprobaciones",
        "--------------",
    ]
    symbols = {"ok": "[OK]", "warn": "[AVISO]", "error": "[ERROR]"}
    for check in checks:
        symbol = symbols.get(str(check.get("status")), "[INFO]")
        lines.append(f"{symbol} {check.get('detail', '')}")
    lines.extend(
        [
            "",
            "Ollama",
            "------",
            f"Comprobado: {_yes_no(ollama.get('checked'))}",
            f"Alcanzable: {_yes_no(ollama.get('reachable'))}",
            f"Detalle: {ollama.get('detail', '') or 'sin detalle'}",
            f"Modelos instalados: {_measure(ollama.get('models_count'))}",
            f"Modelo configurado instalado: "
            f"{_yes_no(ollama.get('configured_model_installed'))}",
            "",
            "Estado en memoria",
            "-----------------",
            f"Modelo: {runtime.get('model', '') or 'sin datos'}",
            f"Generando: {_yes_no(runtime.get('generating'))}",
            f"Pendientes en cola: {_measure(runtime.get('pending'))}",
            f"Historial: {_measure(runtime.get('history_messages'))} mensajes (sin contenido)",
            f"Uptime: {_measure(runtime.get('uptime_seconds'), ' s')}",
            "",
            "Emparejamiento",
            "--------------",
            f"Disponible: {_yes_no(pairing.get('available'))}",
            f"Extensión vinculada: {_yes_no(pairing.get('confirmed'))}",
            "",
            "Logs",
            "----",
            f"Incluidos: {len(logs.get('files_included', []))} "
            f"(encontrados: {logs.get('files_found', 0)})",
            f"Líneas de contenido omitidas: {logs.get('content_lines_omitted', 0)}",
            f"Redactados: {'sí' if logs.get('redacted') else 'no'}",
        ]
    )
    for name in logs.get("files_included", []):
        lines.append(f"  - {name}")
    lines.extend(
        [
            "",
            "Privacidad",
            "----------",
            "Este paquete no incluye token, claves API, .env, historial ni capturas.",
            "Revisa el contenido antes de compartirlo fuera de tu equipo.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_readme(snapshot: Mapping[str, Any]) -> str:
    """Nota de ayuda incluida en la raíz del ZIP."""
    privacy = snapshot.get("privacy", {})
    config = snapshot.get("config", {})
    lines = [
        "LEEME — Diagnóstico de UNnexor",
        "================================",
        "",
        "Este paquete ayuda a diagnosticar un problema de UNnexor.",
        "",
        "Contiene:",
        "- resumen.json: datos técnicos estructurados.",
        "- diagnostico.txt: la misma información en formato legible.",
        "- logs/: registros redactados, si existían.",
        "",
        "No contiene:",
        f"- token del puente: {'sí' if privacy.get('includes_token') else 'no'}",
        f"- claves API: {'sí' if privacy.get('includes_api_keys') else 'no'}",
        f"- historial de conversación: "
        f"{'sí' if privacy.get('includes_conversation') else 'no'}",
        f"- capturas de pantalla: "
        f"{'sí' if privacy.get('includes_screenshots') else 'no'}",
        "- archivo .env ni user_settings.json completos.",
        "",
        "Los logs se redactan automáticamente:",
        f"- {privacy.get('log_redaction', '')}.",
        "- Las líneas de selección o imagen registradas con LOG_PRIVACY=0 se omiten.",
        "",
    ]
    if not config.get("log_privacy"):
        lines.extend(
            [
                "AVISO: LOG_PRIVACY está desactivado. Aunque el exportador elimina",
                "las líneas de contenido conocidas, revisa el ZIP antes de",
                "compartirlo y considera activar LOG_PRIVACY=1.",
                "",
            ]
        )
    lines.extend(
        [
            "Cómo usarlo:",
            "1. Abre diagnostico.txt y revisa el resumen.",
            "2. Adjunta el ZIP a tu informe junto con una descripción del problema.",
            "3. Si algo te parece privado, no lo compartas: elimina ese archivo.",
            "",
        ]
    )
    return "\n".join(lines)


def write_diagnostics_archive(
    target: Path | str,
    cfg: Config,
    *,
    state: Any = None,
    bus: Any = None,
    pairing: Any = None,
    ollama: Optional[OllamaClient] = None,
    log_dir: Optional[Path | str] = None,
    environ: Optional[Mapping[str, str]] = None,
    now: Optional[datetime] = None,
    check_ollama: bool = True,
) -> Path:
    """Escribe el ZIP de diagnóstico de forma atómica y devuelve su ruta."""
    destination = Path(target).expanduser()
    if destination.exists() and destination.is_dir():
        raise IsADirectoryError(f"La ruta de destino es un directorio: {destination}")
    destination = destination.resolve()

    log_root = Path(log_dir) if log_dir is not None else paths.logs_dir()
    log_paths = _log_paths(log_root)
    secrets = _known_secrets(cfg, environ)
    snapshot = collect_snapshot(
        cfg,
        state=state,
        bus=bus,
        pairing=pairing,
        ollama=ollama,
        environ=environ,
        now=now,
        check_ollama=check_ollama,
        log_paths=log_paths,
        log_dir=log_root,
    )
    entries, omitted = _collect_logs(log_paths, secrets)
    snapshot["logs"]["files_included"] = [name for name, _ in entries]
    snapshot["logs"]["content_lines_omitted"] = omitted

    summary = _sanitize_value(snapshot, secrets)
    summary_text = json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    report_text = redact_text(render_report(summary), secrets)
    readme_text = redact_text(render_readme(summary), secrets)

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            archive.writestr("LEEME.txt", readme_text)
            archive.writestr("resumen.json", summary_text)
            archive.writestr("diagnostico.txt", report_text)
            for name, text in entries:
                archive.writestr(name, text)
        os.replace(temporary, destination)
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass
    return destination
