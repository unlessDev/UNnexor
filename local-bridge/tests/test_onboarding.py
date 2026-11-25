"""Pruebas del asistente de primer inicio (Qt offscreen).

Cubren la decisión de mostrarlo una sola vez, la comprobación de requisitos
sin red real y los tres pasos del diálogo, incluido el emparejamiento.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 no instalada")

from app.config import Config
from app.desktop.onboarding import (
    ONBOARDING_SEEN_KEY,
    OnboardingDialog,
    RequirementsCheckTask,
    model_is_installed,
    should_show_onboarding,
)
from app.ollama_client import OllamaError
from app.pairing import PairingManager


class FakeOllama:
    def __init__(
        self,
        *,
        reachable: bool = True,
        detail: str = "versión 0.12.3",
        models: list[str] | None = None,
        list_error: str | None = None,
    ) -> None:
        self.reachable = reachable
        self.detail = detail
        self.models = models if models is not None else ["llama3:latest"]
        self.list_error = list_error

    def is_reachable(self, timeout: float = 2.0) -> tuple[bool, str]:
        return self.reachable, self.detail

    def list_models(self) -> list[str]:
        if self.list_error:
            raise OllamaError(self.list_error)
        return self.models


class FakePairing:
    def __init__(self, *, confirmed: bool = False) -> None:
        self.confirmed = confirmed
        self.code = "123456" if not confirmed else ""
        self.left = 420
        self.revoked = 0

    def current_code(self) -> str:
        if not self.code:
            self.code = "654321"
        return self.code

    def seconds_left(self) -> int:
        return self.left

    def is_confirmed(self) -> bool:
        return self.confirmed

    def revoke(self) -> None:
        self.revoked += 1
        self.confirmed = False
        self.code = ""
        self.left = 600


def make_config(model: str = "llama3") -> Config:
    cfg = Config()
    cfg.token = "test-token-123"
    cfg.ollama_model = model
    return cfg


def make_dialog(pairing=None, ollama=None) -> OnboardingDialog:
    return OnboardingDialog(
        make_config(),
        ollama or FakeOllama(),
        pairing if pairing is not None else FakePairing(),
    )


def test_onboarding_seen_key_is_stable() -> None:
    assert ONBOARDING_SEEN_KEY == "onboarding/seen"


def test_model_is_installed_tolerates_latest_suffix() -> None:
    assert model_is_installed(["llama3:latest"], "llama3")
    assert model_is_installed(["llama3:latest"], "llama3:latest")
    assert model_is_installed(["qwen3:8b"], "qwen3:8b")
    assert not model_is_installed(["qwen3:8b"], "qwen3:14b")
    assert not model_is_installed([], "llama3")
    assert not model_is_installed(["llama3"], "")


def test_should_show_onboarding_only_once_and_not_offscreen() -> None:
    assert should_show_onboarding(seen=False, platform_name="windows")
    assert not should_show_onboarding(seen=True, platform_name="windows")
    assert not should_show_onboarding(seen=False, platform_name="offscreen")


def test_requirements_task_reports_ready() -> None:
    task = RequirementsCheckTask(FakeOllama(), "llama3")
    results: list[tuple] = []
    task.signals.finished.connect(lambda *args: results.append(args))
    task.run()
    assert results == [(True, "versión 0.12.3", True, "«llama3» está instalado.")]


def test_requirements_task_reports_unreachable_ollama() -> None:
    task = RequirementsCheckTask(
        FakeOllama(reachable=False, detail="No se pudo conectar."), "llama3"
    )
    results: list[tuple] = []
    task.signals.finished.connect(lambda *args: results.append(args))
    task.run()
    assert results == [(False, "No se pudo conectar.", False, "")]


def test_requirements_task_reports_listing_error() -> None:
    task = RequirementsCheckTask(FakeOllama(list_error="fallo al listar"), "llama3")
    results: list[tuple] = []
    task.signals.finished.connect(lambda *args: results.append(args))
    task.run()
    assert results == [
        (
            True,
            "versión 0.12.3",
            False,
            "No se pudieron listar los modelos: fallo al listar",
        )
    ]


def test_requirements_task_reports_empty_model() -> None:
    task = RequirementsCheckTask(FakeOllama(), "")
    results: list[tuple] = []
    task.signals.finished.connect(lambda *args: results.append(args))
    task.run()
    assert results == [(True, "versión 0.12.3", False, "No hay un modelo configurado.")]


def test_dialog_starts_on_welcome_and_advances(qapp) -> None:
    dialog = make_dialog()
    assert dialog._step == 0
    assert dialog._next.text() == "Continuar"
    assert dialog._back.isHidden()

    dialog._set_step(1, check=False)
    assert dialog._step == 1
    assert not dialog._back.isHidden()
    assert dialog._next.text() == "Continuar"

    dialog._set_step(2, check=False)
    assert dialog._step == 2
    assert dialog._next.text() == "Finalizar"


def test_dialog_reports_ready_requirements(qapp) -> None:
    dialog = make_dialog()
    dialog._set_step(1, check=False)
    dialog._apply_check(True, "versión 0.12.3", True, "«llama3» está instalado.")
    assert "✓" in dialog._ollama_status.text()
    assert "versión 0.12.3" in dialog._ollama_status.text()
    assert "✓" in dialog._model_status.text()
    assert "Todo listo" in dialog._requirements_note.text()


def test_dialog_reports_missing_model(qapp) -> None:
    dialog = make_dialog()
    dialog._set_step(1, check=False)
    dialog._apply_check(True, "versión 0.12.3", False, "«llama3» no está instalado.")
    assert "!" in dialog._model_status.text()
    assert "no está instalado" in dialog._model_status.text()
    assert "Elige otro modelo" in dialog._requirements_note.text()


def test_dialog_reports_unreachable_ollama(qapp) -> None:
    dialog = make_dialog()
    dialog._set_step(1, check=False)
    dialog._apply_check(False, "No se pudo conectar.", False, "")
    assert "✕" in dialog._ollama_status.text()
    assert "cuando Ollama responda" in dialog._model_status.text()
    assert "ollama.com/download" in dialog._requirements_note.text()


def test_pairing_step_shows_code_and_detects_confirmation(qapp) -> None:
    pairing = FakePairing()
    dialog = make_dialog(pairing=pairing)
    dialog._set_step(2, check=False)

    assert dialog._code.text() == "123456"
    assert not dialog._code.isHidden()
    assert "Esperando" in dialog._pair_status.text()

    pairing.confirmed = True
    dialog._tick_pairing()

    assert "Extensión vinculada" in dialog._pair_status.text()
    assert "ya está vinculada" in dialog._pair_intro.text()
    assert dialog._code.isHidden()
    assert dialog._pairing_note.isHidden()
    assert not dialog._new_code_button.isHidden()
    assert dialog._next.text() == "Finalizar"
    assert not dialog._pair_timer.isActive()


def test_pairing_step_can_generate_a_new_code_after_linking(qapp) -> None:
    pairing = FakePairing(confirmed=True)
    dialog = make_dialog(pairing=pairing)
    dialog._set_step(2, check=False)
    assert dialog._code.isHidden()

    dialog._new_code()

    assert pairing.revoked == 1
    assert not pairing.is_confirmed()
    assert dialog._code.text() == "654321"
    assert not dialog._code.isHidden()
    assert not dialog._pairing_note.isHidden()
    assert "Conectar con la aplicación" in dialog._pair_intro.text()


def test_pairing_step_rotates_expired_code(qapp) -> None:
    pairing = FakePairing()
    dialog = make_dialog(pairing=pairing)
    dialog._set_step(2, check=False)
    pairing.left = 0
    dialog._tick_pairing()
    assert pairing.revoked == 1
    assert dialog._code.text() == "654321"


def test_pairing_step_without_pairing_explains_why(qapp) -> None:
    dialog = OnboardingDialog(make_config(), FakeOllama(), None)
    dialog._set_step(2, check=False)
    assert "no disponible" in dialog._pair_status.text()
    assert "no puede vincularse" in dialog._pair_intro.text()
    assert dialog._code.isHidden()
    assert dialog._pairing_note.isHidden()
    assert dialog._new_code_button.isHidden()


def test_pairing_manager_exposes_confirmation() -> None:
    pairing = PairingManager("token-de-prueba")
    code = pairing.current_code()
    assert not pairing.is_confirmed()
    assert pairing.confirm("000000") is None
    assert not pairing.is_confirmed()
    assert pairing.confirm(code) == "token-de-prueba"
    assert pairing.is_confirmed()
    pairing.revoke()
    assert not pairing.is_confirmed()
