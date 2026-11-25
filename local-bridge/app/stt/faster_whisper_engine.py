"""Whisper local con faster-whisper (CTranslate2).

* Dispositivo: CUDA (``float16``) si CTranslate2 detecta una GPU NVIDIA;
  en otro caso CPU ``int8``. CTranslate2 no soporta GPU AMD/Intel: en esos
  equipos se usa CPU y así se informa.
* Los modelos se leen SOLO de ``paths.models_dir()/faster-whisper/<modelo>``
  (``local_files_only``): nunca se descarga nada al transcribir. La descarga
  es una acción explícita (``download_model``) tras el consentimiento.
* El VAD ya se aplica en ``app.audio``; aquí ``vad_filter=False``.
* No es seguro compartir la instancia entre hilos: el servicio de
  transcripción la usa desde un único hilo.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import paths
from ..audio.types import SAMPLE_RATE, FloatArray
from .base import STTError, Transcript, TranscriptPiece
from .hallucinations import is_hallucination, is_repetition_loop

log = logging.getLogger("extdator.stt.whisper")

ENGINE_ID = "faster-whisper"
_REQUIRED_FILES = ("model.bin", "config.json", "tokenizer.json")


def models_root() -> Path:
    return paths.models_dir() / "faster-whisper"


def model_dir(model: str) -> Path:
    if not model or any(sep in model for sep in ("/", "\\", "..")):
        raise STTError(f"Nombre de modelo no válido: {model!r}", "general")
    return models_root() / model


def is_installed(model: str) -> bool:
    folder = model_dir(model)
    return all((folder / name).is_file() for name in _REQUIRED_FILES)


def installed_models() -> list[str]:
    root = models_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and is_installed(p.name))


def installed_size_mb(model: str) -> float:
    folder = model_dir(model)
    if not folder.is_dir():
        return 0.0
    total = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
    return round(total / 1e6, 1)


def engine_available() -> tuple[bool, str]:
    try:
        import faster_whisper  # noqa: F401
    except Exception as exc:
        return False, (
            "El reconocimiento de voz local no está instalado en esta edición "
            f"({exc.__class__.__name__})."
        )
    return True, ""


def cuda_devices() -> int:
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


def default_cpu_threads() -> int:
    """Núcleos físicos menos uno (audio, interfaz y LLM también trabajan)."""
    try:
        import psutil

        physical = psutil.cpu_count(logical=False) or os.cpu_count() or 2
    except Exception:
        physical = os.cpu_count() or 2
    return max(1, min(8, int(physical) - 1))


def download_model(
    model: str,
    on_progress: Callable[[float], None] | None = None,
) -> Path:
    """Descarga ``model`` desde Hugging Face (repos de faster-whisper).

    Bloqueante: llamar desde un hilo de trabajo y solo tras confirmar con
    el usuario. ``on_progress`` recibe MB descargados (sondeo del tamaño de
    la carpeta, porque huggingface_hub no expone progreso agregado).
    """
    ok, reason = engine_available()
    if not ok:
        raise STTError(reason, "unavailable")
    from faster_whisper.utils import download_model as fw_download

    target = model_dir(model)
    target.mkdir(parents=True, exist_ok=True)
    done = threading.Event()

    def monitor() -> None:
        while not done.wait(0.5):
            if on_progress is not None:
                on_progress(installed_size_mb(model))

    watcher = threading.Thread(target=monitor, name="whisper-download", daemon=True)
    watcher.start()
    try:
        fw_download(model, output_dir=str(target))
    except Exception as exc:
        raise STTError(
            f"No se pudo descargar el modelo '{model}': {exc}", "network"
        ) from exc
    finally:
        done.set()
        watcher.join(2)
    if not is_installed(model):
        raise STTError(f"La descarga de '{model}' quedó incompleta.", "network")
    if on_progress is not None:
        on_progress(installed_size_mb(model))
    log.info(
        "Modelo Whisper '%s' descargado (%.1f MB).", model, installed_size_mb(model)
    )
    return target


def remove_model(model: str) -> None:
    folder = model_dir(model)
    if folder.is_dir():
        shutil.rmtree(folder)


class FasterWhisperRecognizer:
    """Adaptador ``SpeechRecognizer`` sobre ``faster_whisper.WhisperModel``."""

    engine_id = ENGINE_ID
    is_remote = False
    supports_partials = True

    def __init__(
        self,
        model: str = "base",
        *,
        device: str = "auto",
        compute_type: str = "auto",
        cpu_threads: int = 0,
        beam_size: int = 3,
    ) -> None:
        self.model = model
        self._requested_device = device
        self._requested_compute = compute_type
        self._cpu_threads = cpu_threads or default_cpu_threads()
        self._beam = max(1, beam_size)
        self._impl: Any = None
        self.device = ""
        self.compute_type = ""
        self.load_seconds: float | None = None

    # ------------------------------------------------------------------ #
    def load(self) -> None:
        if self._impl is not None:
            return
        ok, reason = engine_available()
        if not ok:
            raise STTError(reason, "unavailable")
        if not is_installed(self.model):
            raise STTError(
                f"El modelo de voz '{self.model}' no está descargado. "
                "Descárgalo desde 'Motores de voz'.",
                "model_missing",
            )
        from faster_whisper import WhisperModel

        device = self._requested_device
        if device == "auto":
            device = "cuda" if cuda_devices() > 0 else "cpu"
        compute = self._requested_compute
        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"
        started = time.perf_counter()
        try:
            self._impl = WhisperModel(
                str(model_dir(self.model)),
                device=device,
                compute_type=compute,
                cpu_threads=self._cpu_threads,
                local_files_only=True,
            )
        except Exception as exc:
            if device == "cuda":
                log.warning("CUDA no utilizable (%s); se usa CPU int8.", exc)
                self._requested_device, self._requested_compute = "cpu", "int8"
                self.load()
                return
            raise STTError(
                f"No se pudo cargar el modelo de voz '{self.model}': {exc}",
                "general",
            ) from exc
        self.load_seconds = round(time.perf_counter() - started, 2)
        self.device, self.compute_type = device, compute
        log.info(
            "Whisper '%s' cargado en %s/%s (%d hilos) en %.2f s.",
            self.model,
            device,
            compute,
            self._cpu_threads,
            self.load_seconds,
        )

    def close(self) -> None:
        self._impl = None

    # ------------------------------------------------------------------ #
    def transcribe(
        self,
        audio: FloatArray,
        *,
        language: str | None = None,
        prompt: str | None = None,
    ) -> Transcript:
        if audio.size < SAMPLE_RATE // 10:
            raise STTError("Fragmento de audio demasiado corto.", "invalid_audio")
        self.load()
        started = time.perf_counter()
        try:
            segments, info = self._impl.transcribe(
                audio,
                language=language or None,
                task="transcribe",
                beam_size=self._beam,
                vad_filter=False,
                condition_on_previous_text=False,
                initial_prompt=prompt or None,
                temperature=(0.0, 0.2, 0.4),
            )
            raw = list(segments)  # el generador decodifica al iterar
        except Exception as exc:
            raise STTError(f"Fallo del reconocimiento local: {exc}", "general") from exc
        elapsed = time.perf_counter() - started

        pieces: list[TranscriptPiece] = []
        weights = 0.0
        weighted = 0.0
        for seg in raw:
            text = str(getattr(seg, "text", "")).strip()
            avg_logprob = float(getattr(seg, "avg_logprob", 0.0))
            no_speech = float(getattr(seg, "no_speech_prob", 0.0))
            if not text or (no_speech > 0.6 and avg_logprob < -1.0):
                continue
            confidence = max(0.0, min(1.0, math.exp(avg_logprob)))
            start, end = float(seg.start), float(seg.end)
            pieces.append(TranscriptPiece(text, start, end, round(confidence, 3)))
            span = max(0.01, end - start)
            weights += span
            weighted += confidence * span

        text = " ".join(piece.text for piece in pieces).strip()
        filtered = bool(text) and (is_hallucination(text) or is_repetition_loop(text))
        if filtered:
            log.debug("Transcripción descartada como alucinación: %r", text[:80])
        return Transcript(
            text="" if filtered else text,
            engine=self.engine_id,
            model=self.model,
            audio_seconds=round(audio.size / SAMPLE_RATE, 3),
            elapsed_seconds=round(elapsed, 3),
            language=getattr(info, "language", None),
            language_probability=_round(getattr(info, "language_probability", None)),
            confidence=round(weighted / weights, 3) if weights else None,
            pieces=() if filtered else tuple(pieces),
            filtered=filtered or (not text and audio.size > 0),
        )


def _round(value: Any) -> float | None:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None
