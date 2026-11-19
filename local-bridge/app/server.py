"""Servidor HTTP local del puente (FastAPI + uvicorn).

Responsabilidades (y solo estas):
  * Validar y autenticar las peticiones de la extensión.
  * Publicar los eventos en la cola thread-safe (``Bus``) → GUI/Ollama.
  * Exponer endpoints auxiliares: health, status, models, clear, config.

El hilo del servidor NUNCA bloquea esperando a Ollama: las selecciones se
aceptan (202) y el worker de la GUI procesa la cola en otro hilo.
"""

from __future__ import annotations

import base64
import binascii
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import Config
from .hardware import detect as detect_hardware
from .hardware import gpu_acceleration_active
from .model_evaluator import assess_model, context_warning
from .ollama_client import OllamaClient, OllamaError
from .pairing import PairingManager
from .queue_manager import (
    Bus,
    ClearConversation,
    ConfigUpdate,
    ImageRequest,
    RuntimeState,
    SelectionRequest,
)
from .security import RateLimiter, RequestGuard, origins_to_regex_string
from .settings_store import (
    ALLOWED_KEYS,
    LIVE_KEYS,
    SettingsStore,
    SettingsValidationError,
    restart_required_for,
)

log = logging.getLogger("extdator.server")

# Límite de la imagen base64 en el JSON (≈12 MB codificados → ~9 MB reales).
MAX_IMAGE_B64_LENGTH = 12_000_000
# Firmas de formato aceptadas (Ollama las procesa directamente).
_IMAGE_MAGIC = (b"\x89PNG", b"\xff\xd8\xff")  # PNG, JPEG


# --------------------------------------------------------------------- #
# Modelos de entrada
# --------------------------------------------------------------------- #
class SelectionIn(BaseModel):
    text: str = Field(min_length=1)
    source_url: str = ""
    timestamp: str = ""
    token: Optional[str] = None


class ConfigIn(BaseModel):
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    temperature: Optional[float] = None
    keep_alive: Optional[str] = None


class ImageIn(BaseModel):
    image_b64: str = Field(min_length=64)
    instruction: str = Field(min_length=1, max_length=8000)
    source_url: str = ""
    timestamp: str = ""


class PairConfirmIn(BaseModel):
    code: str = Field(min_length=4, max_length=12)


class SettingsIn(BaseModel):
    changes: Dict[str, Any]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------- #
