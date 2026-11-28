"""Mediciones de rendimiento opcionales (solo con consentimiento).

Consumen CPU/GPU y memoria durante decenas de segundos: la interfaz las
ofrece como acción explícita. No descargan nada: miden modelos instalados.

* ``benchmark_llm``: carga (``/api/generate`` sin prompt), primer token,
  tokens/s informados por Ollama, VRAM usada (``/api/ps``) y caída de RAM.
* ``benchmark_stt``: carga del modelo Whisper y latencia por intervención
  (Whisper procesa ventanas de 30 s: la latencia casi no depende del
  contenido, así que se mide con ruido de fondo sintético).
"""

from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np

from ..ollama_client import OllamaClient

PROMPT = "Explica en tres frases qué es una API REST y da un ejemplo de endpoint."


class _RamSampler:
    """Pico de memoria durante la medición.

    ``own_process=True`` (STT: el modelo vive en este proceso) mide el RSS
    propio, fiable aunque el equipo esté ocupado. Para Ollama (otro proceso)
    se usa la caída de RAM libre del sistema: aproximada.
    """

    def __init__(self, own_process: bool = False) -> None:
        import psutil

        self._psutil = psutil
        self._process = psutil.Process() if own_process else None
        self.baseline_mb = self._used_mb()
        self.maximum_mb = self.baseline_mb
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _used_mb(self) -> float:
        if self._process is not None:
            return float(self._process.memory_info().rss) / 2**20
        memory = self._psutil.virtual_memory()
        return float(memory.total - memory.available) / 2**20

    def _run(self) -> None:
        while not self._stop.wait(0.1):
            self.maximum_mb = max(self.maximum_mb, self._used_mb())

    def __enter__(self) -> _RamSampler:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join(2)

    @property
    def peak_used_mb(self) -> float:
        return round(max(0.0, self.maximum_mb - self.baseline_mb), 1)


def benchmark_llm(ollama: OllamaClient, model: str) -> dict[str, Any]:
    with _RamSampler() as ram:
        load_s = ollama.preload(model, keep_alive="5m")
        first: list[float] = []
        started = time.perf_counter()
        text, stats = ollama.chat_stream(
            [{"role": "user", "content": PROMPT}],
            model=model,
            temperature=0.0,
            on_chunk=lambda _d: (
                first.append(time.perf_counter() - started) if not first else None
            ),
        )
        total_s = time.perf_counter() - started
    vram_mb = None
    try:
        for entry in ollama.running_models():
            if entry.get("name") == model or entry.get("model") == model:
                vram_mb = round(int(entry.get("size_vram") or 0) / 2**20)
    except Exception:
        pass
    return {
        "model": model,
        "load_s": load_s,
        "first_token_s": round(first[0], 2) if first else None,
        "tokens_per_s": stats.get("tokens_per_s"),
        "eval_count": stats.get("eval_count"),
        "total_s": round(total_s, 2),
        "vram_mb": vram_mb,
        "ram_peak_delta_mb": ram.peak_used_mb,
        "chars": len(text),
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


SPEECH_TEXT = (
    "Buenos días. ¿Podrías contarme cuál fue tu experiencia más difícil "
    "trabajando en equipo?"
)


def speech_sample() -> tuple[Any, str]:
    """(audio 16 kHz, origen). Voz TTS de Windows generada en el momento; si
    no hay voces, silencio exacto. El ruido sintético NO sirve: provoca
    re-decodificaciones y alucinaciones de Whisper (latencia irreal)."""
    import os
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    from ..audio.dsp import StreamingResampler
    from ..audio.types import SAMPLE_RATE
    from ..audio.wav import decode_wav

    if sys.platform == "win32":
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "speech.wav"
            script = (
                "Add-Type -AssemblyName System.Speech;"
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                f"$s.SetOutputToWaveFile('{target}');"
                "$s.Speak($env:UNNEXOR_TTS_TEXT); $s.Dispose()"
            )
            try:
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", script],
                    check=True,
                    capture_output=True,
                    timeout=60,
                    env=dict(os.environ, UNNEXOR_TTS_TEXT=SPEECH_TEXT),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                samples, rate = decode_wav(target.read_bytes())
                return StreamingResampler(rate, SAMPLE_RATE).process(samples), "voz TTS"
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
    return np.zeros(16_000 * 4, dtype=np.float32), "silencio (sin voz TTS)"


def benchmark_stt(model: str, calls: int = 3) -> dict[str, Any]:
    from ..stt.faster_whisper_engine import FasterWhisperRecognizer

    audio, source = speech_sample()
    seconds = len(audio) / 16_000
    recognizer = FasterWhisperRecognizer(model)
    with _RamSampler(own_process=True) as ram:
        started = time.perf_counter()
        recognizer.load()
        load_s = time.perf_counter() - started
        latencies = []
        for _ in range(calls):
            latencies.append(
                recognizer.transcribe(audio, language="es").elapsed_seconds
            )
    device = recognizer.device
    recognizer.close()
    call_s = sum(latencies) / len(latencies)
    return {
        "model": model,
        "device": device,
        "load_s": round(load_s, 2),
        "call_s": round(call_s, 2),
        "rtf": round(call_s / seconds, 3),
        "audio": source,
        "ram_peak_delta_mb": ram.peak_used_mb,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
