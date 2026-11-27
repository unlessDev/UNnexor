"""Transcripción por ``POST {base}/audio/transcriptions`` (multipart).

Compatible con Groq (``/openai/v1``) y OpenAI (``/v1``), verificados el
2026-11-24:

* Groq: ``response_format=verbose_json`` devuelve segmentos con
  ``avg_logprob`` y ``no_speech_prob``; ``prompt`` limitado a 224 tokens;
  mínimo facturado 10 s por petición.
* OpenAI ``gpt-transcribe``: ``languages[]`` sustituye a ``language`` (no
  se envían ambos) y responde JSON con ``text``; ``verbose_json`` solo
  existe para ``whisper-1``.

No es streaming: se envía un fragmento ya segmentado (modalidad por
fragmentos). Los fragmentos provisionales NO se envían (consumirían cuota).
"""

from __future__ import annotations

import math
import time
from typing import Any

import httpx

from ..audio.types import SAMPLE_RATE, FloatArray
from ..audio.wav import encode_wav
from .base import STTError, Transcript, TranscriptPiece
from .hallucinations import collapse_repetitions, is_hallucination, is_repetition_loop
from .remote_common import error_from_response, network_error, normalize_language

MAX_PROMPT_CHARS = 600  # ~224 tokens: límite documentado por Groq
MAX_UPLOAD_BYTES = 24 * 1024 * 1024  # bajo los 25 MB documentados


class OpenAICompatTranscriber:
    is_remote = True
    supports_partials = False

    def __init__(
        self,
        provider_id: str,
        base_url: str,
        api_key: str,
        model: str,
        *,
        label: str = "",
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise STTError("Falta la clave API del servicio de voz.", "auth")
        self.engine_id = provider_id
        self.model = model
        self._label = label or provider_id
        base = base_url.rstrip("/") + "/"
        self._client = httpx.Client(
            base_url=base,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def _uses_languages_array(self) -> bool:
        return self.model.startswith("gpt-")

    def _response_format(self) -> str:
        return "verbose_json" if self.model.startswith("whisper") else "json"

    def transcribe(
        self,
        audio: FloatArray,
        *,
        language: str | None = None,
        prompt: str | None = None,
    ) -> Transcript:
        if audio.size < SAMPLE_RATE // 10:
            raise STTError("Fragmento de audio demasiado corto.", "invalid_audio")
        wav = encode_wav(audio)
        if len(wav) > MAX_UPLOAD_BYTES:
            raise STTError("El fragmento supera el tamaño permitido.", "invalid_audio")
        data: dict[str, str] = {
            "model": self.model,
            "response_format": self._response_format(),
            "temperature": "0",
        }
        if language:
            key = "languages[]" if self._uses_languages_array() else "language"
            data[key] = language
        if prompt:
            data["prompt"] = prompt[-MAX_PROMPT_CHARS:]
        started = time.perf_counter()
        try:
            response = self._client.post(
                "audio/transcriptions",
                data=data,
                files={"file": ("audio.wav", wav, "audio/wav")},
            )
        except httpx.HTTPError as exc:
            raise network_error(exc, self._label) from exc
        elapsed = time.perf_counter() - started
        if response.status_code != 200:
            raise error_from_response(response, self._label)
        try:
            body: Any = response.json()
        except ValueError as exc:
            raise STTError(f"{self._label}: respuesta no válida.", "general") from exc
        return self._parse(body, audio.size / SAMPLE_RATE, elapsed)

    def _parse(self, body: Any, seconds: float, elapsed: float) -> Transcript:
        if not isinstance(body, dict):
            raise STTError(f"{self._label}: respuesta no válida.", "general")
        text = str(body.get("text") or "").strip()
        pieces: list[TranscriptPiece] = []
        weights = weighted = 0.0
        for seg in body.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            seg_text = str(seg.get("text") or "").strip()
            avg = seg.get("avg_logprob")
            no_speech = float(seg.get("no_speech_prob") or 0.0)
            confidence = None
            if isinstance(avg, int | float):
                if no_speech > 0.6 and avg < -1.0:
                    continue
                confidence = max(0.0, min(1.0, math.exp(float(avg))))
            start, end = float(seg.get("start") or 0), float(seg.get("end") or 0)
            pieces.append(TranscriptPiece(seg_text, start, end, confidence))
            if confidence is not None:
                span = max(0.01, end - start)
                weights += span
                weighted += confidence * span
        if pieces:
            text = " ".join(p.text for p in pieces if p.text).strip()
        text = collapse_repetitions(text)
        filtered = bool(text) and (is_hallucination(text) or is_repetition_loop(text))
        return Transcript(
            text="" if filtered else text,
            engine=self.engine_id,
            model=self.model,
            audio_seconds=round(seconds, 3),
            elapsed_seconds=round(elapsed, 3),
            language=normalize_language(body.get("language")),
            confidence=round(weighted / weights, 3) if weights else None,
            pieces=() if filtered else tuple(pieces),
            filtered=filtered,
        )
