"""Rebranding UNnexor: identidad gráfica, consulta sobre la selección,
presentación en terminal, consola de registros y supervisión de Ollama."""

from __future__ import annotations

import json
import logging
import struct
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.banner import render
from app.ollama_service import OllamaSupervisor
from app.queue_manager import Bus, RuntimeState, SelectionRequest
from app.server import MAX_QUESTION_LENGTH, create_app
from tests.conftest import make_config
from tests.test_server_api import AUTH, ollama_stub

ROOT = Path(__file__).resolve().parents[2]
SVG_NS = "{http://www.w3.org/2000/svg}"


# ------------------------------------------------------------ identidad
@pytest.mark.parametrize(
    "name", ["unnexor-x.svg", "unnexor-icon.svg", "unnexor-badge.svg"]
)
def test_brand_svgs_are_real_vectors(name):
    path = ROOT / "branding" / name
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    assert root.tag == f"{SVG_NS}svg" and root.get("viewBox")
    # Vectorial de verdad: trazados, ni imágenes incrustadas ni filtros.
    assert root.findall(f".//{SVG_NS}image") == []
    assert root.findall(f".//{SVG_NS}filter") == []
    parts = {p.get("data-part") for p in root.iter(f"{SVG_NS}path")}
    assert parts == {"upper-left", "lower-right", "slash", "slash-inner", "slash-floor"}


def test_bridge_svg_is_the_master():
    master = (ROOT / "branding" / "unnexor-x.svg").read_text(encoding="utf-8")
    bundled = (ROOT / "local-bridge" / "assets" / "unnexor-x.svg").read_text(
        encoding="utf-8"
    )
    assert master == bundled


def _png_size(path: Path) -> tuple[int, int, int]:
    """(ancho, alto, tipo de color) leídos de la cabecera IHDR (sin Pillow)."""
    data = path.read_bytes()[:33]
    assert data[:8] == b"PNG

"
    width, height = struct.unpack(">II", data[16:24])
    return width, height, data[25]


