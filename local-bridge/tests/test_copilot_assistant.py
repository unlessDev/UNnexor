"""Detección de preguntas, prompts, ajustes, motor enrutado y asistente."""

from __future__ import annotations

import threading
import time

import pytest

from app.audio.types import SourceKind
from app.copilot.assistant import AssistantCallbacks, ConversationAssistant, QAStatus
from app.copilot.engine import GenerationResult, RoutedEngine
from app.copilot.prompts import (
    TranscriptLine,
    answer_messages,
    challenge_messages,
    summary_messages,
    translation_messages,
)
from app.copilot.questions import TurnAccumulator, score_question, similar
from app.copilot.settings import CopilotSettings, SettingsRepository
from app.copilot.transcription import TranscriptEvent
from app.knowledge.profiles import ContextProfile, Detail, Tone
from app.knowledge.service import KnowledgeBase
from app.providers.access import AccessTier
from app.providers.base import ProviderError
from app.routing.executor import RoutingCancelled, RoutingError
from app.routing.router import Candidate, RoutingPolicy, Strategy, Task


# --------------------------------------------------------------- preguntas
@pytest.mark.parametrize(
    "text",
    [
        "¿Cuál fue tu mayor desafío técnico?",
        "Podrías contarme cuál fue tu experiencia más difícil trabajando en equipo.",
        "Bueno, cuéntame sobre tu último proyecto",
        "Has trabajado con Kubernetes en producción",
        "Explícanos cómo diseñarías un sistema de colas",
        "How would you scale this service",
        "Tell me about a time you failed",
    ],
)
def test_questions_and_requests_are_detected(text):
    assert score_question(text).is_question(0.5), text


@pytest.mark.parametrize(
    "text",
    [
        "Gracias.",
        "Perfecto, entonces seguimos con el siguiente punto del informe.",
        "El presupuesto aumentó un quince por ciento.",
        "Sí",
    ],
)
def test_statements_are_not_questions(text):
    assert not score_question(text).is_question(0.5), text


def test_similarity_for_dedupe():
    assert similar(
        "¿Cuál fue tu mayor desafío técnico?", "cual fue tu mayor desafio tecnico"
    )
    assert not similar("¿Qué estudiaste?", "¿Dónde trabajaste antes?")


def test_turn_accumulator_waits_for_pause_and_splits_by_speaker():
    acc = TurnAccumulator(settle_s=1.0)
    assert acc.add(SourceKind.SYSTEM, "Cuéntame", 0, 1, now=10.0) == []
    assert acc.add(SourceKind.SYSTEM, "sobre tu proyecto", 1.2, 2, now=10.5) == []
    assert acc.due(now=11.0) == []  # aún no hubo pausa suficiente
    turns = acc.due(now=11.6)
    assert [t.text for t in turns] == ["Cuéntame sobre tu proyecto"]
    acc.add(SourceKind.SYSTEM, "Hola", 3, 4, now=20.0)
    closed = acc.add(SourceKind.MICROPHONE, "Hola, ¿qué tal?", 4, 5, now=20.2)
    assert [t.source for t in closed] == [SourceKind.SYSTEM]


# ----------------------------------------------------------------- prompts
def test_answer_prompt_contains_profile_context_and_honesty_rule():
    profile = ContextProfile(
        name="p",
        role="Backend",
        about="Python y FastAPI",
        goal="Entrevista",
        language="en",
        tone=Tone.TECHNICAL,
        detail=Detail.DETAILED,
    )
    messages = answer_messages(
        "¿Qué framework usas?",
        profile,
        transcript=[
            TranscriptLine("Interlocutor", "Hola"),
            TranscriptLine("Tú", "Hola"),
        ],
        knowledge="[1] Fuente: cv.md\nFastAPI",
    )
    system, user = messages[0]["content"], messages[1]["content"]
    assert "inglés" in system and "No inventes" in system and "[completar" in system
    assert "Python y FastAPI" in user and "Fuente: cv.md" in user
    assert "Interlocutor: Hola\nTú: Hola" in user
    alt = answer_messages("q", profile, mode="alternative", previous_answer="A")
    assert "otra respuesta distinta" in alt[1]["content"]


