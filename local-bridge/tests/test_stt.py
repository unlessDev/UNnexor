"""Pruebas del reconocimiento de voz: adaptadores, catálogo, recomendación."""

from __future__ import annotations

import json

import httpx
import numpy as np
import pytest

from app.audio.types import SAMPLE_RATE
from app.audio.wav import decode_wav
from app.providers.access import AccessTier
from app.stt import faster_whisper_engine as fw
from app.stt.base import STTError, STTMode, Transcript
from app.stt.catalog import ALL_MODELS, get_spec
from app.stt.deepgram_engine import DEEPGRAM_URL, DeepgramTranscriber
from app.stt.factory import create_recognizer, engine_status
from app.stt.hallucinations import is_hallucination, is_repetition_loop
from app.stt.openai_compat_engine import OpenAICompatTranscriber
from app.stt.recommend import estimated_call_seconds, recommend

AUDIO = (0.2 * np.sin(2 * np.pi * 220 * np.arange(SAMPLE_RATE) / SAMPLE_RATE)).astype(
    np.float32
)


# ----------------------------------------------------------- alucinaciones
@pytest.mark.parametrize(
    "text",
    [
        "Subtítulos realizados por la comunidad de Amara.org",
        "¡Gracias por ver!",
        "Gracias por ver el video.",
        "Thank you.",
        "[Música]",
        "...",
        "",
    ],
)
def test_known_hallucinations_are_detected(text):
    assert is_hallucination(text)


@pytest.mark.parametrize(
    "text",
    [
        "Gracias por ver el informe antes de la reunión.",
        "Thank you for the detailed explanation of the pipeline.",
        "La música del anuncio estaba muy alta.",
    ],
)
def test_real_sentences_are_kept(text):
    assert not is_hallucination(text)


def test_repetition_loops():
    assert is_repetition_loop("sí " * 12)
    assert is_repetition_loop("no lo sé " * 6)
    assert not is_repetition_loop("sí, sí, claro que podemos revisar el plan mañana")


# ---------------------------------------------------------------- catálogo
def test_catalog_keys_unique_and_tiers_consistent():
    keys = [spec.key for spec in ALL_MODELS]
    assert len(keys) == len(set(keys))
    for spec in ALL_MODELS:
        assert spec.mode is STTMode.CHUNKED  # nada se declara streaming sin serlo
        assert spec.is_local == (spec.access is AccessTier.LOCAL)
        if not spec.is_local:
            assert spec.provider_id and spec.docs_url
    assert get_spec("faster-whisper:small").download_mb == 486
    with pytest.raises(KeyError):
        get_spec("nope:x")


def test_access_tier_semantics():
    assert not AccessTier.LOCAL.may_cost_money
    assert not AccessTier.FREE_LIMITED.may_cost_money
    assert AccessTier.CREDITS.may_cost_money
    assert AccessTier.UNKNOWN.may_cost_money  # ante la duda, avisar
    assert AccessTier.FREE_LIMITED.label == "Gratuito con límites de uso"


# ------------------------------------------------------ OpenAI-compatible
def _multipart_fields(request: httpx.Request) -> dict[str, str]:
    body = request.content.decode("latin-1")
    fields = {}
    for part in body.split(
        "--" + request.headers["content-type"].split("boundary=")[1]
    ):
        if 'name="' in part and "filename=" not in part:
            name = part.split('name="')[1].split('"')[0]
            fields[name] = part.split("\r\n\r\n", 1)[1].rsplit("\r\n", 1)[0]
    return fields


