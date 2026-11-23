"""Cliente de la API HTTP local de Ollama (http://127.0.0.1:11434).

Implementa chat en streaming real contra ``POST /api/chat`` (NDJSON línea a
línea). Nunca se invoca la CLI de Ollama ni se inyecta texto por teclado: el
texto seleccionado viaja siempre por HTTP dentro de JSON UTF-8.

El transporte httpx es inyectable para poder simular Ollama en los tests.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import httpx


class OllamaError(Exception):
    """Error con mensaje orientado al usuario (sin trazas en la UI)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class OllamaConnectionError(OllamaError):
    """Ollama no responde / no está instalado / no está ejecutándose."""


class ModelNotFoundError(OllamaError):
    """El modelo configurado no existe en Ollama."""


class OllamaResponseError(OllamaError):
    """Respuesta malformada o error reportado por el propio Ollama.

    ``detail`` conserva el texto crudo de Ollama (p. ej.
    ``"x" does not support thinking``) para reintentos decididos por el
    llamador; ``message`` es la versión orientada a la UI.
    """

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


class GenerationCancelled(OllamaError):
    """El usuario pidió detener la generación o limpiar la conversación."""

    def __init__(self) -> None:
        super().__init__("Generación cancelada por el usuario.")


def _friendly_http_error(exc: httpx.HTTPError, base_url: str) -> str:
    detail = str(exc) or exc.__class__.__name__
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return (
            f"No se pudo conectar con Ollama en {base_url}. "
            "¿Está instalado y en ejecución? (abre la app de Ollama o usa 'ollama serve')"
        )
    if isinstance(exc, httpx.ReadTimeout):
        return (
            "Ollama tardó demasiado en responder (timeout). "
            "Prueba con un modelo más ligero o sube OLLAMA_TIMEOUT."
        )
    return f"Error de red con Ollama: {detail}"


def _read_error_detail(response: httpx.Response) -> str:
    try:
        response.read()
        data = response.json()
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])
    except Exception:
        pass
    return "sin detalles"


def _finalize_stats(
    text: str, stats: Dict[str, Any], started: float
) -> Tuple[str, Dict[str, Any]]:
    """Completa las estadísticas de una generación (duración y tokens/s)."""
    stats["elapsed_s"] = round(time.perf_counter() - started, 3)
    eval_count = stats.get("eval_count")
    eval_duration = stats.get("eval_duration")
    if eval_count and eval_duration:
        stats["tokens_per_s"] = round(eval_count / (eval_duration / 1e9), 2)
    return text, stats


# Tiempo máximo del sondeo de versión usado por /health. El cliente que
# consume /health debe esperar más que este valor (ver main.HEALTH_REQUEST_TIMEOUT).
OLLAMA_HEALTH_TIMEOUT = 2.0


