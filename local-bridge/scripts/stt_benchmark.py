"""Benchmark real de reconocimiento de voz (locuciones TTS de Windows).

Mide por motor: tiempo de carga, latencia por intervención, factor de tiempo
real (RTF), memoria del proceso y WER frente al texto de referencia.

Uso (los motores remotos consumen cuota: se limitan con --max-remote):
    .venv\\Scripts\\python.exe scripts\\stt_benchmark.py faster-whisper:base faster-whisper:small
    .venv\\Scripts\\python.exe scripts\\stt_benchmark.py groq:whisper-large-v3-turbo --max-remote 3
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import psutil
from tts_samples import SAMPLES, synthesize_lines

from app.audio.dsp import StreamingResampler
from app.audio.segmenter import Segmenter
from app.audio.types import SAMPLE_RATE, AudioChunk, SourceKind, SpeechSegment
from app.audio.vad import create_vad
from app.audio.wav import decode_wav
from app.stt.base import STTError
from app.stt.catalog import get_spec
from app.stt.factory import create_recognizer


def normalize_words(text: str, strip_accents: bool = False) -> list[str]:
    text = text.lower()
    if strip_accents:
        text = "".join(
            c
            for c in unicodedata.normalize("NFD", text)
            if unicodedata.category(c) != "Mn"
        )
    text = re.sub(r"[^\w\s]", " ", text)
    return text.split()


def wer(reference: str, hypothesis: str, strip_accents: bool = False) -> float:
    ref = normalize_words(reference, strip_accents)
    hyp = normalize_words(hypothesis, strip_accents)
    rows = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, rows[0] = rows[0], i
        for j, h in enumerate(hyp, 1):
            cur = min(rows[j] + 1, rows[j - 1] + 1, prev + (r != h))
            prev, rows[j] = rows[j], cur
    return rows[len(hyp)] / max(1, len(ref))


def segments_from(path: Path) -> list[SpeechSegment]:
    samples, rate = decode_wav(path.read_bytes())
    samples = StreamingResampler(rate, SAMPLE_RATE).process(samples)
    seg = Segmenter(create_vad("auto"), SourceKind.SYSTEM)
    out: list[SpeechSegment] = []
    for start in range(0, len(samples), 1600):
        out += seg.feed(AudioChunk(samples[start : start + 1600], SourceKind.SYSTEM, 0))
    out += seg.flush()
    return [s for s in out if s.final and not s.discarded]


def bench(key: str, clips: dict, max_remote: int) -> dict:
    spec = get_spec(key)
    process = psutil.Process()
    rss_before = process.memory_info().rss
    recognizer = create_recognizer(key)
    report: dict = {"engine": key, "results": []}
    started = time.perf_counter()
    if hasattr(recognizer, "load"):
        recognizer.load()
        report["load_s"] = round(time.perf_counter() - started, 2)
        report["device"] = f"{recognizer.device}/{recognizer.compute_type}"
    rss_peak = process.memory_info().rss
    requests = 0
    report["wer"] = {}
    for language, (segments, references) in clips.items():
        texts: list[str] = []
        complete = True
        for segment in segments:
            if not spec.is_local and requests >= max_remote:
                complete = False
                break
            try:
                result = recognizer.transcribe(segment.samples, language=language)
            except STTError as exc:
                report["results"].append({"error": exc.message, "kind": exc.kind})
                complete = False
                break
            requests += 1
            rss_peak = max(rss_peak, process.memory_info().rss)
            texts.append(result.text)
            report["results"].append(
                {
                    "lang": language,
                    "audio_s": result.audio_seconds,
                    "latency_s": result.elapsed_seconds,
                    "rtf": round(result.real_time_factor or 0, 3),
                    "confidence": result.confidence,
                    "detected": result.language,
                    "text": result.text,
                }
            )
        # El VAD puede partir una frase en varias intervenciones: el WER se
        # calcula sobre el texto concatenado (solo si se transcribió todo).
        if complete and texts:
            reference = " ".join(references)
            hypothesis = " ".join(texts)
            report["wer"][language] = {
                "wer": round(wer(reference, hypothesis), 3),
                "wer_no_accents": round(wer(reference, hypothesis, True), 3),
            }
    recognizer.close()
    ok = [r for r in report["results"] if "rtf" in r]
    if ok:
        audio = sum(r["audio_s"] for r in ok)
        busy = sum(r["latency_s"] for r in ok)
        report["aggregate_rtf"] = round(busy / audio, 3)
        report["mean_latency_s"] = round(busy / len(ok), 3)
    report["requests"] = requests
    report["rss_delta_mb"] = round((rss_peak - rss_before) / 1e6, 1)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("engines", nargs="+")
    parser.add_argument("--max-remote", type=int, default=3)
    parser.add_argument("--languages", default="es,en")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    clips: dict = {}
    with tempfile.TemporaryDirectory() as tmp:
        for language in args.languages.split(","):
            wav = Path(tmp) / f"{language}.wav"
            synthesize_lines(SAMPLES[language], language, wav)
            segments = segments_from(wav)
            if len(segments) != len(SAMPLES[language]):
                print(
                    f"Aviso: {language}: {len(segments)} intervenciones para "
                    f"{len(SAMPLES[language])} frases.",
                    file=sys.stderr,
                )
            clips[language] = (segments, SAMPLES[language])
    reports = [bench(engine, clips, args.max_remote) for engine in args.engines]
    payload = {
        "cpu": psutil.cpu_count(logical=False),
        "threads": psutil.cpu_count(),
        "reports": reports,
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