def test_groq_style_verbose_json_parsing_and_request():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["fields"] = _multipart_fields(request)
        seen["has_wav"] = b"RIFF" in request.content
        return httpx.Response(
            200,
            json={
                "text": " Hola a todos.",
                "language": "Spanish",
                "segments": [
                    {
                        "text": " Hola",
                        "start": 0,
                        "end": 0.5,
                        "avg_logprob": -0.1,
                        "no_speech_prob": 0.01,
                    },
                    {
                        "text": " a todos.",
                        "start": 0.5,
                        "end": 1.0,
                        "avg_logprob": -0.3,
                        "no_speech_prob": 0.02,
                    },
                    {
                        "text": " ruido",
                        "start": 1.0,
                        "end": 1.2,
                        "avg_logprob": -1.5,
                        "no_speech_prob": 0.9,
                    },
                ],
            },
        )

    stt = OpenAICompatTranscriber(
        "groq",
        "https://api.groq.com/openai/v1",
        "gsk_test",
        "whisper-large-v3-turbo",
        transport=httpx.MockTransport(handler),
    )
    result = stt.transcribe(AUDIO, language="es", prompt="x" * 2000)
    assert seen["url"] == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert seen["auth"] == "Bearer gsk_test"
    assert seen["has_wav"]
    fields = seen["fields"]
    assert fields["model"] == "whisper-large-v3-turbo"
    assert fields["language"] == "es"
    assert fields["response_format"] == "verbose_json"
    assert len(fields["prompt"]) == 600
    assert result.text == "Hola a todos."  # el segmento sin voz se descarta
    assert result.language == "es"
    assert 0.8 < (result.confidence or 0) < 0.85
    assert len(result.pieces) == 2


def test_gpt_transcribe_uses_languages_array_and_json():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(_multipart_fields(request))
        return httpx.Response(200, json={"text": "Buenos días."})

    stt = OpenAICompatTranscriber(
        "openai",
        "https://api.openai.com/v1",
        "sk",
        "gpt-transcribe",
        transport=httpx.MockTransport(handler),
    )
    assert stt.transcribe(AUDIO, language="es").text == "Buenos días."
    assert seen["languages[]"] == "es" and "language" not in seen
    assert seen["response_format"] == "json"


@pytest.mark.parametrize(
    "status,kind",
    [(401, "auth"), (402, "payment"), (404, "auth"), (429, "quota"), (500, "general")],
)
def test_remote_http_errors_are_typed(status, kind):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={"retry-after": "7"}, json={"error": {}})

    stt = OpenAICompatTranscriber(
        "groq",
        "https://x/v1",
        "k",
        "whisper-large-v3",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(STTError) as info:
        stt.transcribe(AUDIO)
    assert info.value.kind == kind
    if status == 429:
        assert info.value.retry_after == 7


def test_remote_network_error_and_short_audio():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin red")

    stt = OpenAICompatTranscriber(
        "groq",
        "https://x/v1",
        "k",
        "whisper-large-v3",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(STTError) as info:
        stt.transcribe(AUDIO)
    assert info.value.kind == "network"
    with pytest.raises(STTError) as info:
        stt.transcribe(AUDIO[:100])
    assert info.value.kind == "invalid_audio"


def test_remote_filters_hallucinated_output():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "Gracias por ver el video."})

    stt = OpenAICompatTranscriber(
        "openai",
        "https://x/v1",
        "k",
        "gpt-transcribe",
        transport=httpx.MockTransport(handler),
    )
    result = stt.transcribe(AUDIO)
    assert result.filtered and result.text == ""


# ---------------------------------------------------------------- Deepgram
def test_deepgram_request_and_parsing():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = request.url
        seen["auth"] = request.headers["authorization"]
        seen["ctype"] = request.headers["content-type"]
        samples, rate = decode_wav(request.content)
        seen["rate"] = rate
        return httpx.Response(
            200,
            json={
                "results": {
                    "channels": [
                        {
                            "detected_language": "es",
                            "language_confidence": 0.97,
                            "alternatives": [
                                {"transcript": "Hola equipo", "confidence": 0.93}
                            ],
                        }
                    ]
                }
            },
        )

    stt = DeepgramTranscriber("dg_key", transport=httpx.MockTransport(handler))
    result = stt.transcribe(AUDIO)
    assert str(seen["url"]).startswith(DEEPGRAM_URL)
    params = dict(seen["url"].params)
    assert params["model"] == "nova-3"
    assert params["detect_language"] == "true" and "language" not in params
    assert seen["auth"] == "Token dg_key"
    assert seen["ctype"] == "audio/wav"
    assert seen["rate"] == SAMPLE_RATE
    assert result.text == "Hola equipo" and result.confidence == 0.93
    assert result.language == "es" and result.language_probability == 0.97

    stt.transcribe(AUDIO, language="es-419")
    assert dict(seen["url"].params)["language"] == "es-419"


def test_deepgram_malformed_response():
    stt = DeepgramTranscriber(
        "k", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    )
    with pytest.raises(STTError):
        stt.transcribe(AUDIO)