def test_transcript_window_keeps_most_recent_lines():
    lines = [
        TranscriptLine("Interlocutor", f"frase {i} " + "x" * 50) for i in range(200)
    ]
    user = answer_messages("q", None, transcript=lines, max_transcript_chars=300)[1][
        "content"
    ]
    assert "frase 199" in user and "frase 0 " not in user


def test_other_prompts():
    tr = translation_messages("Hola", "en", "es", context="Antes")
    assert "inglés" in tr[0]["content"] and "Antes" in tr[1]["content"]
    assert "acuerdos" in summary_messages("A: hola")[0]["content"]
    ch = challenge_messages("Resuelve", ocr_text="2+2", images=["QUJD"])
    assert (
        ch[1]["images"] == ["QUJD"] and "Procedimiento paso a paso" in ch[0]["content"]
    )


# ----------------------------------------------------------------- ajustes
def test_settings_roundtrip_and_policy(tmp_path):
    repo = SettingsRepository(tmp_path / "settings.json")
    assert repo.load().stt_engine == "faster-whisper:base"
    settings = CopilotSettings(
        strategy="speed",
        pinned={"answer": "ollama:qwen3:8b"},
        remote_models={"groq": ["openai/gpt-oss-20b"]},
    )
    repo.save(settings)
    loaded = repo.load()
    policy = loaded.policy(frozenset({"groq"}), frozenset(), frozenset())
    assert policy.strategy is Strategy.SPEED
    assert policy.pinned == {Task.ANSWER: "ollama:qwen3:8b"}
    assert "groq:openai/gpt-oss-20b" in policy.selected
    with pytest.raises(ValueError):
        repo.save(CopilotSettings(sensitivity="extreme"))
    (tmp_path / "settings.json").write_text('{"strategy": "rara"}', encoding="utf-8")
    assert (
        repo.load().strategy == "local_first"
    )  # archivo inválido → valores por defecto


# ------------------------------------------------------------ motor routed
class StubBackend:
    last_rate_limits = {"x-ratelimit-remaining-requests": "5"}

    def __init__(self, text="respuesta", error=None):
        self.text, self.error = text, error

    def chat_stream(
        self, messages, model, temperature=None, on_chunk=None, should_abort=None
    ):
        if self.error:
            raise self.error
        if on_chunk:
            on_chunk(self.text)
        return self.text, {"chars": len(self.text), "eval_count": 3}

    def close(self):
        pass


def test_routed_engine_local_only_and_usage(tmp_path, monkeypatch):
    from app.copilot import engine as engine_module
    from app.providers.usage import UsageLedger

    local = Candidate(
        "ollama",
        "qwen3:8b",
        True,
        AccessTier.LOCAL,
        frozenset({"chat"}),
        3,
        3,
        fits_hardware=True,
    )
    remote = Candidate(
        "groq", "gpt-oss", False, AccessTier.FREE_LIMITED, frozenset({"chat"}), 4, 5
    )
    monkeypatch.setattr(engine_module, "ollama_candidates", lambda o, hw: [local])
    monkeypatch.setattr(
        engine_module, "remote_candidates", lambda sel, has_key: [remote]
    )
    ledger = UsageLedger(tmp_path / "u.json")
    eng = RoutedEngine(
        object(),  # type: ignore[arg-type]
        policy=lambda: RoutingPolicy(
            Strategy.SPEED, remote_allowed=frozenset({"groq"})
        ),
        remote_models=lambda: {"groq": ["gpt-oss"]},
        has_key=lambda p: True,
        ledger=ledger,
        client_factory=lambda c: StubBackend(f"desde {c.provider_id}"),
    )
    assert eng.generate(Task.ANSWER, []).text == "desde groq"
    assert ledger.summary()["groq"]["requests"] == 1
    assert ledger.rate_limits("groq")["x-ratelimit-remaining-requests"] == "5"
    only_local = eng.generate(Task.ANSWER, [], local_only=True)
    assert only_local.text == "desde ollama" and "local" in only_local.model_label
    assert eng.decide(Task.ANSWER, local_only=True).primary == local


