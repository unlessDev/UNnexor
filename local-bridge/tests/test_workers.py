"""Pruebas del GenerationWorker con Ollama simulado (hilo real QThread).

Regresión clave: los workers deben ARRANCARSE (window.py llama a start()).
Sin start(), la cola nunca se consume (bug detectado en E2E).
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from app.config import Config
from app.desktop.workers import GenerationWorker
from app.ollama_client import OllamaClient
from app.queue_manager import Bus, ClearConversation, RuntimeState, SelectionRequest

pytest.importorskip("PySide6", reason="PySide6 no instalada")


def ndjson(*objects: dict) -> bytes:
    return ("".join(json.dumps(o) + "\n" for o in objects)).encode("utf-8")


def make_ollama(handler, cfg: Config | None = None) -> OllamaClient:
    cfg = cfg or Config()
    cfg.token = ""
    return OllamaClient(cfg, transport=httpx.MockTransport(handler))


def pump(app, until, timeout: float = 6.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if until():
            return
        time.sleep(0.05)


def test_worker_streams_and_builds_history(qapp):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            content=ndjson(
                {"message": {"content": "Hola "}, "done": False},
                {"message": {"content": "mundo"}, "done": True, "eval_count": 2},
            ),
        )

    cfg = Config()
    cfg.token = ""
    bus, state = Bus(), RuntimeState(model="m")
    worker = GenerationWorker(cfg, bus, state, make_ollama(handler, cfg))
    events: list[tuple] = []
    worker.queryStarted.connect(lambda t: events.append(("started", t)))
    worker.chunkReceived.connect(lambda d: events.append(("chunk", d)))
    worker.generationFinished.connect(lambda s: events.append(("finished", s)))
    worker.start()  # ← sin esta llamada el worker no consume la cola
    try:
        bus.publish(SelectionRequest(text="hola", source_url="u", received_at=""))
        pump(qapp, lambda: any(e[0] == "finished" for e in events))
        assert ("started", "hola") in events
        chunks = [e[1] for e in events if e[0] == "chunk"]
        assert "".join(chunks) == "Hola mundo"
        assert state.history_count() == 2  # user + assistant
        assert state.is_generating() is False
    finally:
        worker.stop()
        worker.wait(3000)


def test_worker_clear_aborts_and_resets(qapp):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=ndjson(
                {"message": {"content": "a"}, "done": False},
                {"message": {"content": "b"}, "done": False},
                {"message": {"content": "c"}, "done": False},
                {"message": {"content": "d"}, "done": False},
            ),
        )

    cfg = Config()
    cfg.token = ""
    bus, state = Bus(), RuntimeState(model="m")
    worker = GenerationWorker(cfg, bus, state, make_ollama(handler, cfg))
    events: list[str] = []
    worker.chunkReceived.connect(lambda d: events.append(d))
    worker.generationCancelled.connect(lambda: events.append("__cancelled__"))
    worker.conversationCleared.connect(lambda: events.append("__cleared__"))
    worker.start()
    try:
        bus.publish(SelectionRequest(text="pregunta", source_url="", received_at=""))
        # Limpiar mientras genera → debe abortar el stream y vaciar historial.
        state.request_clear()
        bus.publish(ClearConversation())
        pump(qapp, lambda: "__cleared__" in events)
        assert "__cleared__" in events
        assert state.history_count() == 0
        assert state.is_generating() is False
    finally:
        worker.stop()
        worker.wait(3000)


def test_worker_model_failure_reports_and_keeps_history_clean(qapp):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model 'x' not found"})

    cfg = Config()
    cfg.token = ""
    bus, state = Bus(), RuntimeState(model="x")
    worker = GenerationWorker(cfg, bus, state, make_ollama(handler, cfg))
    failures: list[tuple] = []
    worker.generationFailed.connect(lambda msg, kind: failures.append((msg, kind)))
    worker.start()
    try:
        bus.publish(SelectionRequest(text="hola", source_url="", received_at=""))
        pump(qapp, lambda: bool(failures))
        assert failures and failures[0][1] == "model"
        assert "ollama pull" in failures[0][0]
        # El turno user fallido NO queda en el historial.
        assert state.history_count() == 0
    finally:
        worker.stop()
        worker.wait(3000)
