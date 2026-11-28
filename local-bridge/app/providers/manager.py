"""Gestor de conexiones de proveedores: keyring + estado sin secretos.

Seguridad:
  * Las claves conectadas desde la app viven en el keyring del sistema
    (Windows Credential Manager). ``data/providers.json`` guarda solo
    estado no sensible (fecha de conexión, nº de modelos vistos).
  * Las copias manuales en ``.env`` se migran sin borrarse; la purga de
    secretos las retira.
  * Los errores/mensajes jamás incluyen la clave.
  * La validación se hace con ``GET /models`` (no consume tokens de chat).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import paths
from .base import ProviderError
from .openai_compat import OpenAICompatClient
from .presets import PRESETS, SPEECH_ONLY_PROVIDER_IDS, preset

log = logging.getLogger("unnexor.providers")

SERVICE = "UNnexor"
# Servicio usado por las versiones anteriores al cambio de nombre: las
# claves se copian a SERVICE la primera vez que se leen (sin borrarlas).
LEGACY_SERVICE = "Extdator"
ACCOUNT_PREFIX = "provider-key"
CONFIG_FILE = paths.providers_file()
CONF_LOCK = threading.Lock()


# --------------------------------------------------------------------- #
# Almacén de estado (SIN secretos)
# --------------------------------------------------------------------- #
def _read_config() -> Dict[str, Any]:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("providers"), dict):
            return data
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("providers.json corrupto o ilegible (%s); se repara.", exc)
        try:
            CONFIG_FILE.with_suffix(".json.bak").write_bytes(CONFIG_FILE.read_bytes())
        except OSError:
            pass
    return {"version": 1, "providers": {}}


def _write_config(data: Dict[str, Any]) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    tmp.replace(CONFIG_FILE)


# --------------------------------------------------------------------- #
# Keyring (lazy: permite tests con stub)
# --------------------------------------------------------------------- #
def _account(provider_id: str) -> str:
    return f"{ACCOUNT_PREFIX}:{provider_id}"


def store_key(provider_id: str, key: str) -> None:
    import keyring

    keyring.set_password(SERVICE, _account(provider_id), key)


def get_key(provider_id: str) -> Optional[str]:
    import keyring

    account = _account(provider_id)
    value = keyring.get_password(SERVICE, account)
    if value:
        return value
    try:
        legacy = keyring.get_password(LEGACY_SERVICE, account)
    except Exception:
        legacy = None
    if legacy:
        try:
            keyring.set_password(SERVICE, account, legacy)
            log.info("Credencial de '%s' migrada desde Extdator.", provider_id)
        except Exception as exc:  # se sigue usando la copia antigua
            log.warning("No se pudo migrar la credencial de '%s': %s", provider_id, exc)
    return legacy or value


def delete_key(provider_id: str) -> None:
    import keyring

    for service in (SERVICE, LEGACY_SERVICE):
        try:
            keyring.delete_password(service, _account(provider_id))
        except Exception:  # entry no existe en algunos backends
            pass


def purge_all_keys() -> List[str]:
    """Elimina las claves de todos los proveedores conocidos y su estado.

    Se usa antes de una desinstalación completa y desde
    ``UNnexor.exe --purge-secrets``. Incluye presets aunque no figuren en
    ``providers.json`` para retirar credenciales huérfanas. No crea el
    directorio de datos si no existe y nunca registra las claves.
    """
    known = (
        {item["id"] for item in PRESETS} | {"custom"} | set(SPEECH_ONLY_PROVIDER_IDS)
    )
    with CONF_LOCK:
        known.update(_read_config()["providers"].keys())
    for provider_id in sorted(known):
        delete_key(provider_id)
    with CONF_LOCK:
        if CONFIG_FILE.exists():
            _write_config({"version": 1, "providers": {}})
    log.info("Credenciales de proveedores eliminadas (%d revisadas).", len(known))
    return sorted(known)


# --------------------------------------------------------------------- #
# API pública del gestor
# --------------------------------------------------------------------- #
def connected_providers() -> List[str]:
    """IDs de proveedores con conexión registrada (sin probar la clave)."""
    with CONF_LOCK:
        return sorted(_read_config()["providers"].keys())


def provider_state(provider_id: str) -> Dict[str, Any]:
    """Estado registrado: connected_at, model_count, preset."""
    with CONF_LOCK:
        entry = _read_config()["providers"].get(provider_id, {})
    info = dict(entry)
    info["connected"] = bool(entry) and get_key(provider_id) is not None
    info["preset"] = preset(provider_id)
    return info


def connect(
    provider_id: str,
    key: str,
    timeout: float = 20.0,
    base_url: Optional[str] = None,
    label: str = "",
) -> Dict[str, Any]:
    """Valida la clave consultando el catálogo y guarda la conexión.

    ``base_url``/``label`` se usan para el proveedor "custom" (OpenAI-
    compatible configurable por el usuario). Lanza ``ProviderError`` con
    mensajes claros si la clave es inválida o no hay red. Devuelve el
    resumen de la conexión (nº de modelos).
    """
    data = preset(provider_id)
    if data is None and provider_id != "custom":
        raise ProviderError(f"Proveedor desconocido: '{provider_id}'.")
    if provider_id == "custom":
        if not base_url or not base_url.startswith(("http://", "https://")):
            raise ProviderError(
                "Introduce la URL base del endpoint (p. ej. "
                "https://mi-servidor/v1).",
                "general",
            )
        resolved_base = base_url
        resolved_label = label or "Personalizado"
    else:
        resolved_base = data["base_url"]  # type: ignore[index]
        resolved_label = data["label"]  # type: ignore[index]
    if not key.strip():
        raise ProviderError("Introduce la clave API del proveedor.", "auth")
    protocol = (data or {}).get("protocol", "openai")
    client = _make_client(protocol, provider_id, resolved_base, key.strip(), timeout)
    try:
        result = client.test_connection()
    finally:
        client.close()

    store_key(provider_id, key.strip())
    with CONF_LOCK:
        config = _read_config()
        config["providers"][provider_id] = {
            "connected_at": datetime.now(timezone.utc).isoformat(),
            "model_count": result["model_count"],
            "label": resolved_label,
            "base_url": resolved_base,
        }
        _write_config(config)
    log.info(
        "Proveedor '%s' conectado (%d modelos).", provider_id, result["model_count"]
    )
    return result


def disconnect(provider_id: str) -> None:
    """Elimina credenciales y registro (no se puede deshacer)."""
    delete_key(provider_id)
    with CONF_LOCK:
        config = _read_config()
        config["providers"].pop(provider_id, None)
        _write_config(config)
    log.info("Proveedor '%s' desconectado.", provider_id)


class DeepgramAccountClient:
    """Solo valida claves de Deepgram (``GET /v1/projects``): sin coste de
    transcripción. La voz se gestiona en ``app/stt/deepgram_engine.py``."""

    def __init__(self, base_url: str, key: str, timeout: float = 20.0) -> None:
        import httpx

        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Token {key}"},
            timeout=timeout,
        )

    def test_connection(self) -> Dict[str, Any]:
        import httpx

        try:
            response = self._client.get("projects")
        except httpx.HTTPError as exc:
            raise ProviderError(
                "No se pudo conectar con Deepgram (revisa la conexión).", "network"
            ) from exc
        if response.status_code in (401, 403):
            raise ProviderError("Clave de Deepgram inválida o sin permisos.", "auth")
        if response.status_code != 200:
            raise ProviderError(
                f"Deepgram devolvió HTTP {response.status_code} al validar la clave.",
                "general",
            )
        projects = response.json().get("projects") or []
        return {"ok": True, "model_count": 0, "projects": len(projects), "sample": []}

    def close(self) -> None:
        self._client.close()


def _make_client(
    protocol: str, provider_id: str, base_url: str, key: str, timeout: float
) -> Any:
    if protocol == "anthropic":
        from .anthropic import AnthropicClient

        return AnthropicClient(key, base_url=base_url, timeout=timeout)
    if protocol == "deepgram":
        return DeepgramAccountClient(base_url, key, timeout=timeout)
    return OpenAICompatClient(provider_id, base_url, key, timeout=timeout)


def get_client_for(provider_id: str, timeout: float = 120.0) -> Any:
    """Cliente de chat listo para usar con la clave del keyring.

    Devuelve ``OpenAICompatClient`` o ``AnthropicClient`` (misma interfaz
    ``list_models``/``chat_stream``). Los proveedores solo de voz no tienen
    cliente de chat.
    """
    key = get_key(provider_id)
    if not key:
        raise ProviderError(
            "No hay clave guardada para este proveedor. Conéctalo desde "
            "'Proveedores'.",
            "auth",
        )
    if provider_id == "custom":
        state = provider_state(provider_id)
        base_url = state.get("base_url")
        if not base_url:
            raise ProviderError(
                "El proveedor personalizado no tiene URL base guardada.",
                "general",
            )
        return OpenAICompatClient(provider_id, base_url, key, timeout=timeout)
    data = preset(provider_id)
    if data is None:
        raise ProviderError(f"Proveedor desconocido: '{provider_id}'.")
    if "chat" not in data.get("capabilities", ["chat"]):
        raise ProviderError(
            f"{data['label']} solo ofrece reconocimiento de voz; elígelo en el "
            "copiloto como motor de voz.",
            "general",
        )
    return _make_client(
        data.get("protocol", "openai"), provider_id, data["base_url"], key, timeout
    )


def available_presets() -> List[Dict[str, Any]]:
    """Catálogo de presets con el estado de conexión de cada uno."""
    out: List[Dict[str, Any]] = []
    for item in PRESETS:
        entry = dict(item)
        state = provider_state(item["id"])
        entry["connected"] = state.get("connected", False)
        entry["connected_at"] = state.get("connected_at", "")
        entry["model_count"] = state.get("model_count", 0)
        out.append(entry)
    return out


def migrate_from_env(env_path: Path) -> List[str]:
    """Migración segura desde .env: claves *_API_KEY → keyring.

    Solo migra claves de presets conocidos que NO estén ya conectadas.
    Devuelve los IDs migrados (la clave se guarda en el keyring y nunca
    se escribe de vuelta ni se loguea). Si no hay nada que migrar, [].
    """
    migrated: List[str] = []
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return migrated
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        raw_name, _, value = line.partition("=")
        name = raw_name.strip().upper()
        value = value.strip().strip('"').strip("'")
        if not value:
            continue
        # OPENAI_API_KEY → openai · GROQ_API_KEY → groq · XAI_API_KEY → xai
        if not name.endswith("_API_KEY"):
            continue
        provider_id = name[: -len("_API_KEY")].lower()
        if provider_id == "gemini":
            provider_id = "gemini"
        if preset(provider_id) is None:
            continue
        if provider_id in connected_providers() or get_key(provider_id):
            continue
        try:
            connect(provider_id, value)
            migrated.append(provider_id)
            log.info("Clave de '%s' migrada desde .env al keyring.", provider_id)
        except ProviderError as exc:
            log.warning(
                "No se pudo migrar la clave de '%s' (queda en .env): %s",
                provider_id,
                exc.message,
            )
    return migrated