# --------------------------------------------------------------- asistente
class FakeEngine:
    def __init__(self, text="Respuesta sugerida.", delay=0.0, error=None):
        self.text, self.delay, self.error = text, delay, error
        self.calls: list[dict] = []
        self.gate = threading.Event()
        self.gate.set()

    def decide(self, task, sensitive=False, local_only=False):  # pragma: no cover
        raise NotImplementedError

    def generate(
        self,
        task,
        messages,
        *,
        sensitive=False,
        on_chunk=None,
        should_abort=None,
        on_notice=None,
        temperature=None,
        local_only=False,
    ):
        self.calls.append(
            {
                "task": task,
                "messages": messages,
                "sensitive": sensitive,
                "local_only": local_only,
            }
        )
        self.gate.wait(5)
        if self.error:
            raise self.error
        for word in self.text.split(" "):
            if should_abort and should_abort():
                raise RoutingCancelled()
            if on_chunk:
                on_chunk(word + " ")
            time.sleep(self.delay)
        return GenerationResult(self.text, "qwen3:8b (local)", False, {})


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def final(text, source=SourceKind.SYSTEM, seg=1):
    return TranscriptEvent(seg, source, text, True, 0.0, 1.0)


def make_assistant(engine, settings=None, profile=None, knowledge=None, clock=None):
    events = {"questions": [], "done": [], "chunks": []}
    assistant = ConversationAssistant(
        engine,
        settings=lambda: settings or CopilotSettings(),
        profile=lambda: profile,
        knowledge=knowledge,
        callbacks=AssistantCallbacks(
            on_question=events["questions"].append,
            on_answer_done=events["done"].append,
            on_answer_chunk=lambda i, d: events["chunks"].append((i, d)),
        ),
        clock=clock or Clock(),
        settle_s=1.0,
    )
    return assistant, events


