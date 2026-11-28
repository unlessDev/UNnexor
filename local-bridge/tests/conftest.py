"""Configuración compartida de las pruebas.

Las pruebas de lógica GUI usan QT_QPA_PLATFORM=offscreen; la prueba de
integración de Hover Scroll se ejecuta en un proceso aparte con la
plataforma real de Windows.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BRIDGE_DIR = Path(__file__).resolve().parent.parent
if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

import pytest  # noqa: E402

from app import paths as _paths  # noqa: E402
from app.config import Config  # noqa: E402
from app.queue_manager import Bus, RuntimeState  # noqa: E402

REAL_MODELS_DIR = _paths.models_dir()


@pytest.fixture(autouse=True)
def _isolated_localappdata(tmp_path_factory, monkeypatch):
    """Ninguna prueba debe leer, migrar ni escribir en el %LOCALAPPDATA% real
    (``main()`` ejecuta la migración Extdator → UNnexor al arrancar)."""
    root = tmp_path_factory.mktemp("localappdata")
    monkeypatch.setenv("LOCALAPPDATA", str(root))
    # Los modelos de voz ya descargados se siguen encontrando (solo lectura)
    # para las pruebas de integración marcadas con skipif.
    if not os.environ.get("UNNEXOR_MODELS_DIR"):
        monkeypatch.setenv("UNNEXOR_MODELS_DIR", str(REAL_MODELS_DIR))
    yield


def make_config(**overrides) -> Config:
    """Config de prueba con token fijo y sin tocar el .env real."""
    cfg = Config()
    cfg.token = "test-token-123"
    cfg.auth_required = True
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


@pytest.fixture()
def bus() -> Bus:
    return Bus()


@pytest.fixture()
def state() -> RuntimeState:
    return RuntimeState(model="test-model")


@pytest.fixture()
def cfg() -> Config:
    return make_config()


@pytest.fixture(scope="session")
def qapp():
    """QApplication compartida para pruebas Qt (offscreen)."""
    pytest.importorskip("PySide6", reason="PySide6 no instalada")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