# ------------------------------------------------------- factoría y estado
def test_factory_builds_remote_engines_with_injected_keys():
    keys = {"groq": "gsk", "deepgram": "dg"}
    groq = create_recognizer("groq:whisper-large-v3-turbo", key_lookup=keys.get)
    assert groq.engine_id == "groq" and groq.is_remote and not groq.supports_partials
    groq.close()
    deepgram = create_recognizer("deepgram:nova-3", key_lookup=keys.get)
    assert deepgram.engine_id == "deepgram"
    deepgram.close()
    with pytest.raises(STTError) as info:
        create_recognizer("openai:gpt-transcribe", key_lookup=keys.get)
    assert info.value.kind == "auth"


def test_engine_status_reports_missing_models_and_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("EXTDATOR_MODELS_DIR", str(tmp_path))
    status = engine_status(get_spec("faster-whisper:small"))
    assert not status.ready
    assert "no descargado" in status.reason or "no está instalado" in status.reason
    assert not engine_status(get_spec("groq:whisper-large-v3"), lambda _p: None).ready
    assert engine_status(get_spec("groq:whisper-large-v3"), lambda _p: "k").ready


def test_local_model_paths_are_validated(tmp_path, monkeypatch):
    monkeypatch.setenv("EXTDATOR_MODELS_DIR", str(tmp_path))
    with pytest.raises(STTError):
        fw.model_dir("../evil")
    assert fw.installed_models() == []
    target = fw.model_dir("base")
    target.mkdir(parents=True)
    for name in ("model.bin", "config.json", "tokenizer.json"):
        (target / name).write_text("x")
    assert fw.is_installed("base") and fw.installed_models() == ["base"]
    recognizer = fw.FasterWhisperRecognizer("small")
    with pytest.raises(STTError) as info:
        recognizer.transcribe(AUDIO)
    assert info.value.kind in ("model_missing", "unavailable")


# ---------------------------------------------------------- recomendación
HW_MODEST = {"cpu_cores": 6, "ram_available_mb": 6000, "gpus": []}


def test_recommend_local_only_by_default_and_privacy():
    recs = recommend(HW_MODEST, installed={"base"})
    assert all(r.spec.is_local for r in recs)
    assert recs[0].spec.model == "base"  # ya descargado y admite provisionales
    assert recommend(HW_MODEST, allow_remote=True, preference="privacy")[
        0
    ].spec.is_local


def test_recommend_warns_on_slow_hardware_and_respects_measurements():
    weak = {"cpu_cores": 2, "ram_available_mb": 3000, "gpus": []}
    small = next(r for r in recommend(weak) if r.spec.model == "small")
    assert small.realtime_ok is False and small.warnings
    measured = next(
        r
        for r in recommend(weak, measured_call_s={"small": 1.2})
        if r.spec.model == "small"
    )
    assert measured.realtime_ok and "medido" in " ".join(measured.reasons)


def test_estimated_call_seconds_uses_reference_measurement():
    call, measured = estimated_call_seconds(
        get_spec("faster-whisper:base"), HW_MODEST, False
    )
    assert call == pytest.approx(1.03) and measured
    call, measured = estimated_call_seconds(
        get_spec("faster-whisper:large-v3"), HW_MODEST, True
    )
    assert call < 1 and not measured


def test_recommend_remote_flags_costs():
    recs = recommend(HW_MODEST, allow_remote=True, connected={"groq"})
    deepgram = next(r for r in recs if r.spec.engine == "deepgram")
    assert any("costos" in w for w in deepgram.warnings)
    assert recs[0].spec.engine == "groq"


# --------------------------------------------- integración con modelo real
@pytest.mark.skipif(not fw.is_installed("base"), reason="Whisper base no descargado")
def test_real_whisper_base_handles_non_speech_without_errors():
    recognizer = fw.FasterWhisperRecognizer("base", beam_size=1)
    try:
        result = recognizer.transcribe(
            np.zeros(SAMPLE_RATE * 2, dtype=np.float32), language="es"
        )
    finally:
        recognizer.close()
    assert isinstance(result, Transcript)
    assert result.engine == "faster-whisper"
    assert recognizer.device == "cpu" or recognizer.device == "cuda"
    # El silencio no debe producir texto inventado.
    assert result.text == "" or not is_hallucination(result.text)
    assert json.loads(json.dumps({"rtf": result.real_time_factor}))["rtf"] >= 0
