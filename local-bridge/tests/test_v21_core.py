"""Pruebas de 2.1.0: migración Extdator → UNnexor, modalidades de la
selección, progreso real de ``ollama pull`` y segmentación de respuestas."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import migration, paths
from app.ollama_pull import (
    COMPLETED,
    DOWNLOADING,
    FINALIZING,
    PREPARING,
    VERIFYING,
    PullTracker,
    phase_for_status,
)
from app.queue_manager import Bus, RuntimeState, SelectionRequest
from app.server import create_app

ROOT = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------------ migración
def _legacy_tree(root: Path) -> None:
    (root / "copilot" / "profiles").mkdir(parents=True)
    (root / "models" / "whisper-small").mkdir(parents=True)
    (root / "logs").mkdir()
    (root / ".env").write_text("BRIDGE_TOKEN=abc\n", encoding="utf-8")
    (root / "user_settings.json").write_text('{"ollama_model": "viejo"}', "utf-8")
    (root / "copilot" / "profiles" / "p.json").write_text("{}", encoding="utf-8")
    (root / "models" / "whisper-small" / "model.bin").write_bytes(b"x" * 4096)
    (root / "logs" / "bridge.log").write_text("log antiguo", encoding="utf-8")


def test_migration_copies_without_deleting_or_overwriting(tmp_path):
    legacy, target = tmp_path / "Extdator", tmp_path / "UNnexor"
    _legacy_tree(legacy)
    target.mkdir()
    # Un archivo nuevo ya existente NUNCA se pisa con la versión antigua.
    (target / "user_settings.json").write_text('{"ollama_model": "nuevo"}', "utf-8")

    report = migration.migrate_legacy_data(legacy, target)

    assert report.status == "completed" and report.ok
    assert report.copied == 3 and report.kept_existing == 1
    assert (target / ".env").read_text(encoding="utf-8") == "BRIDGE_TOKEN=abc\n"
    assert (target / "models" / "whisper-small" / "model.bin").stat().st_size == 4096
    assert json.loads((target / "user_settings.json").read_text("utf-8")) == {
        "ollama_model": "nuevo"
    }
    assert not (target / "logs").exists()  # los registros antiguos no se migran
    # Origen intacto (más una nota explicativa).
    assert (legacy / ".env").is_file() and (legacy / "logs" / "bridge.log").is_file()
    assert (legacy / migration.NOTE_NAME).is_file()
    marker = json.loads((target / migration.MARKER_NAME).read_text("utf-8"))
    assert marker["status"] == "completed" and marker["copied"] == 3
    assert not list(target.rglob("*" + migration.TMP_SUFFIX))


def test_migration_is_idempotent_and_retries_after_errors(tmp_path, monkeypatch):
    legacy, target = tmp_path / "Extdator", tmp_path / "UNnexor"
    _legacy_tree(legacy)
    real_copy = migration._copy_verified

    def flaky(source, dest):
        if source.name == "p.json":
            raise OSError("archivo bloqueado")
        return real_copy(source, dest)

    monkeypatch.setattr(migration, "_copy_verified", flaky)
    first = migration.migrate_legacy_data(legacy, target)
    assert first.status == "partial" and not first.ok
    assert any("p.json" in e for e in first.errors)
    assert not (legacy / migration.NOTE_NAME).exists()

    monkeypatch.setattr(migration, "_copy_verified", real_copy)
    second = migration.migrate_legacy_data(legacy, target)
    assert second.status == "completed" and second.copied == 1
    third = migration.migrate_legacy_data(legacy, target)
    assert third.status == "already_done"


def test_migration_not_needed_and_override(tmp_path, monkeypatch):
    report = migration.migrate_legacy_data(tmp_path / "nada", tmp_path / "UNnexor")
    assert report.status == "not_needed"
    monkeypatch.setenv("UNNEXOR_DATA_DIR", str(tmp_path / "portable"))
    assert migration.migrate_legacy_data().status == "skipped_override"


def test_env_overrides_accept_new_and_legacy_names(monkeypatch, tmp_path):
    monkeypatch.delenv("UNNEXOR_DATA_DIR", raising=False)
    monkeypatch.setenv("EXTDATOR_DATA_DIR", str(tmp_path / "old"))
    assert paths.data_dir() == tmp_path / "old"
    monkeypatch.setenv("UNNEXOR_DATA_DIR", str(tmp_path / "new"))
    assert paths.data_dir() == tmp_path / "new"  # el nombre nuevo gana


def test_keyring_reads_and_copies_legacy_credentials(monkeypatch):
    import keyring

    from app.providers import manager

    store: dict[tuple[str, str], str] = {("Extdator", "provider-key:groq"): "sk-1"}
    monkeypatch.setattr(keyring, "get_password", lambda s, a: store.get((s, a)))
    monkeypatch.setattr(
        keyring, "set_password", lambda s, a, v: store.__setitem__((s, a), v)
    )

    def delete(s, a):
        store.pop((s, a))

    monkeypatch.setattr(keyring, "delete_password", delete)
    assert manager.get_key("groq") == "sk-1"
    assert store[("UNnexor", "provider-key:groq")] == "sk-1"
    manager.delete_key("groq")
    assert store == {}


# ---------------------------------------------- Consultar / Enviar directamente
def _client(make_config, ollama_stub):
    bus = Bus()
    app = create_app(make_config(), bus, RuntimeState(model="m"), ollama_stub())
    return TestClient(app), bus


@pytest.fixture
def selection_client():
    from tests.test_unnexor_identity import AUTH, make_config, ollama_stub

    client, bus = _client(make_config, ollama_stub)
    return client, bus, AUTH


def test_ask_and_direct_modes_reach_distinct_flows(selection_client):
    client, bus, auth = selection_client
    with client:
        direct = client.post(
            "/api/selection", json={"text": "hola", "mode": "direct"}, headers=auth
        )
        ask = client.post(
            "/api/selection",
            json={"text": "hola", "question": "Tradúcelo al inglés", "mode": "ask"},
            headers=auth,
        )
    assert direct.status_code == 202 and direct.json()["mode"] == "direct"
    assert ask.status_code == 202 and ask.json()["mode"] == "ask"
    assert ask.json()["question_received"] is True
    first, second = bus.consume(timeout=1), bus.consume(timeout=1)
    assert first.mode == "direct" and first.prompt() == "hola"
    assert second.mode == "ask" and "Tradúcelo al inglés" in second.prompt()
    assert second.prompt() != first.prompt()


def test_inconsistent_modes_are_rejected(selection_client):
    client, _bus, auth = selection_client
    with client:
        empty_ask = client.post(
            "/api/selection", json={"text": "t", "mode": "ask"}, headers=auth
        )
        direct_with_q = client.post(
            "/api/selection",
            json={"text": "t", "question": "¿?", "mode": "direct"},
            headers=auth,
        )
        bogus = client.post(
            "/api/selection", json={"text": "t", "mode": "otro"}, headers=auth
        )
    assert empty_ask.status_code == 422
    assert direct_with_q.status_code == 422
    assert bogus.status_code == 422


def test_extension_background_forwards_question_and_mode():
    source = (ROOT / "browser-extension" / "background.js").read_text("utf-8")
    payload = source[source.index("const payload = {") :]
    payload = payload[: payload.index("};")]
    # Causa raíz del fallo de «Consultar»: la pregunta no viajaba al puente.
    assert "question," in payload and "mode," in payload
    assert "question_received" in source


def test_followup_ask_keeps_conversation_context():
    from app.conversation import Conversation

    conv = Conversation(system_prompt="s", max_messages=20)
    first = SelectionRequest(text="La fotosíntesis…", question="Resúmelo")
    conv.add_user(first.prompt())
    conv.add_assistant("Resumen…")
    second = SelectionRequest(text="La fotosíntesis…", question="¿Y en una frase?")
    conv.add_user(second.prompt())
    payload = conv.payload()
    assert [m["role"] for m in payload] == ["system", "user", "assistant", "user"]
    assert "en esta conversación" in second.prompt()


# ----------------------------------------------------------- progreso de pull
def test_phase_mapping():
    assert phase_for_status("pulling manifest") == PREPARING
    assert phase_for_status("pulling 6a0746a1ec1a") == DOWNLOADING
    assert phase_for_status("verifying sha256 digest") == VERIFYING
    assert phase_for_status("writing manifest") == FINALIZING
    assert phase_for_status("success") == COMPLETED


def test_tracker_aggregates_layers_and_never_jumps_back():
    clock = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]).__next__
    tracker = PullTracker("ollama:m", clock=clock)
    tracker.update("pulling manifest")
    a = tracker.update("pulling aaa", 1000, 500, "sha256:aaa")
    assert a.fraction == pytest.approx(0.5)
    b = tracker.update("pulling aaa", 1000, 1000, "sha256:aaa")
    # Capa pequeña nueva: con el cálculo antiguo la barra volvía a 0 %.
    c = tracker.update("pulling bbb", 10, 0, "sha256:bbb")
    assert c.fraction is not None and c.fraction >= 0.98
    assert c.layers_total == 2 and c.layers_done == 1
    d = tracker.update("verifying sha256 digest")
    assert d.phase == VERIFYING and d.fraction is not None and d.fraction < 1.0
    assert b.speed_bps is not None and b.speed_bps > 0
    done = tracker.finish(COMPLETED)
    assert done.fraction == 1.0 and done.percent == 100
    assert "Completado" in done.describe()


def test_tracker_without_total_is_indeterminate():
    snap = PullTracker("ollama:m").update("pulling manifest")
    assert snap.fraction is None and snap.percent is None
    assert snap.describe().startswith("Preparando descarga")


def test_pull_stream_without_success_is_an_error():
    from app.ollama_client import OllamaClient, OllamaResponseError
    from tests.test_hardware_models import make_cfg, ndjson

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=ndjson(
                {"status": "pulling manifest"},
                {"status": "pulling a", "digest": "a", "total": 10, "completed": 3},
            ),
        )

    client = OllamaClient(make_cfg(), transport=httpx.MockTransport(handler))
    lines: list[dict] = []
    with pytest.raises(OllamaResponseError, match="interrumpió"):
        client.pull_stream("m", on_line=lines.append)
    assert lines[-1]["digest"] == "a"


# --------------------------------------------------------- segmentación
def test_split_segments_math_code_and_currency():
    from app.desktop.rich_text import CODE, MATH_DISPLAY, MATH_INLINE, split_segments

    text = (
        "Sea $x^2$ y $$\\frac{a}{b}$$ pero cuesta $5 y $10.\n"
        "```python\nprecio = '$x$'\n```\n"
        "`$y$` y \\(\\alpha\\) y \\begin{cases} a \\\\ b \\end{cases}"
    )
    segs = split_segments(text)
    kinds = [(s.kind, s.text) for s in segs if s.kind != "text"]
    assert (MATH_INLINE, "x^2") in kinds
    assert (MATH_DISPLAY, "\\frac{a}{b}") in kinds
    assert (CODE, "precio = '$x$'") in kinds
    assert (MATH_INLINE, "\\alpha") in kinds
    assert any(k == MATH_DISPLAY and "cases" in t for k, t in kinds)
    # Ni la moneda ni el código en línea son fórmulas.
    assert not any("5 y" in t for k, t in kinds)
    assert not any(t == "y" for k, t in kinds)


def test_unclosed_code_fence_while_streaming():
    from app.desktop.rich_text import CODE, split_segments

    segs = split_segments("Mira:\n```js\nconst a = 1;")
    assert segs[-1].kind == CODE and segs[-1].closed is False
    assert segs[-1].lang == "js"


def test_rich_renderer_is_safe_and_renders(qapp):
    from app.desktop.rich_text import RichRenderer, highlight_code

    result = RichRenderer().render(
        "<script>alert(1)</script> $x = \\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}$\n\n"
        "```python\ndef f():\n    return 1\n```\n\n$\\frac{1}{$"
    )
    assert "<script>" not in result.html and "&lt;script&gt;" in result.html
    assert len(result.images) >= 1 and result.codes == ["def f():\n    return 1"]
    assert 'href="unxcopy:0"' in result.html
    html_code, label = highlight_code("SELECT 1 FROM t;", "sql")
    assert "<span" in html_code and label == "sql"


def test_rich_browser_blocks_foreign_resources(qapp):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QTextDocument

    from app.desktop.rich_text import RichTextBrowser

    browser = RichTextBrowser()
    browser.set_rich_text("![x](file:///C:/Windows/win.ini) $a^2$")
    image_kind = QTextDocument.ResourceType.ImageResource.value
    assert browser.loadResource(image_kind, QUrl("file:///C:/Windows/win.ini")) is None
    assert browser.loadResource(image_kind, QUrl("unxmath:0")) is not None
