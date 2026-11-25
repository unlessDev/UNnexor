"""Deepgram: API REST de audio pregrabado (``POST /v1/listen``).

Verificado el 2026-11-24: autenticación ``Authorization: Token <clave>``,
audio binario con ``Content-Type: audio/wav``, ``model=nova-3`` con
``language=es``/``es-419``; el idioma por defecto es ``en`` (por eso se
envía siempre ``language`` o, en modo automático, ``detect_language=true``,
que solo existe para audio pregrabado). Transcripción en
``results.channels[0].alternatives[0].transcript`` y confianza en
``...confidence``; idioma detectado en ``results.channels[0].detected_language``.

La API de streaming por WebSocket existe pero no se integra todavía: este
adaptador trabaja por fragmentos y así se declara en el catálogo.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..audio.types import SAMPLE_RATE, FloatArray
from ..audio.wav import encode_wav
from .base import STTError, Transcript, TranscriptPiece
from .hallucinations import is_hallucination
from .remote_common import error_from_response, network_error, normalize_language

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"


class DeepgramTranscriber:
    engine_id = "deepgram"
    is_remote = True
    supports_partials = False

    def __init__(
        self,
        api_key: str,
        model: str = "nova-3",
        *,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise STTError("Falta la clave API de Deepgram.", "auth")
        self.model = model
        self._client = httpx.Client(
            headers={
                "Authorization": f"Token {api_key}",
                "Content-Type": "audio/wav",
            },
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def transcribe(
        self,
        audio: FloatArray,
        *,
        language: str | None = None,
        prompt: str | None = None,
    ) -> Transcript:
        del prompt  # la API pregrabada no admite un prompt libre
        if audio.size < SAMPLE_RATE // 10:
            raise STTError("Fragmento de audio demasiado corto.", "invalid_audio")
        params: dict[str, str] = {
            "model": self.model,
            "smart_format": "true",
            "punctuate": "true",
        }
        if language:
            params["language"] = language
        else:
            params["detect_language"] = "true"
        started = time.perf_counter()
        try:
            response = self._client.post(
                DEEPGRAM_URL, params=params, content=encode_wav(audio)
            )
        except httpx.HTTPError as exc:
            raise network_error(exc, "Deepgram") from exc
        elapsed = time.perf_counter() - started
        if response.status_code != 200:
            raise error_from_response(response, "Deepgram")
        try:
            body: Any = response.json()
            channel = body["results"]["channels"][0]
            alternative = channel["alternatives"][0]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise STTError("Deepgram: respuesta no válida.", "general") from exc
        text = str(alternative.get("transcript") or "").strip()
        confidence = alternative.get("confidence")
        conf = float(confidence) if isinstance(confidence, int | float) else None
        seconds = audio.size / SAMPLE_RATE
        filtered = bool(text) and is_hallucination(text)
        pieces = (
            (TranscriptPiece(text, 0.0, seconds, conf),)
            if text and not filtered
            else ()
        )
        return Transcript(
            text="" if filtered else text,
            engine=self.engine_id,
            model=self.model,
            audio_seconds=round(seconds, 3),
            elapsed_seconds=round(elapsed, 3),
            language=normalize_language(channel.get("detected_language")) or language,
            language_probability=_maybe_float(channel.get("language_confidence")),
            confidence=round(conf, 3) if conf is not None else None,
            pieces=pieces,
            filtered=filtered,
        )


def _maybe_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None
