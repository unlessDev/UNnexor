"""Prueba real de captura y segmentación (Windows).

1. Genera una locución en español con la voz TTS del sistema (System.Speech)
   en un WAV temporal: no se usa audio personal.
2. ``--file``: segmenta el WAV directamente con el VAD elegido.
3. ``--loopback``: reproduce el WAV por los altavoces predeterminados y lo
   captura con WASAPI loopback a través de ``AudioPipeline`` (suena unos
   segundos en el equipo).

Uso:
    .venv\\Scripts\\python.exe scripts\\audio_e2e.py --file --loopback
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.audio.capture import CaptureStatus, SoundCardBackend
from app.audio.dsp import StreamingResampler
from app.audio.pipeline import AudioPipeline, SourceSelection
from app.audio.segmenter import Segmenter
from app.audio.types import (
    SAMPLE_RATE,
    AudioChunk,
    SourceKind,
    SpeechSegment,
)
from app.audio.vad import create_vad
from app.audio.wav import decode_wav

DEFAULT_TEXT = (
    "Buenos días a todos. Empezamos la reunión con el informe del trimestre. "
    "¿Podrías contarme cuál fue tu experiencia más difícil trabajando en equipo "
    "y cómo la resolviste?"
)


def synthesize(text: str, voice_hint: str, target: Path) -> None:
    """Locución TTS con System.Speech (voz instalada en Windows)."""
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$v = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name "
        f"-like '{voice_hint}*' }} | Select-Object -First 1;"
        "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) };"
        "$s.Rate = 0;"
        f"$s.SetOutputToWaveFile('{target}');"
        "$s.Speak($env:EXTDATOR_TTS_TEXT); $s.Dispose()"
    )
    env = dict(os.environ, EXTDATOR_TTS_TEXT=text)
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=True,
        env=env,
        capture_output=True,
        timeout=60,
    )


def segment_file(path: Path, vad_name: str) -> list[SpeechSegment]:
    samples, rate = decode_wav(path.read_bytes())
    samples = StreamingResampler(rate, SAMPLE_RATE).process(samples)
    segmenter = Segmenter(create_vad(vad_name), SourceKind.SYSTEM)
    events: list[SpeechSegment] = []
    block = SAMPLE_RATE // 10
    for start in range(0, len(samples), block):
        piece = samples[start : start + block]
        events += segmenter.feed(
            AudioChunk(piece, SourceKind.SYSTEM, start / SAMPLE_RATE)
        )
    events += segmenter.flush()
    return events


def capture_loopback(path: Path, vad_name: str) -> dict:
    import winsound

    segments: list[SpeechSegment] = []
    statuses: list[CaptureStatus] = []
    levels: list[float] = []
    lock = threading.Lock()

    def on_segment(segment: SpeechSegment) -> None:
        with lock:
            segments.append(segment)

    pipeline = AudioPipeline(
        SoundCardBackend(),
        [SourceSelection(SourceKind.SYSTEM)],
        on_segment,
        statuses.append,
        on_level=lambda _k, db, _p: levels.append(db),
        vad_factory=lambda: create_vad(vad_name),
    )
    pipeline.start()
    time.sleep(0.8)  # el dispositivo abre antes de reproducir
    started = time.perf_counter()
    winsound.PlaySound(str(path), winsound.SND_FILENAME)  # bloqueante
    played = time.perf_counter() - started
    time.sleep(1.5)  # deja cerrar la última intervención por silencio
    pipeline.stop()
    finals = [s for s in segments if s.final and not s.discarded]
    return {
        "vad": pipeline.vad_name,
        "played_s": round(played, 2),
        "states": sorted({s.state.value for s in statuses}),
        "max_level_dbfs": round(max(levels), 1) if levels else None,
        "provisional": sum(1 for s in segments if not s.final),
        "finals": [
            {
                "id": s.segment_id,
                "start": round(s.t_start, 2),
                "dur": round(s.duration, 2),
            }
            for s in finals
        ],
        "dropped_chunks": pipeline.dropped_chunks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", action="store_true")
    parser.add_argument("--loopback", action="store_true")
    parser.add_argument("--vad", default="auto")
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--voice", default="es")
    parser.add_argument("--keep", type=Path, help="Copia el WAV generado aquí")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "tts.wav"
        synthesize(args.text, args.voice, wav)
        if args.keep:
            args.keep.write_bytes(wav.read_bytes())
        report: dict = {"tts_bytes": wav.stat().st_size}
        if args.file:
            finals = [s for s in segment_file(wav, args.vad) if s.final]
            report["file"] = [
                {
                    "start": round(s.t_start, 2),
                    "dur": round(s.duration, 2),
                    "discarded": s.discarded,
                }
                for s in finals
            ]
        if args.loopback:
            report["loopback"] = capture_loopback(wav, args.vad)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
