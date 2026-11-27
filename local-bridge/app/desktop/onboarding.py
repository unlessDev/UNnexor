"""Asistente de primer inicio de la aplicación de escritorio.

Tres pasos: bienvenida y privacidad, requisitos locales (Ollama y modelo
activo) y emparejamiento de la extensión con un código efímero. Se muestra
una sola vez en el primer arranque real y puede reabrirse desde el menú ⚙.

Las comprobaciones de Ollama se ejecutan en el pool de hilos de Qt para no
bloquear la interfaz. La comprobación de requisitos es informativa: el
usuario puede continuar y configurarlo más tarde.
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from ..ollama_client import OllamaClient, OllamaError
from .styles import (
    ACCENT,
    AMBER,
    BACKGROUND,
    BORDER,
    GREEN,
    MUTED,
    PANEL,
    RED,
    TEXT,
)

log = logging.getLogger("extdator.onboarding")

ONBOARDING_SEEN_KEY = "onboarding/seen"
STEP_TITLES = ("Bienvenida", "Requisitos", "Conectar extensión")
PAIRING_INTRO = (
    "En Chrome, Brave o Edge abre las opciones de UNnexor, pulsa "
    "«Conectar con la aplicación de escritorio» e introduce este código:"
)

DIALOG_QSS = f"""
QDialog {{
    background: {BACKGROUND};
    color: {TEXT};
}}
QLabel {{
    color: {TEXT};
    background: transparent;
}}
QLabel#onboardingTitle {{
    font-size: 19px;
    font-weight: 700;
    color: #e8eaf2;
}}
QLabel#onboardingSubtitle {{
    color: {MUTED};
    font-size: 11px;
}}
QLabel#onboardingCard {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 12px;
}}
QLabel#onboardingCode {{
    color: {ACCENT};
    font-size: 36px;
    font-weight: 700;
    letter-spacing: 8px;
}}
QPushButton {{
    background: #20242e;
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 7px 14px;
}}
QPushButton:hover {{
    border-color: {ACCENT};
}}
QPushButton:disabled {{
    color: {MUTED};
    border-color: {BORDER};
}}
QPushButton#primary {{
    background: {ACCENT};
    color: #0e1016;
    border-color: {ACCENT};
    font-weight: 700;
}}
QPushButton#primary:disabled {{
    background: #3a3550;
    color: #b9b4cc;
}}
QPushButton#quiet {{
    background: transparent;
    border: none;
    color: {MUTED};
    padding: 4px 6px;
}}
QPushButton#quiet:hover {{
    color: {TEXT};
}}
"""


def model_is_installed(installed: Iterable[str], wanted: str) -> bool:
    """Comprueba el modelo activo tolerando el sufijo implícito ``:latest``.

    Si el usuario configura ``llama3`` y Ollama lo lista como
    ``llama3:latest``, se considera instalado. Si el nombre incluye etiqueta,
    la coincidencia debe ser exacta para no confundir variantes.
    """

    target = (wanted or "").strip().lower()
    if not target:
        return False
    for name in installed:
        candidate = (name or "").strip().lower()
        if not candidate:
            continue
        if candidate == target:
            return True
        if ":" not in target and candidate.split(":", 1)[0] == target:
            return True
    return False


def should_show_onboarding(*, seen: bool, platform_name: str) -> bool:
    """Decide si toca abrir el asistente en este arranque.

    En plataformas ``offscreen`` (pruebas y smoke tests) nunca se abre: no
    hay usuario delante y el diálogo modal bloquearía el arranque.
    """

    return not seen and (platform_name or "").strip().lower() != "offscreen"


class _CheckSignals(QObject):
    """Señales del sondeo, separadas del ``QRunnable`` que se autodestruye."""

    finished = Signal(bool, str, bool, str)


class RequirementsCheckTask(QRunnable):
    """Comprueba Ollama y el modelo activo sin bloquear la interfaz."""

    def __init__(self, ollama: OllamaClient, model: str) -> None:
        super().__init__()
        self.signals = _CheckSignals()
        self._ollama = ollama
        self._model = model

    def run(self) -> None:
        reachable, detail = self._ollama.is_reachable()
        if not reachable:
            self.signals.finished.emit(False, detail, False, "")
            return
        try:
            installed = self._ollama.list_models()
        except OllamaError as exc:
            self.signals.finished.emit(
                True, detail, False, f"No se pudieron listar los modelos: {exc}"
            )
            return
        except Exception as exc:
            log.exception("Fallo inesperado al listar modelos: %s", exc)
            self.signals.finished.emit(
                True,
                detail,
                False,
                "No se pudieron listar los modelos. Revisa Ollama.",
            )
            return
        if not self._model.strip():
            self.signals.finished.emit(
                True, detail, False, "No hay un modelo configurado."
            )
            return
        found = model_is_installed(installed, self._model)
        if found:
            model_detail = f"«{self._model}» está instalado."
        else:
            model_detail = f"«{self._model}» no está instalado."
        self.signals.finished.emit(True, detail, found, model_detail)


class OnboardingDialog(QDialog):
    """Asistente modal de tres pasos para el primer arranque."""

    openModelsRequested = Signal()
    openSettingsRequested = Signal()

    def __init__(
        self,
        cfg: Config,
        ollama: OllamaClient,
        pairing,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._ollama = ollama
        self._pairing = pairing
        self._step = 0
        self._check_running = False
        self._task: Optional[RequirementsCheckTask] = None

        self.setWindowTitle("Asistente de inicio — UNnexor")
        self.setMinimumSize(580, 470)
        self.setStyleSheet(DIALOG_QSS)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        title = QLabel("Asistente de inicio")
        title.setObjectName("onboardingTitle")
        layout.addWidget(title)
        subtitle = QLabel(
            "Tres pasos para dejar UNnexor listo. Puedes volver a abrirlo "
            "desde el menú ⚙."
        )
        subtitle.setObjectName("onboardingSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        layout.addWidget(self._build_steps())

        self._stack = QStackedWidget()
        self._stack.addWidget(self._welcome_page())
        self._stack.addWidget(self._requirements_page())
        self._stack.addWidget(self._pairing_page())
        layout.addWidget(self._stack, 1)
        layout.addLayout(self._build_footer())

        self._pair_timer = QTimer(self)
        self._pair_timer.setInterval(1000)
        self._pair_timer.timeout.connect(self._tick_pairing)

        self._set_step(0)

    # ------------------------------------------------------------------ #
    # Construcción de la UI
    # ------------------------------------------------------------------ #
    def _build_steps(self) -> QWidget:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._pills = []
        for index, title in enumerate(STEP_TITLES, start=1):
            pill = QLabel(f"{index} · {title}")
            pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._pills.append(pill)
            row.addWidget(pill, 1)
        return container

    def _welcome_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(10)

        heading = QLabel("Tu asistente, en tu equipo")
        heading.setObjectName("onboardingTitle")
        layout.addWidget(heading)

        intro = QLabel(
            "UNnexor muestra las respuestas en un panel flotante mientras "
            "sigues trabajando en el navegador. Selecciona texto, pulsa la X de UNnexor y "
            "el panel aparecerá sin robarte el foco."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        card = QLabel(
            "Privacidad de serie\n"
            "· Por defecto, el texto se envía solo a Ollama en 127.0.0.1.\n"
            "· Si activas un proveedor remoto, el texto va a ese proveedor.\n"
            "· No hay telemetría ni cuentas de usuario.\n"
            "· Las claves de proveedores remotos se guardan en el "
            "Administrador de credenciales de Windows.\n"
            "· Tus conversaciones viven en la memoria del proceso y se "
            "borran al cerrar la aplicación."
        )
        card.setObjectName("onboardingCard")
        card.setWordWrap(True)
        card.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(card)

        note = QLabel(
            "En el siguiente paso comprobaremos que Ollama y el modelo activo "
            "están disponibles. Nada se descarga sin tu confirmación."
        )
        note.setObjectName("onboardingSubtitle")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    def _requirements_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(10)

        heading = QLabel("Comprueba los requisitos")
        heading.setObjectName("onboardingTitle")
        layout.addWidget(heading)

        intro = QLabel(
            "UNnexor usa Ollama para responder en local. Esta comprobación "
            "solo consulta tu equipo; no descarga ni cambia nada."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._ollama_status = QLabel()
        self._ollama_status.setTextFormat(Qt.TextFormat.PlainText)
        self._ollama_status.setWordWrap(True)
        self._model_status = QLabel()
        self._model_status.setTextFormat(Qt.TextFormat.PlainText)
        self._model_status.setWordWrap(True)
        self._set_status(self._ollama_status, None, "Ollama: sin comprobar.")
        self._set_status(self._model_status, None, "Modelo: sin comprobar.")
        layout.addWidget(self._ollama_status)
        layout.addWidget(self._model_status)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self._check_button = QPushButton("Volver a comprobar")
        self._check_button.clicked.connect(self._start_check)
        self._models_button = QPushButton("Elegir modelo…")
        self._models_button.clicked.connect(self._choose_model)
        self._settings_button = QPushButton("Abrir ajustes…")
        self._settings_button.clicked.connect(self._open_settings)
        buttons.addWidget(self._check_button)
        buttons.addWidget(self._models_button)
        buttons.addWidget(self._settings_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self._requirements_note = QLabel(
            "Si aún no tienes Ollama, instálalo desde ollama.com/download. "
            "También puedes usar un proveedor de API remoto desde el menú ⚙."
        )
        self._requirements_note.setObjectName("onboardingSubtitle")
        self._requirements_note.setWordWrap(True)
        layout.addWidget(self._requirements_note)
        layout.addStretch(1)
        return page

    def _pairing_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(10)

        heading = QLabel("Conecta la extensión")
        heading.setObjectName("onboardingTitle")
        layout.addWidget(heading)

        intro = QLabel(
            "En Chrome, Brave o Edge abre las opciones de UNnexor, pulsa "
            "«Conectar con la aplicación de escritorio» e introduce este "
            "código:"
        )
        intro.setWordWrap(True)
        self._pair_intro = intro
        layout.addWidget(intro)

        self._code = QLabel("")
        self._code.setObjectName("onboardingCode")
        self._code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._code.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self._code)

        self._timer_label = QLabel("")
        self._timer_label.setObjectName("onboardingSubtitle")
        self._timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._timer_label)

        self._pair_status = QLabel()
        self._pair_status.setTextFormat(Qt.TextFormat.PlainText)
        self._pair_status.setWordWrap(True)
        layout.addWidget(self._pair_status)

        self._new_code_button = QPushButton("Generar código nuevo")
        self._new_code_button.clicked.connect(self._new_code)
        row = QHBoxLayout()
        row.addWidget(self._new_code_button)
        row.addStretch(1)
        layout.addLayout(row)

        note = QLabel(
            "El código caduca a los 10 minutos y solo sirve una vez. No se "
            "comparte ningún dato de navegación."
        )
        note.setObjectName("onboardingSubtitle")
        note.setWordWrap(True)
        self._pairing_note = note
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    def _build_footer(self) -> QHBoxLayout:
        footer = QHBoxLayout()
        footer.setSpacing(8)
        self._skip = QPushButton("Omitir")
        self._skip.setObjectName("quiet")
        self._skip.clicked.connect(self.reject)
        self._back = QPushButton("Atrás")
        self._back.clicked.connect(self._go_back)
        self._next = QPushButton("Continuar")
        self._next.setObjectName("primary")
        self._next.clicked.connect(self._go_next)
        footer.addWidget(self._skip)
        footer.addStretch(1)
        footer.addWidget(self._back)
        footer.addWidget(self._next)
        return footer

    # ------------------------------------------------------------------ #
    # Navegación
    # ------------------------------------------------------------------ #
    def _set_step(self, step: int, *, check: bool = True) -> None:
        self._step = max(0, min(len(STEP_TITLES) - 1, step))
        self._stack.setCurrentIndex(self._step)
        self._back.setVisible(self._step > 0)
        self._next.setText(
            "Finalizar" if self._step == len(STEP_TITLES) - 1 else "Continuar"
        )
        self._update_pills()
        if self._step != 2:
            self._pair_timer.stop()
        if self._step == 1 and check:
            self._start_check()
        if self._step == 2:
            self._prepare_pairing()

    def _update_pills(self) -> None:
        for index, pill in enumerate(self._pills):
            if index == self._step:
                style = f"color: {TEXT}; border: 1px solid {ACCENT};"
            elif index < self._step:
                style = f"color: {GREEN}; border: 1px solid rgba(61,220,151,.45);"
            else:
                style = f"color: {MUTED}; border: 1px solid {BORDER};"
            pill.setStyleSheet(
                f"QLabel {{ {style} border-radius: 12px; padding: 4px 8px; "
                "font-size: 10px; background: transparent; }"
            )

    def _go_back(self) -> None:
        if self._step > 0:
            self._set_step(self._step - 1)

    def _go_next(self) -> None:
        if self._step < len(STEP_TITLES) - 1:
            self._set_step(self._step + 1)
        else:
            self.accept()

    # ------------------------------------------------------------------ #
    # Requisitos
    # ------------------------------------------------------------------ #
    def _start_check(self) -> None:
        if self._check_running:
            return
        self._check_running = True
        self._check_button.setEnabled(False)
        self._set_status(self._ollama_status, None, "Ollama: comprobando…")
        self._set_status(
            self._model_status,
            None,
            f"Modelo: comprobando «{self._cfg.ollama_model}»…",
        )
        task = RequirementsCheckTask(self._ollama, self._cfg.ollama_model)
        task.signals.finished.connect(self._on_check_finished)
        self._task = task
        QThreadPool.globalInstance().start(task)

    def _on_check_finished(
        self, reachable: bool, detail: str, model_ok: bool, model_detail: str
    ) -> None:
        self._check_running = False
        self._check_button.setEnabled(True)
        self._task = None
        self._apply_check(reachable, detail, model_ok, model_detail)

    def _apply_check(
        self, reachable: bool, detail: str, model_ok: bool, model_detail: str
    ) -> None:
        if reachable:
            self._set_status(self._ollama_status, "ok", f"Ollama: {detail}.")
            if model_ok:
                self._set_status(self._model_status, "ok", f"Modelo: {model_detail}")
                self._requirements_note.setText(
                    "Todo listo. Puedes continuar para conectar la extensión."
                )
            else:
                self._set_status(
                    self._model_status,
                    "warn",
                    f"Modelo: {model_detail or 'no disponible.'}",
                )
                self._requirements_note.setText(
                    "Elige otro modelo instalado o descarga uno desde el "
                    "selector 🤖. También puedes continuar y cambiarlo después."
                )
        else:
            self._set_status(self._ollama_status, "error", f"Ollama: {detail}")
            self._set_status(
                self._model_status,
                None,
                "Modelo: se comprobará cuando Ollama responda.",
            )
            self._requirements_note.setText(
                "Arranca Ollama o instálalo desde ollama.com/download y pulsa "
                "«Volver a comprobar». También puedes usar un proveedor de API "
                "remoto desde el menú ⚙."
            )

    def _choose_model(self) -> None:
        self.openModelsRequested.emit()
        self._start_check()

    def _open_settings(self) -> None:
        self.openSettingsRequested.emit()
        self._start_check()

    @staticmethod
    def _set_status(label: QLabel, state: Optional[str], text: str) -> None:
        prefix = {"ok": "✓", "warn": "!", "error": "✕"}.get(state or "", "○")
        colors = {"ok": GREEN, "warn": AMBER, "error": RED}
        label.setText(f"{prefix}  {text}")
        label.setStyleSheet(f"color: {colors.get(state or '', MUTED)};")

    # ------------------------------------------------------------------ #
    # Emparejamiento
    # ------------------------------------------------------------------ #
    def _prepare_pairing(self) -> None:
        self._pair_timer.stop()
        self._new_code_button.setVisible(True)
        if self._pairing is None or not self._cfg.token:
            self._code.setVisible(False)
            self._timer_label.setVisible(False)
            self._pairing_note.setVisible(False)
            self._new_code_button.setVisible(False)
            self._pair_intro.setText(
                "Esta instalación no tiene token local, así que la extensión "
                "no puede vincularse. Revisa la configuración del puente."
            )
            self._set_status(
                self._pair_status,
                "warn",
                "Emparejamiento no disponible: no hay token local configurado.",
            )
            return
        if self._pairing.is_confirmed():
            self._show_paired()
            return
        self._refresh_code()
        self._pair_timer.start()

    def _refresh_code(self) -> None:
        self._pair_intro.setText(PAIRING_INTRO)
        self._code.setText(self._pairing.current_code())
        self._code.setVisible(True)
        self._timer_label.setVisible(True)
        self._pairing_note.setVisible(True)
        self._set_status(
            self._pair_status, None, "Esperando a que conectes la extensión…"
        )
        self._tick_pairing()

    def _new_code(self) -> None:
        if self._pairing is None or not self._cfg.token:
            return
        self._pairing.revoke()
        self._refresh_code()
        self._pair_timer.start()

    def _tick_pairing(self) -> None:
        if self._pairing is None or not self._cfg.token:
            return
        if self._pairing.is_confirmed():
            self._show_paired()
            return
        left = self._pairing.seconds_left()
        if left <= 0:
            # El código ha caducado: se rota sin intervención del usuario.
            self._pairing.revoke()
            self._refresh_code()
            return
        self._timer_label.setText(
            f"Válido durante {left // 60}:{left % 60:02d} · un solo uso"
        )

    def _show_paired(self) -> None:
        self._pair_timer.stop()
        self._pair_intro.setText(
            "La extensión ya está vinculada a este equipo. Puedes generar un "
            "código nuevo si necesitas volver a conectarla."
        )
        self._code.setVisible(False)
        self._timer_label.setVisible(False)
        self._pairing_note.setVisible(False)
        self._new_code_button.setVisible(True)
        self._set_status(
            self._pair_status,
            "ok",
            "Extensión vinculada. Ya puedes seleccionar texto y pulsar la X de UNnexor.",
        )
        self._next.setText("Finalizar")

    # ------------------------------------------------------------------ #
    def done(self, result: int) -> None:
        self._pair_timer.stop()
        super().done(result)