class OllamaClient:
    """Fachada síncrona sobre la API HTTP de Ollama (uso desde QThread)."""

    def __init__(
        self,
        cfg,
        transport: Optional[httpx.BaseTransport] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self._base_url = (base_url or cfg.ollama_host).rstrip("/")
        self._model = cfg.ollama_model
        self._keep_alive = cfg.ollama_keep_alive
        self._temperature = cfg.ollama_temperature
        self._timeout = cfg.ollama_timeout
        # "Thinking" de modelos que lo soportan (OLLAMA_THINK): None = no
        # enviar el parámetro y dejar el comportamiento por defecto.
        self._think = getattr(cfg, "ollama_think", None)
        # Cache por modelo de si soporta "thinking" (Ollama rechaza con
        # 400 el parámetro en modelos sin esa capacidad).
        self._thinking_cache: Dict[str, bool] = {}
        # Opciones extra del .env (OLLAMA_EXTRA_OPTIONS): top_p, top_k,
        # repeat_penalty, num_ctx, num_predict, etc.
        self._extra_options = dict(getattr(cfg, "ollama_extra_options", {}) or {})
        self._client = httpx.Client(
            base_url=self._base_url,
            transport=transport,
            timeout=httpx.Timeout(
                connect=5.0,
                read=self._timeout,
                write=30.0,
                pool=30.0,
            ),
        )

    # ------------------------------------------------------------------
    def close(self) -> None:
        """Cierra el pool de conexiones (aborta streams en curso)."""
        try:
            self._client.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    def is_reachable(self, timeout: float = OLLAMA_HEALTH_TIMEOUT) -> Tuple[bool, str]:
        try:
            response = self._client.get("/api/version", timeout=timeout)
        except httpx.HTTPError as exc:
            return False, _friendly_http_error(exc, self._base_url)
        if response.status_code != 200:
            return False, f"Ollama respondió HTTP {response.status_code}"
        try:
            version = response.json().get("version", "?")
        except Exception:
            version = "?"
        return True, str(version)

    def list_models(self) -> List[str]:
        try:
            response = self._client.get("/api/tags", timeout=8.0)
        except httpx.HTTPError as exc:
            raise OllamaConnectionError(
                _friendly_http_error(exc, self._base_url)
            ) from exc
        if response.status_code != 200:
            raise OllamaResponseError(
                f"Ollama devolvió HTTP {response.status_code} al listar modelos."
            )
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise OllamaResponseError("Respuesta no válida de /api/tags.") from exc
        models = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
        return models

    # ------------------------------------------------------------------
    def installed_models_full(self) -> List[Dict[str, Any]]:
        """Modelos instalados con metadatos (tamaño, fecha, detalles).

        Devuelve la lista cruda de ``GET /api/tags`` (name, size, digest,
        modified_at, details...). Lanza ``OllamaError`` si Ollama no responde.
        """
        try:
            response = self._client.get("/api/tags", timeout=8.0)
        except httpx.HTTPError as exc:
            raise OllamaConnectionError(
                _friendly_http_error(exc, self._base_url)
            ) from exc
        if response.status_code != 200:
            raise OllamaResponseError(
                f"Ollama devolvió HTTP {response.status_code} al listar modelos."
            )
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise OllamaResponseError("Respuesta no válida de /api/tags.") from exc
        models = data.get("models", [])
        return [m for m in models if isinstance(m, dict)]

    def show(self, model: str) -> Dict[str, Any]:
        """Detalles de un modelo (``POST /api/show``): familia, parámetros,
        cuantización, longitud de contexto y capacidades (p. ej. "vision")."""
        try:
            response = self._client.post(
                "/api/show", json={"model": model}, timeout=10.0
            )
        except httpx.HTTPError as exc:
            raise OllamaConnectionError(
                _friendly_http_error(exc, self._base_url)
            ) from exc
        if response.status_code == 404:
            raise ModelNotFoundError(
                f"El modelo '{model}' no está disponible en Ollama."
            )
        if response.status_code != 200:
            detail = _read_error_detail(response)
            raise OllamaResponseError(
                f"Ollama devolvió HTTP {response.status_code} al consultar el "
                f"modelo: {detail}"
            )
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise OllamaResponseError("Respuesta no válida de /api/show.") from exc

    def running_models(self) -> List[Dict[str, Any]]:
        """Modelos cargados en memoria ahora mismo (``GET /api/ps``).

        Cada entrada incluye ``size`` y ``size_vram``; ``size_vram > 0``
        indica que el runtime está usando la GPU.
        """
        try:
            response = self._client.get("/api/ps", timeout=8.0)
        except httpx.HTTPError as exc:
            raise OllamaConnectionError(
                _friendly_http_error(exc, self._base_url)
            ) from exc
        if response.status_code != 200:
            return []
        try:
            data = response.json()
        except json.JSONDecodeError:
            return []
        models = data.get("models", [])
        return [m for m in models if isinstance(m, dict)]

    def pull_stream(
        self,
        model: str,
        on_progress: Optional[
            Callable[[str, Optional[int], Optional[int]], None]
        ] = None,
        should_abort: Optional[Callable[[], bool]] = None,
    ) -> str:
        """Descarga un modelo (``POST /api/pull``) con progreso en streaming.

        ``on_progress(status, total, completed)`` recibe bytes totales y
        completados (None cuando no se conocen). Devuelve el estado final.
        Lanza ``OllamaError`` si Ollama reporta un error (p. ej. 404).
        """
        payload: Dict[str, Any] = {"model": model, "stream": True}
        last_status = ""
        try:
            with self._client.stream("POST", "/api/pull", json=payload) as response:
                if response.status_code != 200:
                    detail = _read_error_detail(response)
                    raise OllamaResponseError(
                        f"Ollama devolvió HTTP {response.status_code}: {detail}"
                    )
                for line in response.iter_lines():
                    if should_abort is not None and should_abort():
                        raise GenerationCancelled()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise OllamaResponseError(
                            "Ollama envió un fragmento no válido durante la descarga."
                        ) from exc
                    if obj.get("error"):
                        raise OllamaResponseError(
                            f"Error al descargar el modelo: {obj['error']}"
                        )
                    last_status = str(obj.get("status", ""))
                    total = obj.get("total")
                    completed = obj.get("completed")
                    if on_progress is not None:
                        on_progress(last_status, total, completed)
        except httpx.HTTPError as exc:
            raise OllamaConnectionError(
                _friendly_http_error(exc, self._base_url)
            ) from exc
        return last_status

    # ------------------------------------------------------------------
    def _supports_thinking(self, model: str) -> Optional[bool]:
        """¿El modelo soporta ``think``? ``None`` si no se pudo saber.

        Ollama responde 400 "does not support thinking" si se envía el
        parámetro a un modelo sin esa capacidad; la comprobación se cachea
        por modelo (``/api/show`` es barata pero no gratuita).
        """
        cached = self._thinking_cache.get(model)
        if cached is not None:
            return cached
        try:
            capabilities = self.show(model).get("capabilities", [])
        except OllamaError:
            return None  # sin datos: se conserva el comportamiento anterior
        supports = "thinking" in capabilities
        self._thinking_cache[model] = supports
        return supports

    # ------------------------------------------------------------------
    def chat_stream(
        self,
        messages: Iterable[Dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        keep_alive: Optional[str] = None,
        on_chunk: Optional[Callable[[str], None]] = None,
        should_abort: Optional[Callable[[], bool]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Chat con streaming. Devuelve (texto_completo, estadísticas).

        Lanza ``GenerationCancelled`` si ``should_abort()`` pasa a True entre
        fragmentos, permitiendo cancelar generaciones largas.
        """
        payload: Dict[str, Any] = {
            "model": model or self._model,
            "messages": list(messages),
            "stream": True,
        }
        effective_keep_alive = self._keep_alive if keep_alive is None else keep_alive
        if effective_keep_alive:
            payload["keep_alive"] = effective_keep_alive
        options: Dict[str, Any] = dict(self._extra_options)
        effective_temp = self._temperature if temperature is None else temperature
        if effective_temp is not None:
            options["temperature"] = effective_temp
        if options:
            payload["options"] = options
        # ``think`` solo para modelos con capacidad "thinking" (los demás
        # responden 400); si no se pudo consultar, se envía como siempre.
        if (
            self._think is not None
            and self._supports_thinking(payload["model"]) is not False
        ):
            payload["think"] = self._think

        def run_attempt(with_think: bool) -> Tuple[str, Dict[str, Any]]:
            attempt_payload = dict(payload)
            if not with_think:
                attempt_payload.pop("think", None)

            parts: List[str] = []
            stats: Dict[str, Any] = {}
            try:
                with self._client.stream(
                    "POST", "/api/chat", json=attempt_payload
                ) as response:
                    if response.status_code == 404:
                        detail = _read_error_detail(response)
                        raise ModelNotFoundError(
                            f"El modelo '{attempt_payload['model']}' no está "
                            "disponible en Ollama "
                            f"({detail}). Instálalo con: ollama pull "
                            f"{attempt_payload['model']} o cambia OLLAMA_MODEL."
                        )
                    if response.status_code != 200:
                        detail = _read_error_detail(response)
                        raise OllamaResponseError(
                            f"Ollama devolvió HTTP {response.status_code}: "
                            f"{detail}",
                            detail=detail,
                        )
                    for line in response.iter_lines():
                        if should_abort is not None and should_abort():
                            raise GenerationCancelled()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise OllamaResponseError(
                                "Ollama envió un fragmento no válido "
                                "(JSON malformado)."
                            ) from exc
                        if obj.get("error"):
                            raise OllamaResponseError(f"Ollama reportó: {obj['error']}")
                        delta = (obj.get("message") or {}).get("content") or ""
                        if delta:
                            parts.append(delta)
                            if on_chunk is not None:
                                on_chunk(delta)
                        if obj.get("done"):
                            for key in (
                                "total_duration",
                                "eval_count",
                                "prompt_eval_count",
                                "eval_duration",
                            ):
                                if key in obj:
                                    stats[key] = obj[key]
                            break
            except httpx.HTTPError as exc:
                raise OllamaConnectionError(
                    _friendly_http_error(exc, self._base_url)
                ) from exc
            return "".join(parts), stats

        started = time.perf_counter()
        try:
            return _finalize_stats(*run_attempt("think" in payload), started)
        except OllamaResponseError as exc:
            if (
                exc.detail
                and "does not support thinking" in exc.detail
                and "think" in payload
            ):
                # El modelo no soporta thinking (el show no lo reflejó o no
                # se pudo consultar): reintenta una vez sin el parámetro.
                self._thinking_cache[payload["model"]] = False
                return _finalize_stats(*run_attempt(False), started)
            raise