def create_app(
    cfg: Config,
    bus: Bus,
    state: RuntimeState,
    ollama: OllamaClient,
    settings_store: Optional[SettingsStore] = None,
    pairing: Optional[PairingManager] = None,
) -> FastAPI:
    guard = RequestGuard(
        token=cfg.token,
        allowed_origins=cfg.allowed_origins,
        auth_required=cfg.auth_required,
    )
    limiter = RateLimiter(cfg.rate_limit_per_minute)
    pair_limiter = RateLimiter(5)  # confirmaciones de emparejamiento

    app = FastAPI(
        title="Extdator Bridge",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # CORS limitado a los orígenes autorizados (la extensión). Nunca "*".
    if cfg.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origin_regex=origins_to_regex_string(cfg.allowed_origins),
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Api-Token"],
            allow_credentials=False,
            max_age=600,
        )

    # ------------------------------------------------------------------
    @app.middleware("http")
    async def _log_requests(request: Request, call_next):
        response = await call_next(request)
        # Privacidad: solo método, ruta y estado; jamás el cuerpo.
        log.info("%s %s -> %s", request.method, request.url.path, response.status_code)
        return response

    # ------------------------------------------------------------------
    def _require_origin(request: Request) -> None:
        ok, reason = guard.origin_ok(request.headers.get("origin"))
        if not ok:
            raise HTTPException(status_code=403, detail=reason)

    def _require_auth(request: Request, body_token: Optional[str] = None) -> None:
        header = request.headers.get("authorization") or request.headers.get(
            "x-api-token"
        )
        ok, reason = guard.token_ok(header, body_token)
        if not ok:
            raise HTTPException(status_code=401, detail=reason)

    # ------------------------------------------------------------------
    @app.get("/health")
    def health() -> dict:
        reachable, detail = ollama.is_reachable()
        return {
            "status": "ok",
            "app": "extdator-bridge",
            "version": __version__,
            "ollama": {"reachable": reachable, "detail": detail},
            "model": state.model_name(),
        }

    @app.post("/api/selection")
    def receive_selection(request: Request, payload: SelectionIn) -> JSONResponse:
        _require_origin(request)
        _require_auth(request, payload.token)

        text = payload.text
        if not text.strip():
            raise HTTPException(status_code=422, detail="El texto está vacío.")
        if len(text) > cfg.max_text_length:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Texto demasiado extenso: {len(text)} caracteres "
                    f"(límite {cfg.max_text_length})."
                ),
            )

        client_key = request.client.host if request.client else "local"
        if not limiter.allow(client_key):
            retry = limiter.retry_after(client_key)
            raise HTTPException(
                status_code=429,
                detail="Demasiadas solicitudes. Espera unos segundos.",
                headers={"Retry-After": str(retry)},
            )

        event = SelectionRequest(
            text=text,
            source_url=payload.source_url[:2048],
            received_at=payload.timestamp or _utcnow_iso(),
        )
        if not bus.publish(event):
            raise HTTPException(
                status_code=503,
                detail="La cola está saturada; espera a que termine la generación.",
            )

        if cfg.log_privacy:
            log.info("Selección aceptada: %d caracteres.", len(text))
        else:
            log.info("Selección aceptada de %s: %r", payload.source_url, text[:80])

        return JSONResponse(
            status_code=202,
            content={
                "accepted": True,
                "pending": bus.qsize(),
                "message": "Texto en cola. Ollama lo procesará en breve.",
            },
        )

    @app.get("/api/status")
    def status(request: Request) -> dict:
        _require_origin(request)
        _require_auth(request)
        reachable, detail = ollama.is_reachable()
        return {
            "app": "extdator-bridge",
            "version": __version__,
            "model": state.model_name(),
            "ollama": {"reachable": reachable, "detail": detail},
            "generating": state.is_generating(),
            "pending": bus.qsize(),
            "history_messages": state.history_count(),
            "uptime_seconds": state.uptime_seconds(),
        }

    @app.get("/api/models")
    def models(request: Request) -> dict:
        _require_origin(request)
        _require_auth(request)
        try:
            names = ollama.list_models()
        except OllamaError as exc:
            raise HTTPException(status_code=503, detail=exc.message) from exc
        return {"models": names, "configured": cfg.ollama_model}

    @app.post("/api/clear")
    def clear(request: Request) -> JSONResponse:
        _require_origin(request)
        _require_auth(request)
        state.request_clear()
        bus.publish(ClearConversation())  # el worker también lo recoge al estar libre
        log.info("Petición de limpieza de conversación aceptada.")
        return JSONResponse(
            status_code=202,
            content={
                "accepted": True,
                "message": "Conversación marcada para reinicio.",
            },
        )

    @app.post("/api/config")
    def update_config(request: Request, payload: ConfigIn) -> dict:
        _require_origin(request)
        _require_auth(request)
        changes: dict = {}

        if payload.model is not None:
            model = payload.model.strip()
            if not model:
                raise HTTPException(
                    status_code=422, detail="El modelo no puede estar vacío."
                )
            with state.lock():
                cfg.ollama_model = model
                state.set_model(model)
            changes["model"] = model

        if payload.system_prompt is not None:
            with state.lock():
                cfg.ollama_system_prompt = payload.system_prompt.strip()
            changes["system_prompt"] = cfg.ollama_system_prompt

        if payload.temperature is not None:
            if not (0.0 <= payload.temperature <= 2.0):
                raise HTTPException(
                    status_code=422, detail="temperature debe estar entre 0.0 y 2.0."
                )
            with state.lock():
                cfg.ollama_temperature = payload.temperature
            changes["temperature"] = payload.temperature

        if payload.keep_alive is not None:
            with state.lock():
                cfg.ollama_keep_alive = payload.keep_alive.strip()
            changes["keep_alive"] = cfg.ollama_keep_alive

        if not changes:
            raise HTTPException(status_code=422, detail="No hay cambios que aplicar.")

        bus.publish(ConfigUpdate(changes=changes))
        log.info("Configuración actualizada en caliente: %s", ", ".join(changes))
        return {
            "applied": changes,
            "note": "Los cambios son en memoria; edítalos en .env para hacerlos permanentes.",
        }

    # ------------------------------------------------------------------
    def _require_pairing_origin(request: Request) -> None:
        """El emparejamiento solo acepta origines de extensión (o curl local).

        Es más laxo que el resto por diseño: es el mecanismo que ENTREGA el
        token; se protege con código efímero, límite de intentos y localhost.
        """
        origin = request.headers.get("origin")
        if origin is None:
            return
        if origin.strip().lower().startswith("chrome-extension://"):
            return
        raise HTTPException(
            status_code=403, detail="Origen no autorizado para emparejar."
        )

    def _validate_image_b64(data: str) -> bytes:
        try:
            raw = base64.b64decode(data, validate=False)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail="La imagen no es base64 válido."
            ) from exc
        if len(raw) < 1024:
            raise HTTPException(
                status_code=422, detail="La imagen es demasiado pequeña."
            )
        if len(data) > MAX_IMAGE_B64_LENGTH:
            raise HTTPException(
                status_code=413,
                detail="Imagen demasiado grande (límite ~9 MB).",
            )
        if not raw.startswith(_IMAGE_MAGIC):
            raise HTTPException(
                status_code=415,
                detail="Formato no soportado: usa PNG o JPEG.",
            )
        return raw

    # ------------------------------------------------------------------
    @app.get("/api/hardware")
    def hardware(request: Request) -> dict:
        _require_origin(request)
        _require_auth(request)
        try:
            info = detect_hardware()
        except Exception as exc:  # la detección nunca debe tumbar el servidor
            log.warning("Fallo al detectar hardware: %s", exc)
            raise HTTPException(
                status_code=503, detail=f"No se pudo detectar el hardware: {exc}"
            ) from exc
        info.ollama_gpu_active = None  # placeholder, se rellena abajo
        try:
            from .hardware import gpu_acceleration_active

            info.ollama_gpu_active = gpu_acceleration_active(ollama)
        except Exception:
            pass
        return info.to_dict()

    @app.get("/api/models/eval")
    def models_eval(request: Request) -> dict:
        """Modelos instalados con clasificación de compatibilidad."""
        _require_origin(request)
        _require_auth(request)
        try:
            installed = ollama.installed_models_full()
        except OllamaError as exc:
            raise HTTPException(status_code=503, detail=exc.message) from exc
        info = detect_hardware()
        gpu_active = gpu_acceleration_active(ollama)
        info.ollama_gpu_active = gpu_active
        hw = info.to_dict()
        # Dato de terreno: si Ollama corre 100% en CPU, la VRAM no cuenta.
        if gpu_active is False and hw.get("gpus"):
            hw["gpus"] = []
            hw.setdefault("notes", []).append(
                "Ollama está usando CPU (no la GPU): evaluación en modo solo-RAM."
            )
        disk_free = info.disk_free_gb
        results = []
        for item in installed:
            details = item.get("details") or {}
            assessment = assess_model(
                model_name=item.get("name", ""),
                size_bytes=item.get("size"),
                parameter_size=details.get("parameter_size"),
                hw=hw,
                installed=True,
            )
            results.append(
                {
                    "name": item.get("name", ""),
                    "size_bytes": item.get("size"),
                    "modified_at": item.get("modified_at"),
                    "assessment": assessment.to_dict(),
                }
            )
        return {
            "configured": cfg.ollama_model,
            "hardware": hw,
            "models": results,
        }

    @app.get("/api/settings")
    def get_settings(request: Request) -> dict:
        _require_origin(request)
        _require_auth(request)
        entries = {}
        for key, spec in ALLOWED_KEYS.items():
            value = getattr(cfg, key, None)
            entries[key] = {
                "value": value,
                "overridden": bool(settings_store and key in settings_store.as_dict()),
                "restart": bool(spec.get("restart")),
                "live": key in LIVE_KEYS,
            }
        return {"settings": entries}

    @app.post("/api/settings")
    def post_settings(request: Request, payload: SettingsIn) -> dict:
        _require_origin(request)
        _require_auth(request)
        if not settings_store:
            raise HTTPException(
                status_code=503,
                detail="El almacén de ajustes no está disponible (arranque con .env).",
            )
        try:
            applied = settings_store.update(payload.changes)
        except SettingsValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        for key, value in applied.items():
            setattr(cfg, key, value)
        live_changes = {k: v for k, v in applied.items() if k in LIVE_KEYS}
        if live_changes:
            bus.publish(ConfigUpdate(changes=live_changes))
        log.info("Ajustes actualizados desde la interfaz: %s", ", ".join(applied))
        return {
            "applied": applied,
            "restart_required": restart_required_for(list(applied)),
        }

    @app.post("/api/settings/reset")
    def reset_settings(request: Request) -> dict:
        _require_origin(request)
        _require_auth(request)
        if not settings_store:
            raise HTTPException(status_code=503, detail="Almacén no disponible.")
        removed = settings_store.reset()
        log.info("Ajustes de usuario restaurados (%d claves).", len(removed))
        return {
            "removed": sorted(removed),
            "note": "Reinicia Extdator para recuperar los valores base del .env.",
        }

    @app.post("/api/pair/confirm")
    def pair_confirm(request: Request, payload: PairConfirmIn) -> JSONResponse:
        """Entrega el token a cambio del código efímero del panel."""
        _require_pairing_origin(request)
        client_key = request.client.host if request.client else "local"
        if not pair_limiter.allow(client_key):
            raise HTTPException(
                status_code=429,
                detail="Demasiados intentos. Espera un minuto.",
                headers={"Retry-After": "60"},
            )
        if pairing is None or not cfg.token:
            raise HTTPException(
                status_code=503, detail="El emparejamiento no está disponible."
            )
        token = pairing.confirm(payload.code)
        if token is None:
            raise HTTPException(
                status_code=403,
                detail="Código inválido o caducado. Pide uno nuevo en el panel.",
            )
        log.info("Emparejamiento confirmado (código de un solo uso).")
        return {"paired": True, "token": token}

    @app.post("/api/image")
    def receive_image(request: Request, payload: ImageIn) -> JSONResponse:
        """Captura de pantalla → modelo multimodal (o OCR cuando exista)."""
        _require_origin(request)
        _require_auth(request)
        _validate_image_b64(payload.image_b64)

        client_key = request.client.host if request.client else "local"
        if not limiter.allow(client_key):
            retry = limiter.retry_after(client_key)
            raise HTTPException(
                status_code=429,
                detail="Demasiadas solicitudes. Espera unos segundos.",
                headers={"Retry-After": str(retry)},
            )
        if state.is_generating():
            raise HTTPException(
                status_code=409,
                detail="Hay una generación en curso; espera o deténla antes de enviar una imagen.",
            )

        event = ImageRequest(
            image_b64=payload.image_b64,
            instruction=payload.instruction.strip(),
            source_url=payload.source_url[:2048],
            received_at=payload.timestamp or _utcnow_iso(),
        )
        if not bus.publish(event):
            raise HTTPException(
                status_code=503,
                detail="La cola está saturada; reintenta en unos segundos.",
            )
        if cfg.log_privacy:
            log.info("Imagen aceptada (%d b64 chars).", len(payload.image_b64))
        else:
            log.info(
                "Imagen aceptada (%d b64 chars) de %s",
                len(payload.image_b64),
                payload.source_url,
            )
        return JSONResponse(
            status_code=202,
            content={"accepted": True, "message": "Captura en cola para procesar."},
        )

    # ------------------------------------------------------------------
    @app.exception_handler(httpx.HTTPError)
    async def _httpx_error_handler(
        request: Request, exc: httpx.HTTPError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"error": "Error de comunicación con Ollama: " + str(exc)},
        )

    return app