def test_icon_files_exist_and_ico_is_multiresolution():
    for size in (16, 32, 48, 128):
        width, height, color = _png_size(
            ROOT / "browser-extension" / "assets" / f"icon{size}.png"
        )
        assert (width, height) == (size, size) and color == 6  # RGBA
    data = (ROOT / "local-bridge" / "assets" / "unnexor.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert reserved == 0 and kind == 1
    sizes = set()
    for i in range(count):
        width, height = data[6 + 16 * i], data[7 + 16 * i]
        sizes.add((width or 256, height or 256))  # 0 significa 256
    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= sizes


def test_extension_uses_brand_and_keeps_protocol():
    manifest = json.loads(
        (ROOT / "browser-extension" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["name"] == "UNnexor"
    for rel in manifest["icons"].values():
        assert (ROOT / "browser-extension" / rel).is_file()
    content = (ROOT / "browser-extension" / "content.js").read_text(encoding="utf-8")
    assert 'data-part=\\"slash\\"' in content  # la X generada, en línea
    assert "extdator:send-selection" in content  # protocolo interno intacto
    assert "Enviar directamente" in content and "Consultar" in content


def test_no_visible_old_name_in_user_interface():
    offenders = []
    for folder in ("local-bridge/app", "browser-extension"):
        for path in (ROOT / folder).rglob("*"):
            if path.suffix not in {".py", ".js", ".html", ".json"}:
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if "Extdator" not in line:
                    continue
                allowed = (
                    'QSettings("Extdator"',
                    'SETTINGS_GROUP = ("Extdator"',
                    'APP_DIR_NAME = "Extdator"',
                    'SERVICE = "Extdator"',
                    'setOrganizationName("Extdator")',
                    "unlessDev/Extdator",
                    "La organización",
                    "Extdator ->",
                    "%LOCALAPPDATA%",
                    "desde Extdator",
                )
                if not any(token in line for token in allowed):
                    offenders.append(f"{path.name}: {line.strip()}")
    assert offenders == []


# ------------------------------------------------- consulta sobre selección
def test_selection_request_prompt_and_display():
    plain = SelectionRequest(text="Texto")
    assert plain.prompt() == "Texto" and plain.display() == "Texto"
    asked = SelectionRequest(text="El aprendizaje automático…", question="¿Ejemplo?")
    prompt = asked.prompt()
    assert prompt.startswith("¿Ejemplo?") and "El aprendizaje automático…" in prompt
    assert asked.display().startswith("¿Ejemplo?\n\n› El aprendizaje")


def _client():
    bus = Bus()
    app = create_app(make_config(), bus, RuntimeState(model="m"), ollama_stub())
    return TestClient(app), bus


def test_server_forwards_question():
    client, bus = _client()
    with client:
        response = client.post(
            "/api/selection",
            json={"text": "contenido", "question": "  ¿Qué significa?  "},
            headers=AUTH,
        )
    assert response.status_code == 202
    assert response.json()["question_received"] is True
    event = bus.consume(timeout=1)
    assert event.question == "¿Qué significa?" and event.text == "contenido"


def test_server_direct_send_is_unchanged():
    client, bus = _client()
    with client:
        response = client.post("/api/selection", json={"text": "x"}, headers=AUTH)
    assert response.status_code == 202
    assert response.json()["question_received"] is False
    assert bus.consume(timeout=1).question == ""


def test_server_rejects_too_long_question():
    client, _bus = _client()
    with client:
        response = client.post(
            "/api/selection",
            json={"text": "x", "question": "q" * (MAX_QUESTION_LENGTH + 1)},
            headers=AUTH,
        )
    assert response.status_code == 422


# ------------------------------------------------------------ terminal
def test_banner_plain_and_color():
    plain = render(color=False)
    assert "\x1b[" not in plain
    assert "A I   B R I D G E" in plain and "by UnlessDev" in plain
    colored = render(color=True)
    assert "\x1b[38;2;" in colored and "\x1b[0m" in colored


def test_log_buffer_captures_records(qapp):
    from app.desktop.log_console import LogBuffer

    buffer = LogBuffer(max_lines=3)
    seen: list[str] = []
    buffer.emitter.line.connect(seen.append)
    logger = logging.getLogger("test.unnexor.console")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        for i in range(5):
            logger.info("HTTP Request: GET /health %d", i)
    finally:
        logger.removeHandler(buffer)
    lines = buffer.snapshot()
    assert len(lines) == 3 and lines[-1].endswith("/health 4")
    assert len(seen) == 5


def test_log_console_hidden_by_default(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from app.desktop import log_console

    ini = str(tmp_path / "c.ini")
    monkeypatch.setattr(
        log_console, "QSettings", lambda *_a: QSettings(ini, QSettings.Format.IniFormat)
    )
    window = log_console.LogConsoleWindow(log_console.LogBuffer())
    assert window.show_at_start() is False and not window.isVisible()
    window.toggle()
    assert window.isVisible()
    window.toggle()
    assert not window.isVisible()
    window.close()


# ------------------------------------------------------------ Ollama
class FakeProcess:
    pid = 4321

    def __init__(self) -> None:
        self.alive = True

    def poll(self):
        return None if self.alive else 0

    def wait(self, timeout=None):
        self.alive = False
        return 0


def _get(responses):
    def get(url, timeout):
        ok = responses.pop(0) if responses else True
        if ok:
            return httpx.Response(200, json={"version": "0.9.1"})
        raise httpx.ConnectError("down")

    return get


def test_running_ollama_is_never_touched(monkeypatch):
    launched = []
    supervisor = OllamaSupervisor(
        "http://127.0.0.1:11434",
        find=lambda: "ollama.exe",
        popen=lambda *a, **k: launched.append(a),
        http_get=_get([True]),
    )
    result = supervisor.ensure_running()
    assert result.reachable and not result.started_by_us and launched == []
    assert supervisor.stop() is False


def test_starts_and_stops_only_its_own_process(monkeypatch):
    process = FakeProcess()
    killed = []
    monkeypatch.setattr(
        subprocess, "run", lambda args, **_k: killed.append(args) or None
    )
    supervisor = OllamaSupervisor(
        "http://127.0.0.1:11434",
        find=lambda: "C:/Ollama/ollama.exe",
        popen=lambda *a, **k: process,
        http_get=_get([False, False, True]),
    )
    result = supervisor.ensure_running()
    assert result.reachable and result.started_by_us and result.version == "0.9.1"
    assert supervisor.owns_process
    assert supervisor.stop() is True
    assert killed and killed[0][:3] == ["taskkill", "/PID", "4321"]
    assert supervisor.stop() is False  # idempotente


def test_autostart_respects_settings_and_remote_hosts():
    off = OllamaSupervisor(
        "http://127.0.0.1:11434", autostart=False, http_get=_get([False])
    )
    assert "desactivado" in off.ensure_running().detail
    remote = OllamaSupervisor("http://192.168.1.9:11434", http_get=_get([False]))
    assert "remoto" in remote.ensure_running().detail
    missing = OllamaSupervisor(
        "http://127.0.0.1:11434", find=lambda: "", http_get=_get([False])
    )
    assert "no está instalado" in missing.ensure_running().detail


# ------------------------------------------------------------ ajustes
def test_settings_checkboxes_reflect_current_config(qapp):
    from PySide6.QtWidgets import QCheckBox

    from app.desktop.dialogs import SettingsDialog

    cfg = make_config()
    cfg.always_on_top = True
    cfg.log_privacy = True
    cfg.ollama_autostart = False
    dialog = SettingsDialog(cfg)
    boxes = {k: w for k, w in dialog._widgets.items() if isinstance(w, QCheckBox)}
    assert boxes["always_on_top"].isChecked()
    assert boxes["log_privacy"].isChecked()
    assert not boxes["ollama_autostart"].isChecked()
    dialog.close()


def test_app_icon_loads(qapp):
    from app.main import app_icon

    assert not app_icon().isNull()