def wait(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    return predicate()


def test_detects_question_after_pause_and_answers_once():
    clock = Clock()
    engine = FakeEngine()
    assistant, events = make_assistant(engine, clock=clock)
    assistant.start()
    assistant.handle_transcript(final("Podrías contarme"))
    assistant.handle_transcript(final("cuál fue tu mayor desafío técnico", seg=2))
    time.sleep(0.3)
    assert events["questions"] == []  # sin pausa todavía
    clock.now += 1.5
    assert wait(lambda: len(events["done"]) == 1)
    item = events["done"][0]
    assert item.question == "Podrías contarme cuál fue tu mayor desafío técnico"
    assert item.status is QAStatus.DONE and item.answer == "Respuesta sugerida."
    assert events["chunks"] and item.model == "qwen3:8b (local)"
    # Repetición de la misma pregunta: no se responde otra vez.
    assistant.handle_transcript(final("¿Cuál fue tu mayor desafío técnico?", seg=3))
    clock.now += 1.5
    time.sleep(0.4)
    assert len(events["done"]) == 1
    assistant.stop()


def test_statements_and_microphone_are_ignored_by_default():
    clock = Clock()
    assistant, events = make_assistant(FakeEngine(), clock=clock)
    assistant.start()
    assistant.handle_transcript(final("Seguimos con el informe trimestral."))
    assistant.handle_transcript(
        final("¿Te parece bien?", source=SourceKind.MICROPHONE, seg=2)
    )
    clock.now += 2
    time.sleep(0.4)
    assert events["questions"] == []
    assert [line.speaker for line in assistant.transcript()] == ["Interlocutor", "Tú"]
    assistant.stop()


def test_manual_question_regenerate_expand_and_feedback():
    engine = FakeEngine()
    assistant, events = make_assistant(engine)
    assistant.start()
    item_id = assistant.ask("¿Qué es CQRS?")
    assert wait(lambda: len(events["done"]) == 1)
    assistant.regenerate(item_id)
    assert wait(lambda: len(events["done"]) == 2)
    assistant.expand(item_id)
    assert wait(lambda: len(events["done"]) == 3)
    item = assistant.history()[0]
    assert len(item.previous_answers) == 2
    modes = [c["messages"][1]["content"] for c in engine.calls]
    assert "otra respuesta distinta" in modes[1] and "Amplíala" in modes[2]
    assistant.mark_useful(item_id, True)
    assert assistant.history()[0].useful is True
    with pytest.raises(ValueError):
        assistant.ask("  ")
    assistant.stop()


def test_personal_data_forces_local_unless_profile_allows(tmp_path):
    kb = KnowledgeBase(tmp_path / "kb.sqlite3")
    profile = ContextProfile(name="p", about="10 años en banca")
    kb.add_note(profile.id, "CV", "Trabajé en el Banco Nación como analista.")
    engine = FakeEngine()
    assistant, events = make_assistant(engine, profile=profile, knowledge=kb)
    assistant.start()
    assistant.ask("¿Dónde trabajaste como analista?")
    assert wait(lambda: len(events["done"]) == 1)
    call = engine.calls[0]
    assert call["sensitive"] and call["local_only"]
    assert "Banco Nación" in call["messages"][1]["content"]
    assert events["done"][0].sources == ["CV"]
    profile.allow_remote_documents = True
    assistant.ask("¿Y antes?")
    assert wait(lambda: len(events["done"]) == 2)
    assert engine.calls[1]["sensitive"] and not engine.calls[1]["local_only"]
    assistant.stop()


def test_failure_and_cancellation_are_reported():
    engine = FakeEngine(error=RoutingError("Ningún modelo permitido"))
    profile = ContextProfile(name="p", about="datos")
    assistant, events = make_assistant(engine, profile=profile)
    assistant.start()
    assistant.ask("¿Algo?")
    assert wait(lambda: len(events["done"]) == 1)
    failed = events["done"][0]
    assert failed.status is QAStatus.FAILED and "modelo local" in failed.error
    assistant.stop()

    slow = FakeEngine(text="uno dos tres cuatro cinco seis", delay=0.1)
    assistant, events = make_assistant(slow)
    assistant.start()
    assistant.ask("¿Larga?")
    assert wait(lambda: bool(events["chunks"]))
    assistant.cancel_current()
    assert wait(lambda: len(events["done"]) == 1)
    assert events["done"][0].status is QAStatus.CANCELLED
    assistant.stop()


def test_provider_errors_in_engine_surface_as_failures():
    engine = FakeEngine(error=RoutingError("groq: 429"))
    assistant, events = make_assistant(engine)
    assistant.start()
    assistant.ask("¿Algo?")
    assert wait(lambda: len(events["done"]) == 1)
    assert "429" in events["done"][0].error
    assistant.stop()
    assert isinstance(ProviderError("x"), RuntimeError)


def test_preload_and_warm_up(monkeypatch):
    import json as _json

    import httpx

    from app.copilot import engine as engine_module
    from app.ollama_client import OllamaClient
    from tests.conftest import make_config

    seen = []

    def handler(request):
        seen.append((request.url.path, _json.loads(request.content)))
        return httpx.Response(200, json={"done": True})

    client = OllamaClient(make_config(), transport=httpx.MockTransport(handler))
    assert client.preload("qwen3:8b", keep_alive="10m") >= 0
    assert seen[-1] == ("/api/generate", {"model": "qwen3:8b", "keep_alive": "10m"})

    local = Candidate(
        "ollama",
        "qwen3:8b",
        True,
        AccessTier.LOCAL,
        frozenset({"chat"}),
        fits_hardware=True,
    )
    monkeypatch.setattr(engine_module, "ollama_candidates", lambda o, hw: [local])
    monkeypatch.setattr(engine_module, "remote_candidates", lambda s, h: [])
    eng = RoutedEngine(
        client, policy=RoutingPolicy, remote_models=dict, has_key=lambda p: False
    )
    assert "listo" in eng.warm_up()
    monkeypatch.setattr(engine_module, "ollama_candidates", lambda o, hw: [])
    eng.invalidate()
    assert "No hay" in eng.warm_up()
