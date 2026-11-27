"""Secciones de sesión en vivo: conversación y traducción."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ...audio.capture import SoundCardBackend
from ...audio.types import AudioDevice, SourceKind
from ...copilot.assistant import QAItem, QAStatus
from ...copilot.listening import (
    MAX_SECONDS,
    MIN_SECONDS,
    PRESET_SECONDS,
    format_seconds,
)
from ...copilot.transcription import TranscriptEvent
from ...copilot.translation import SegmentStatus, TranslationSegment
from ..styles import AMBER, MUTED, RED
from ..widgets import AssistantMessage
from .common import escape, export_pdf, hint, language_combo, select_data, title
from .controller import CopilotController
from .tasks import run_async


class SessionControls(QGroupBox):
    """Fuentes de audio, dispositivos, inicio/pausa/fin, niveles y estado."""

    startClicked = Signal()

    def __init__(self, controller: CopilotController, mode: str) -> None:
        super().__init__("Captura de audio")
        self._c = controller
        self._mode = mode
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.system = QCheckBox("Audio del sistema (lo que suena en el equipo)")
        self.mic = QCheckBox("Micrófono (tu voz)")
        self.system_device = QComboBox()
        self.mic_device = QComboBox()
        for combo in (self.system_device, self.mic_device):
            combo.addItem("Predeterminado", "")
            combo.setMinimumWidth(180)
        row.addWidget(self.system)
        row.addWidget(self.system_device)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(self.mic)
        row.addWidget(self.mic_device)
        layout.addLayout(row)
        meters = QHBoxLayout()
        self.level_system = QProgressBar()
        self.level_mic = QProgressBar()
        for bar, label in ((self.level_system, "Sistema"), (self.level_mic, "Mic")):
            bar.setRange(0, 60)
            bar.setTextVisible(False)
            meters.addWidget(QLabel(label))
            meters.addWidget(bar, 1)
        layout.addLayout(meters)
        buttons = QHBoxLayout()
        self.start = QPushButton("▶ Iniciar")
        self.start.setObjectName("primary")
        self.pause = QPushButton("⏸ Pausar")
        self.stop = QPushButton("■ Finalizar")
        for button in (self.start, self.pause, self.stop):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.status = QLabel("Preparado")
        self.status.setObjectName("statusLine")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addWidget(
            hint(
                "Mientras la captura está activa verás el indicador en el panel "
                "flotante. Obtén el consentimiento de los participantes si la "
                "ley o la plataforma lo exigen."
            )
        )
        self.start.clicked.connect(self.startClicked)
        self.pause.clicked.connect(controller.toggle_pause)
        self.stop.clicked.connect(controller.stop_session)
        controller.sessionChanged.connect(self._on_session)
        controller.pausedChanged.connect(self._on_paused)
        controller.level.connect(self._on_level)
        controller.status.connect(self._on_status)
        self.load_settings()
        self._on_session(controller.mode)

    def load_settings(self) -> None:
        s = self._c.settings
        self.system.setChecked(s.capture_system)
        self.mic.setChecked(s.capture_microphone)

    def apply_to_settings(self) -> None:
        s = self._c.settings
        s.capture_system = self.system.isChecked()
        s.capture_microphone = self.mic.isChecked()
        s.system_device = str(self.system_device.currentData() or "")
        s.microphone_device = str(self.mic_device.currentData() or "")

    def refresh_devices(self) -> None:
        def load() -> list[AudioDevice]:
            return SoundCardBackend().list_devices()

        def fill(devices: object) -> None:
            assert isinstance(devices, list)
            for combo, kind, current in (
                (self.system_device, SourceKind.SYSTEM, self._c.settings.system_device),
                (
                    self.mic_device,
                    SourceKind.MICROPHONE,
                    self._c.settings.microphone_device,
                ),
            ):
                combo.clear()
                combo.addItem("Predeterminado", "")
                for device in devices:
                    if device.kind is kind:
                        mark = " (predeterminado)" if device.is_default else ""
                        combo.addItem(device.name + mark, device.id)
                select_data(combo, current)

        run_async(
            load,
            on_done=fill,
            on_error=lambda m: self.status.setText(
                f"No se pudieron listar los dispositivos: {m}"
            ),
        )

    def _on_session(self, mode: str) -> None:
        active = mode == self._mode
        idle = mode == "idle"
        self.start.setEnabled(idle)
        self.pause.setEnabled(active)
        self.stop.setEnabled(active)
        for widget in (self.system, self.mic, self.system_device, self.mic_device):
            widget.setEnabled(idle)
        if idle:
            self.level_system.setValue(0)
            self.level_mic.setValue(0)

    def _on_paused(self, paused: bool) -> None:
        self.pause.setText("▶ Reanudar" if paused else "⏸ Pausar")
        if paused:
            self.status.setText("Pausado")

    def _on_level(self, source: str, db: float, _prob: float) -> None:
        bar = self.level_system if source == SourceKind.SYSTEM.value else self.level_mic
        bar.setValue(int(max(0, min(60, db + 60))))

    def _on_status(self, component: str, text: str) -> None:
        if self._c.mode == self._mode or component == "llm":
            self.status.setText(text)


class ConversationPage(QWidget):
    hotkeysChanged = Signal()  # el usuario cambió los atajos de escucha

    def __init__(self, controller: CopilotController) -> None:
        super().__init__()
        self._c = controller
        self._items: dict[int, QAItem] = {}
        self._list_items: dict[int, QListWidgetItem] = {}
        layout = QVBoxLayout(self)
        layout.addWidget(title("Asistencia en conversaciones"))
        layout.addWidget(
            hint(
                "Escucha la conversación, detecta preguntas del interlocutor y "
                "sugiere respuestas con tu perfil y tus documentos. Las "
                "sugerencias no inventan experiencias: lo que falte aparece como "
                "[completar: …]."
            )
        )
        self.controls = SessionControls(controller, "conversation")
        self.controls.startClicked.connect(self.start)
        layout.addWidget(self.controls)

        options = QHBoxLayout()
        self.auto = QCheckBox("Responder automáticamente")
        self.sensitivity = QComboBox()
        for key, label in (
            ("low", "Sensibilidad baja"),
            ("medium", "Sensibilidad media"),
            ("high", "Sensibilidad alta"),
        ):
            self.sensitivity.addItem(label, key)
        self.profile = QComboBox()
        options.addWidget(QLabel("Perfil:"))
        options.addWidget(self.profile, 1)
        options.addWidget(self.auto)
        options.addWidget(self.sensitivity)
        layout.addLayout(options)

        # --- Control del tiempo de escucha (ventanas de escucha) ---------
        listen = QHBoxLayout()
        self.listen_mode = QComboBox()
        for key, label in (
            ("continuous", "Escucha continua (detección automática)"),
            ("timed", "Escucha temporizada"),
            ("manual", "Escucha manual con atajo"),
        ):
            self.listen_mode.addItem(label, key)
        self.listen_mode.setToolTip(
            "Continua: detecta preguntas turno a turno.\n"
            "Temporizada: al pulsar «Escuchar» captura N segundos y responde a "
            "todo lo escuchado.\n"
            "Manual: el atajo inicia la escucha y el mismo atajo la termina."
        )
        self.listen_seconds = QSpinBox()
        self.listen_seconds.setRange(MIN_SECONDS, MAX_SECONDS)
        self.listen_seconds.setSuffix(" s")
        self.listen_seconds.setToolTip("Duración de la escucha temporizada")
        self.listen_presets = QComboBox()
        self.listen_presets.addItem("Duración…", 0)
        for seconds in PRESET_SECONDS:
            self.listen_presets.addItem(format_seconds(seconds), seconds)
        self.listen_presets.activated.connect(self._apply_preset)
        self.listen_hotkey = QLineEdit()
        self.listen_hotkey.setPlaceholderText("Ctrl+Alt+Shift+L")
        self.listen_hotkey.setToolTip(
            "Atajo global para iniciar/terminar la escucha (p. ej. Ctrl+Alt+Shift+L)"
        )
        self.listen_hotkey.setMaximumWidth(150)
        self.listen_cancel_hotkey = QLineEdit()
        self.listen_cancel_hotkey.setPlaceholderText("Ctrl+Alt+Shift+X")
        self.listen_cancel_hotkey.setToolTip("Atajo global para descartar la escucha")
        self.listen_cancel_hotkey.setMaximumWidth(150)
        apply_keys = QPushButton("Aplicar atajos")
        apply_keys.clicked.connect(self._apply_listen_hotkeys)
        listen.addWidget(QLabel("Escucha:"))
        listen.addWidget(self.listen_mode, 1)
        listen.addWidget(self.listen_seconds)
        listen.addWidget(self.listen_presets)
        listen.addWidget(QLabel("Atajo:"))
        listen.addWidget(self.listen_hotkey)
        listen.addWidget(QLabel("Cancelar:"))
        listen.addWidget(self.listen_cancel_hotkey)
        listen.addWidget(apply_keys)
        layout.addLayout(listen)

        window_row = QHBoxLayout()
        self.listen_button = QPushButton("🎙 Escuchar")
        self.listen_button.setObjectName("primary")
        self.listen_end = QPushButton("■ Terminar y responder")
        self.listen_cancel = QPushButton("✕ Cancelar escucha")
        self.listen_state = QLabel("")
        self.listen_state.setWordWrap(True)
        window_row.addWidget(self.listen_button)
        window_row.addWidget(self.listen_end)
        window_row.addWidget(self.listen_cancel)
        window_row.addWidget(self.listen_state, 1)
        self.listen_panel = QWidget()
        self.listen_panel.setLayout(window_row)
        layout.addWidget(self.listen_panel)
        self.listen_button.clicked.connect(self._begin_listening)
        self.listen_end.clicked.connect(controller.end_listening)
        self.listen_cancel.clicked.connect(controller.cancel_listening)
        controller.listeningChanged.connect(self._on_listening)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Transcripción"))
        self.transcript = QTextBrowser()
        left_layout.addWidget(self.transcript, 1)
        self.partial = QLabel("")
        self.partial.setStyleSheet(f"color: {MUTED}; font-style: italic;")
        self.partial.setWordWrap(True)
        left_layout.addWidget(self.partial)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("Preguntas y sugerencias"))
        self.questions = QListWidget()
        self.questions.currentItemChanged.connect(self._show_selected)
        right_layout.addWidget(self.questions, 1)
        self.answer_box = QVBoxLayout()
        self.answer_box.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.answer_holder = QWidget()
        self.answer_holder.setLayout(self.answer_box)
        right_layout.addWidget(self.answer_holder, 2)
        actions = QHBoxLayout()
        self.alt = QPushButton("↻ Otra respuesta")
        self.more = QPushButton("＋ Ampliar")
        self.useful = QPushButton("👍 Útil")
        self.not_useful = QPushButton("👎")
        self.copy = QPushButton("⧉ Copiar")
        for button in (self.alt, self.more, self.useful, self.not_useful, self.copy):
            actions.addWidget(button)
        right_layout.addLayout(actions)
        ask_row = QHBoxLayout()
        self.ask_input = QLineEdit()
        self.ask_input.setPlaceholderText("Escribe una pregunta y pulsa Enter…")
        ask = QPushButton("Preguntar")
        ask_row.addWidget(self.ask_input, 1)
        ask_row.addWidget(ask)
        right_layout.addLayout(ask_row)
        splitter.addWidget(right)
        splitter.setSizes([420, 520])
        layout.addWidget(splitter, 1)

        ask.clicked.connect(self.ask)
        self.ask_input.returnPressed.connect(self.ask)
        self.alt.clicked.connect(lambda: self._followup("alt"))
        self.more.clicked.connect(lambda: self._followup("more"))
        self.useful.clicked.connect(lambda: self._feedback(True))
        self.not_useful.clicked.connect(lambda: self._feedback(False))
        self.copy.clicked.connect(self._copy)
        self.auto.toggled.connect(self._save_options)
        self.sensitivity.currentIndexChanged.connect(self._save_options)
        self.profile.currentIndexChanged.connect(self._save_options)
        self.listen_mode.currentIndexChanged.connect(self._save_options)
        self.listen_seconds.valueChanged.connect(self._save_options)
        controller.transcript.connect(self._on_transcript)
        controller.question.connect(self._on_item)
        controller.answerStarted.connect(self._on_item)
        controller.answerChunk.connect(self._on_chunk)
        controller.answerDone.connect(self._on_item)
        self._answer_widget: AssistantMessage | None = None
        self._answer_item_id: int | None = None
        self.reload()

    # ------------------------------------------------------------------ #
    def reload(self) -> None:
        s = self._c.settings
        self.auto.blockSignals(True)
        self.sensitivity.blockSignals(True)
        self.profile.blockSignals(True)
        self.listen_mode.blockSignals(True)
        self.listen_seconds.blockSignals(True)
        select_data(self.listen_mode, s.listen_mode)
        self.listen_seconds.setValue(int(s.listen_seconds))
        self.listen_hotkey.setText(s.hotkey_listen)
        self.listen_cancel_hotkey.setText(s.hotkey_listen_cancel)
        self.listen_mode.blockSignals(False)
        self.listen_seconds.blockSignals(False)
        self._update_listen_widgets()
        self.auto.setChecked(s.auto_answer)
        select_data(self.sensitivity, s.sensitivity)
        self.profile.clear()
        self.profile.addItem("Sin perfil", "")
        for profile in self._c.profiles.list():
            self.profile.addItem(profile.name, profile.id)
        select_data(self.profile, s.active_profile)
        self.auto.blockSignals(False)
        self.sensitivity.blockSignals(False)
        self.profile.blockSignals(False)
        self.controls.load_settings()

    def _save_options(self) -> None:
        s = self._c.settings
        s.auto_answer = self.auto.isChecked()
        s.sensitivity = str(self.sensitivity.currentData())
        s.active_profile = str(self.profile.currentData() or "")
        new_mode = str(self.listen_mode.currentData() or "continuous")
        mode_changed = new_mode != s.listen_mode
        s.listen_mode = new_mode
        s.listen_seconds = int(self.listen_seconds.value())
        self._c.save_settings(s)
        self._update_listen_widgets()
        if mode_changed and self._c.mode == "conversation":
            QMessageBox.information(
                self,
                "Modo de escucha",
                "El nuevo modo se aplica al iniciar la próxima sesión.",
            )

    def _apply_preset(self, index: int) -> None:
        seconds = int(self.listen_presets.itemData(index) or 0)
        if seconds:
            self.listen_seconds.setValue(seconds)
        self.listen_presets.setCurrentIndex(0)

    def _apply_listen_hotkeys(self) -> None:
        from .hotkeys import HotkeyError, find_conflicts, parse_hotkey

        s = self._c.settings
        listen = self.listen_hotkey.text().strip()
        cancel = self.listen_cancel_hotkey.text().strip()
        try:
            for combo in (listen, cancel):
                if combo:
                    parse_hotkey(combo)
        except HotkeyError as exc:
            QMessageBox.warning(self, "Atajo no válido", str(exc))
            return
        problems = find_conflicts(
            {
                "pausar/reanudar": s.hotkey_toggle_capture,
                "preguntar": s.hotkey_ask_manual,
                "desafío en pantalla": s.hotkey_screen_challenge,
                "escuchar": listen,
                "cancelar escucha": cancel,
            }
        )
        if problems:
            QMessageBox.warning(self, "Conflicto de atajos", "\n".join(problems))
            return
        s.hotkey_listen = listen
        s.hotkey_listen_cancel = cancel
        self._c.save_settings(s)
        self.hotkeysChanged.emit()

    def _update_listen_widgets(self) -> None:
        mode = str(self.listen_mode.currentData() or "continuous")
        windowed = mode != "continuous"
        self.listen_seconds.setEnabled(mode == "timed")
        self.listen_presets.setEnabled(mode == "timed")
        self.listen_panel.setVisible(windowed)
        self.auto.setEnabled(not windowed)
        self.sensitivity.setEnabled(not windowed)
        if windowed and not self.listen_state.text():
            self.listen_state.setText(
                f"Pulsa «Escuchar» o {self._c.settings.hotkey_listen} cuando "
                "quieras que el copiloto escuche."
            )
        self._refresh_listen_buttons(self._c.window.state.value)

    def _begin_listening(self) -> None:
        self.controls.apply_to_settings()
        self._save_options()
        try:
            self._c.begin_listening()
        except ValueError as exc:
            QMessageBox.warning(self, "No se puede escuchar", str(exc))

    def _refresh_listen_buttons(self, state: str) -> None:
        self.listen_button.setEnabled(state == "idle")
        self.listen_end.setEnabled(state == "listening")
        self.listen_cancel.setEnabled(state in ("listening", "processing"))

    def _on_listening(self, state: str, text: str) -> None:
        self._refresh_listen_buttons(state)
        if text:
            self.listen_state.setText(text)
        color = {"listening": RED, "processing": AMBER}.get(state, MUTED)
        self.listen_state.setStyleSheet(f"color: {color};")

    def start(self) -> None:
        self.controls.apply_to_settings()
        self._save_options()
        try:
            self._c.start_session("conversation")
        except ValueError as exc:
            QMessageBox.warning(self, "No se puede iniciar", str(exc))

    def ask(self) -> None:
        text = self.ask_input.text().strip()
        if not text:
            return
        try:
            self._c.assistant.ask(text)
            self.ask_input.clear()
        except ValueError as exc:
            QMessageBox.information(self, "Pregunta", str(exc))

    def focus_question(self) -> None:
        self.ask_input.setFocus()

    # -------------------------------------------------------- transcripción
    def _on_transcript(self, event: TranscriptEvent) -> None:
        if self._c.mode != "conversation":
            return
        if not event.final:
            self.partial.setText(f"{event.source.speaker_label}: {event.text} …")
            return
        self.partial.setText("")
        if not event.text:
            return
        flag = " ⚠️" if event.low_confidence else ""
        self.transcript.append(
            f"<b>{escape(event.source.speaker_label)}:</b> {escape(event.text)}{flag}"
        )
        self.transcript.moveCursor(QTextCursor.MoveOperation.End)

    # ------------------------------------------------------------- Q&A
    def _on_item(self, item: QAItem) -> None:
        self._items[item.id] = item
        icon = {
            QAStatus.PENDING: "⏳",
            QAStatus.GENERATING: "✍",
            QAStatus.DONE: "✅",
            QAStatus.FAILED: "⚠",
            QAStatus.CANCELLED: "✖",
        }[item.status]
        text = f"{icon} {item.question}"
        list_item = self._list_items.get(item.id)
        if list_item is None:
            # Solo se salta a la pregunta nueva si el usuario ya estaba en la
            # última: si está leyendo una anterior, no se le interrumpe.
            count = self.questions.count()
            current = self.questions.currentRow()
            follow = count == 0 or current in (-1, count - 1)
            list_item = QListWidgetItem(text)
            list_item.setData(Qt.ItemDataRole.UserRole, item.id)
            self.questions.addItem(list_item)
            self._list_items[item.id] = list_item
            if follow:
                self.questions.setCurrentItem(list_item)
        else:
            list_item.setText(text)
        if self._selected_id() == item.id:
            self._render(item)

    def _on_chunk(self, item_id: int, delta: str) -> None:
        if self._selected_id() == item_id and self._answer_widget is not None:
            self._answer_widget.append_streaming(delta)

    def _selected_id(self) -> int | None:
        current = self.questions.currentItem()
        return int(current.data(Qt.ItemDataRole.UserRole)) if current else None

    def _show_selected(self, *_args: object) -> None:
        item_id = self._selected_id()
        if item_id is not None and item_id in self._items:
            self._render(self._items[item_id])

    def _render(self, item: QAItem) -> None:
        # Si la respuesta que terminó es la que se está mostrando en
        # streaming, se finaliza en su sitio (sin reconstruir el widget).
        streaming = self._answer_widget
        if (
            streaming is not None
            and item.status is QAStatus.DONE
            and self._answer_item_id == item.id
            and streaming.text().strip() == item.answer.strip()
        ):
            streaming.finalize()
            self._answer_widget = None
            for button in (
                self.alt,
                self.more,
                self.useful,
                self.not_useful,
                self.copy,
            ):
                button.setEnabled(True)
            if item.sources:
                self.answer_box.addWidget(hint("Fuentes: " + " · ".join(item.sources)))
            return
        while self.answer_box.count():
            child = self.answer_box.takeAt(0)
            widget = child.widget() if child is not None else None
            if widget is not None:
                widget.deleteLater()
        widget = AssistantMessage(item.model or "sugerencia", role="COPILOTO")
        if item.answer:
            widget.append_streaming(item.answer)
        if item.status is QAStatus.DONE:
            widget.finalize()
        elif item.status is QAStatus.FAILED:
            widget.append_streaming(f"⚠ {item.error}")
            widget.mark_error()
        self.answer_box.addWidget(widget)
        if item.sources:
            self.answer_box.addWidget(hint("Fuentes: " + " · ".join(item.sources)))
        self._answer_widget = widget if item.status is QAStatus.GENERATING else None
        self._answer_item_id = item.id
        done = item.status is QAStatus.DONE
        for button in (self.alt, self.more, self.useful, self.not_useful, self.copy):
            button.setEnabled(done)

    def _followup(self, kind: str) -> None:
        item_id = self._selected_id()
        if item_id is None:
            return
        if kind == "alt":
            self._c.assistant.regenerate(item_id)
        else:
            self._c.assistant.expand(item_id)

    def _feedback(self, useful: bool) -> None:
        item_id = self._selected_id()
        if item_id is not None:
            self._c.assistant.mark_useful(item_id, useful)
            self.useful.setText("👍 Útil ✓" if useful else "👍 Útil")

    def _copy(self) -> None:
        item_id = self._selected_id()
        if item_id is not None and item_id in self._items:
            QGuiApplication.clipboard().setText(self._items[item_id].answer)

    def clear(self) -> None:
        self._c.assistant.clear()
        self.questions.clear()
        self._items.clear()
        self._list_items.clear()
        self.transcript.clear()
        self.partial.setText("")
        self._render(QAItem(0, "", "manual", 0.0))


class TranslationPage(QWidget):
    def __init__(self, controller: CopilotController) -> None:
        super().__init__()
        self._c = controller
        layout = QVBoxLayout(self)
        layout.addWidget(title("Traducción en tiempo real"))
        layout.addWidget(
            hint(
                "El texto en gris y cursiva es provisional (la persona sigue "
                "hablando). Solo se traducen los segmentos confirmados; ⚠️ marca "
                "una transcripción de baja confianza."
            )
        )
        langs = QHBoxLayout()
        self.source = language_combo(include_auto=True)
        self.target = language_combo(include_auto=False)
        langs.addWidget(QLabel("Idioma de origen:"))
        langs.addWidget(self.source)
        langs.addWidget(QLabel("Traducir a:"))
        langs.addWidget(self.target)
        langs.addStretch(1)
        layout.addLayout(langs)
        self.controls = SessionControls(controller, "translation")
        self.controls.startClicked.connect(self.start)
        layout.addWidget(self.controls)
        self.view = QTextBrowser()
        layout.addWidget(self.view, 1)
        actions = QHBoxLayout()
        copy = QPushButton("⧉ Copiar")
        export = QPushButton("💾 Exportar…")
        summary = QPushButton("📝 Resumir")
        clear = QPushButton("Limpiar")
        for button in (copy, export, summary, clear):
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.summary = QTextBrowser()
        self.summary.setVisible(False)
        layout.addWidget(self.summary)
        copy.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(
                self._c.translator.export("txt")
            )
        )
        export.clicked.connect(self.export)
        summary.clicked.connect(self.summarize)
        clear.clicked.connect(self.clear)
        controller.translationUpdated.connect(lambda _s: self.refresh_view())
        select_data(self.source, controller.settings.translation_source)
        select_data(self.target, controller.settings.translation_target)

    def start(self) -> None:
        s = self._c.settings
        s.translation_source = str(self.source.currentData() or "")
        s.translation_target = str(self.target.currentData() or "en")
        self.controls.apply_to_settings()
        self._c.save_settings(s)
        try:
            self._c.start_session("translation")
        except ValueError as exc:
            QMessageBox.warning(self, "No se puede iniciar", str(exc))

    def refresh_view(self) -> None:
        rows: list[str] = []
        for seg in self._c.translator.segments():
            if not seg.original:
                continue
            rows.append(self._row(seg))
        self.view.setHtml("".join(rows))
        self.view.moveCursor(QTextCursor.MoveOperation.End)

    @staticmethod
    def _row(seg: TranslationSegment) -> str:
        if seg.status is SegmentStatus.PROVISIONAL:
            return f"<p style='color:{MUTED}'><i>{escape(seg.original)} …</i></p>"
        flag = " ⚠️" if seg.low_confidence else ""
        translated = {
            SegmentStatus.DONE: f"<b>{escape(seg.translation)}</b>",
            SegmentStatus.FAILED: f"<span style='color:#ff6b6b'>sin traducción: {escape(seg.error)}</span>",
        }.get(seg.status, f"<span style='color:{MUTED}'>traduciendo…</span>")
        return (
            f"<p><span style='color:{MUTED}'>{escape(seg.source.speaker_label)}: "
            f"{escape(seg.original)}{flag}</span><br>{translated}</p>"
        )

    def export(self) -> None:
        path, selected = QFileDialog.getSaveFileName(
            self,
            "Exportar transcripción",
            "transcripcion.md",
            "Markdown (*.md);;Texto (*.txt);;PDF (*.pdf)",
        )
        if not path:
            return
        try:
            if path.lower().endswith(".pdf") or "PDF" in selected:
                export_pdf(self._c.translator.export("md"), path)
            elif path.lower().endswith(".txt"):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(self._c.translator.export("txt"))
            else:
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(self._c.translator.export("md"))
        except OSError as exc:
            QMessageBox.warning(self, "Exportar", f"No se pudo guardar: {exc}")

    def summarize(self) -> None:
        self.summary.setVisible(True)
        self.summary.setPlainText("Generando resumen…")

        def work() -> str:
            return self._c.translator.summarize()

        run_async(
            work,
            on_done=lambda text: self.summary.setMarkdown(str(text)),
            on_error=lambda msg: self.summary.setPlainText(
                f"No se pudo resumir: {msg}"
            ),
        )

    def clear(self) -> None:
        self._c.translator.clear()
        self.view.clear()
        self.summary.clear()
        self.summary.setVisible(False)
