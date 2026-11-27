"""Ventana del copiloto en tiempo real.

Ventana normal (con marco) con navegación lateral. El panel flotante
acompaña a las sesiones y los atajos globales permiten pausar, preguntar
o capturar sin cambiar de ventana. Cerrar la ventana no detiene una sesión
activa: se sigue viendo en el panel flotante, desde donde se finaliza.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QToolButton,
    QWidget,
)

from ...config import Config
from ...copilot.assistant import QAItem
from ...copilot.challenges import ChallengeResult
from ...copilot.transcription import TranscriptEvent
from ...copilot.translation import TranslationSegment
from ..topmost import PinPreference, set_topmost
from .common import COPILOT_QSS
from .controller import CopilotController
from .hotkeys import HotkeyManager, find_conflicts
from .overlay import CopilotOverlay
from .pages_challenge import ChallengesPage
from .pages_config import EnginesPage, HomePage, PrivacyPage, ProfilesPage
from .pages_session import ConversationPage, TranslationPage

log = logging.getLogger("extdator.copilot.ui")

PAGES = [
    ("home", "🏠  Inicio"),
    ("conversation", "🎧  Conversación"),
    ("translation", "🌐  Traducción"),
    ("challenges", "🧩  Desafíos"),
    ("profiles", "👤  Perfiles y documentos"),
    ("engines", "⚙  Motores y modelos"),
    ("privacy", "🔒  Privacidad"),
]


class CopilotWindow(QMainWindow):
    def __init__(
        self,
        cfg: Config,
        controller: CopilotController | None = None,
        *,
        register_hotkeys: bool = True,
    ) -> None:
        super().__init__()
        self.setWindowTitle("UNnexor · Copiloto en tiempo real")
        self.resize(1080, 760)
        self.setStyleSheet(COPILOT_QSS)
        self.controller = controller or CopilotController(cfg)
        c = self.controller
        self.overlay = CopilotOverlay(
            c.settings.overlay_on_top, c.settings.overlay_opacity
        )
        self.hotkeys = HotkeyManager()
        self._hidden_for_capture: list[QWidget] = []

        root = QWidget()
        root.setObjectName("copilotRoot")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(220)
        for _key, label in PAGES:
            self.nav.addItem(label)
        layout.addWidget(self.nav)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.home = HomePage(c)
        self.conversation = ConversationPage(c)
        self.translation = TranslationPage(c)
        self.challenges = ChallengesPage(c)
        self.profiles = ProfilesPage(c)
        self.engines = EnginesPage(c)
        self.privacy = PrivacyPage(c)
        self._pages = {
            "home": self.home,
            "conversation": self.conversation,
            "translation": self.translation,
            "challenges": self.challenges,
            "profiles": self.profiles,
            "engines": self.engines,
            "privacy": self.privacy,
        }
        for key, _label in PAGES:
            self.stack.addWidget(self._pages[key])
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.currentRowChanged.connect(self._on_page_changed)
        self.nav.setCurrentRow(0)

        self.home.openMode.connect(self.open_page)
        self.profiles.profilesChanged.connect(self.conversation.reload)
        self.conversation.hotkeysChanged.connect(self._reregister_hotkeys)
        self.privacy.dataCleared.connect(self._after_clear)
        self.challenges.hideWindowsRequested.connect(self._hide_for_capture)
        self.challenges.restoreWindowsRequested.connect(self._restore_after_capture)

        # Panel flotante
        self.overlay.pauseClicked.connect(c.toggle_pause)
        self.overlay.stopClicked.connect(self._stop_from_overlay)
        self.overlay.alternativeClicked.connect(c.assistant.regenerate)
        self.overlay.expandClicked.connect(c.assistant.expand)
        self.overlay.openMainClicked.connect(self.bring_to_front)
        self.overlay.pinChanged.connect(self._save_overlay_pin)
        self.overlay.listenClicked.connect(c.toggle_listening)
        c.listeningChanged.connect(self.overlay.set_listening)
        c.sessionChanged.connect(self._on_session)
        c.pausedChanged.connect(self.overlay.set_paused)
        c.status.connect(self._on_status)
        c.transcript.connect(self._on_transcript)
        c.question.connect(lambda item: None)
        c.answerStarted.connect(self._on_answer_started)
        c.answerChunk.connect(self.overlay.append_answer)
        c.answerDone.connect(self.overlay.finish_answer)
        c.translationUpdated.connect(self._on_translation)
        c.challengeStarted.connect(self._on_challenge_started)
        c.challengeDone.connect(self._on_challenge_done)
        c.challengeChunk.connect(self.overlay.append_text)
        c.challengeFailed.connect(self._on_challenge_failed)
        c.notice.connect(lambda m: self.overlay.set_status(m, "info"))
        c.error.connect(self._on_error)
        self.hotkey_problems: list[str] = []
        if register_hotkeys:
            QTimer.singleShot(0, self.register_hotkeys)

        # «Siempre visible» propio de esta ventana (independiente del panel
        # principal y del flotante), persistente entre sesiones.
        self._pin_pref = PinPreference("copilotWindow", False)
        self._on_top = self._pin_pref.load()
        self.pin_button = QToolButton()
        self.pin_button.setText("📌 Siempre visible")
        self.pin_button.setCheckable(True)
        self.pin_button.setToolTip("Mantener esta ventana encima de las demás")
        self.pin_button.toggled.connect(self.set_on_top)
        self.statusBar().addPermanentWidget(self.pin_button)
        self.statusBar().setSizeGripEnabled(True)

    # ---------------------------------------------------------- navegación
    def open_page(self, key: str) -> None:
        keys = [k for k, _ in PAGES]
        if key in keys:
            self.nav.setCurrentRow(keys.index(key))
        self.bring_to_front()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_pin(self._on_top)

    def _apply_pin(self, wanted: bool) -> None:
        self._on_top = wanted  # antes de aplicar (showEvent puede reentrar)
        actual = set_topmost(self, wanted)
        self._on_top = actual
        self.pin_button.blockSignals(True)
        self.pin_button.setChecked(actual)
        self.pin_button.blockSignals(False)

    def set_on_top(self, enabled: bool) -> None:
        self._apply_pin(bool(enabled))
        self._pin_pref.save(self._on_top)

    def _save_overlay_pin(self, on_top: bool) -> None:
        """El 📌 del panel flotante persiste en los ajustes del copiloto."""
        settings = self.controller.settings
        if settings.overlay_on_top != on_top:
            settings.overlay_on_top = on_top
            self.controller.save_settings(settings)

    def bring_to_front(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_page_changed(self, row: int) -> None:
        key = PAGES[row][0]
        if key in ("conversation", "translation"):
            page = self._pages[key]
            page.controls.refresh_devices()  # type: ignore[attr-defined]
        if key == "engines":
            self.engines.reload()

    # ------------------------------------------------------------- atajos
    def register_hotkeys(self) -> None:
        s = self.controller.settings
        self.hotkeys.unregister_all()
        bindings = (
            (s.hotkey_toggle_capture, self.controller.toggle_pause, "pausar/reanudar"),
            (s.hotkey_ask_manual, self._hotkey_ask, "preguntar"),
            (
                s.hotkey_screen_challenge,
                self.challenges.capture_and_solve,
                "desafío en pantalla",
            ),
            (s.hotkey_listen, self.controller.toggle_listening, "escuchar"),
            (
                s.hotkey_listen_cancel,
                self.controller.cancel_listening,
                "cancelar escucha",
            ),
        )
        problems = find_conflicts({label: combo for combo, _a, label in bindings})
        duplicated = {p.split("»")[0].lstrip("«") for p in problems}
        for combo, action, label in bindings:
            if label in duplicated:
                continue  # no registrar una combinación en conflicto
            problem = self.hotkeys.register(combo, action)
            if problem:
                problems.append(f"Atajo '{label}': {problem}")
        self.hotkey_problems = problems
        if problems:
            log.warning("Atajos no registrados: %s", "; ".join(problems))

    def _reregister_hotkeys(self) -> None:
        self.register_hotkeys()
        if self.hotkey_problems:
            QMessageBox.warning(self, "Atajos", "\n".join(self.hotkey_problems))

    def _hotkey_ask(self) -> None:
        self.open_page("conversation")
        self.conversation.focus_question()

    # -------------------------------------------------------------- sesión
    def _on_session(self, mode: str) -> None:
        self.overlay.set_listen_available(self.controller.uses_listen_windows)
        if mode == "idle":
            self.overlay.set_status("Finalizado", "paused")
            return
        self.overlay.set_status(
            "Capturando audio" if mode == "conversation" else "Traduciendo en vivo",
            "capture",
        )
        self.overlay.show()

    def _stop_from_overlay(self) -> None:
        self.controller.stop_session()
        self.challenges.stop_periodic()

    def _on_status(self, component: str, text: str) -> None:
        kind = "busy" if component == "llm" and text.endswith("…") else "capture"
        if "Error" in text or "error" in text:
            kind = "error"
        if self.controller.paused:
            kind = "paused"
        self.overlay.set_status(text, kind)

    def _on_transcript(self, event: TranscriptEvent) -> None:
        if self.controller.mode == "conversation":
            self.overlay.show_transcript(event)

    def _on_answer_started(self, item: QAItem) -> None:
        self.overlay.begin_answer(item)

    def _on_translation(self, segment: TranslationSegment) -> None:
        if self.controller.mode == "translation":
            self.overlay.show_translation(segment)

    def _on_challenge_started(self, instruction: str) -> None:
        self.overlay.begin_text(f"🧩 {instruction or 'Desafío en pantalla'}")
        self.overlay.set_status("Procesando imagen…", "busy")
        self.overlay.show()

    def _on_challenge_done(self, result: ChallengeResult) -> None:
        self.overlay.finish_text(result.text)
        self.overlay.set_status(f"Listo · {result.model}", "capture")

    def _on_challenge_failed(self, message: str) -> None:
        self.overlay.finish_text(f"⚠ {message}", error=True)
        self.overlay.set_status(message, "error")

    def _on_error(self, message: str) -> None:
        self.overlay.set_status(message, "error")
        if self.isVisible():
            QMessageBox.warning(self, "Copiloto", message)

    def _after_clear(self) -> None:
        self.conversation.clear()
        self.translation.clear()
        self.profiles.reload()
        self.conversation.reload()
        self.engines.reload()

    # ----------------------------------------------------- captura de pantalla
    def _hide_for_capture(self) -> None:
        self._hidden_for_capture = [w for w in (self, self.overlay) if w.isVisible()]
        for widget in self._hidden_for_capture:
            widget.hide()

    def _restore_after_capture(self) -> None:
        for widget in self._hidden_for_capture:
            widget.show()
        self._hidden_for_capture = []

    # -------------------------------------------------------------- cierre
    def closeEvent(self, event) -> None:
        if self.controller.mode != "idle":
            # La sesión continúa visible en el panel flotante.
            self.overlay.show()
        event.accept()

    def shutdown(self) -> None:
        from PySide6.QtCore import QThreadPool

        self.challenges.stop_periodic()
        self.hotkeys.unregister_all()
        self.controller.shutdown()
        # Deja terminar las tareas en segundo plano antes de destruir vistas.
        QThreadPool.globalInstance().waitForDone(5000)
        self.overlay.close()
        self.close()
