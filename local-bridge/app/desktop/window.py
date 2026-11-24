"""Ventana flotante de Extdator (PySide6).

Características:
  * Estética terminal oscura, frameless, arrastrable y redimensionable.
  * Always on Top + WS_EX_NOACTIVATE: nunca roba el foco del navegador.
  * Hover Scroll: la rueda desplaza el panel sin hacer clic (hook Win32 +
    enrutamiento nativo de Windows como respaldo).
  * Streaming de tokens con autoscroll inteligente.
  * Persistencia de posición, tamaño, opacidad y always-on-top (QSettings).
"""

from __future__ import annotations

import base64
import logging
import os
import tempfile
from typing import Optional

from PySide6.QtCore import QTimer, QSettings, Qt
from PySide6.QtGui import QCursor, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSlider,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from .. import ocr_manager
from ..config import Config
from ..ollama_client import OllamaClient, OllamaError
from ..queue_manager import (
    Bus,
    ClearConversation,
    ConfigUpdate,
    ImageRequest,
    RuntimeState,
    SelectionRequest,
)
from ..settings_store import (
    LIVE_KEYS,
    SettingsStore,
    SettingsValidationError,
    restart_required_for,
)
from .onboarding import (
    ONBOARDING_SEEN_KEY,
    OnboardingDialog,
    should_show_onboarding,
)
from .styles import APP_QSS
from .widgets import (
    AssistantMessage,
    ChatScroller,
    IconButton,
    SystemNote,
    TitleBar,
    UserMessage,
    make_chat_area,
)
from .window_manager import HoverScrollHook, NoActivateFilter, apply_noactivate
from .workers import GenerationWorker, OCRWorker, StatusWorker

log = logging.getLogger("extdator.window")

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# Límite prudente de imagen codificada (coincide con el del servidor).
MAX_CAPTURE_B64 = 12_000_000

# Banda de redimensionado (px) y tamaño mínimo del panel.
RESIZE_BAND = 6
MIN_W, MIN_H = 340, 320


class BridgeWindow(QMainWindow):
    """Panel flotante siempre visible con las respuestas de Ollama."""

    def __init__(
        self,
        cfg: Config,
        bus: Bus,
        state: RuntimeState,
        ollama: OllamaClient,
        parent: Optional[QWidget] = None,
        settings_store: Optional[SettingsStore] = None,
        pairing=None,
        show_onboarding: bool = True,
    ) -> None:
        flags = (
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if cfg.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.setWindowTitle("Extdator")

        self._cfg = cfg
        self._bus = bus
        self._state = state
        self._ollama = ollama
        self._settings_store = settings_store
        self._pairing = pairing
        self._show_onboarding = show_onboarding
        self._onboarding_scheduled = False
        self._onboarding_dialog: Optional[OnboardingDialog] = None
        self._on_top = cfg.always_on_top
        self._opacity = cfg.window_opacity
        self._settings = QSettings("Extdator", "Bridge")
        self._streaming_widget: Optional[AssistantMessage] = None
        self._last_assistant_text = ""
        self._hook = HoverScrollHook()
        self._native_filter = NoActivateFilter(lambda: self._hwnd())
        self._native_filter_installed = False
        # Estado del pie/spinner (debe existir antes de _build_ui).
        self._spinner_frame = 0
        self._footer_stable = (
            "Extdator listo. Selecciona texto en el navegador y pulsa el botón ✦."
        )
        self._footer_transient_until = 0.0
        self._footer_transient_text = ""
        self._footer_is_error = False
        # Estado del flujo de captura de pantalla.
        self._capture_selector = None
        self._capture_was_visible = True
        self._ocr_workers: list = []
        self._pending_ocr_instruction: Optional[str] = None

        self._build_ui()

        # Workers
        self._worker = GenerationWorker(cfg, bus, state, ollama)
        self._worker.queryStarted.connect(self._on_query_started)
        self._worker.chunkReceived.connect(self._on_chunk)
        self._worker.generationFinished.connect(self._on_finished)
        self._worker.generationFailed.connect(self._on_failed)
        self._worker.generationCancelled.connect(self._on_cancelled)
        self._worker.conversationCleared.connect(self._on_cleared)
        self._worker.configApplied.connect(self._on_config_applied)
        self._status_worker = StatusWorker(cfg, ollama)
        self._status_worker.statusChanged.connect(self._on_status_changed)
        self._status_worker.modelsReady.connect(self._on_models_ready)

        # Arranque de los workers (hilo de generación + sondeo de estado).
        self._worker.start()
        self._status_worker.start()

        # Temporizadores: cola pendiente, spinner y estado transitorio del pie.
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(400)
        self._poll_timer.timeout.connect(self._on_poll)
        self._poll_timer.start()
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(120)
        self._spinner_timer.timeout.connect(self._on_spinner_tick)
        self._show_footer_stable()

        self._restore_geometry()
        self._apply_opacity(self._opacity)

        # Restaurar el proveedor activo persistido (Ollama o API remota).
        provider_id, remote_model = state.provider()
        if provider_id != "ollama" and remote_model:
            self._titlebar.set_model(f"{remote_model} ({provider_id})")

    # ------------------------------------------------------------------ #
    # Construcción de la UI
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("rootCentral")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._titlebar = TitleBar(self._cfg.ollama_model, self)
        self._titlebar.pinToggled.connect(self.set_on_top)
        self._titlebar.minimizeClicked.connect(self.showMinimized)
        self._titlebar.closeClicked.connect(self.close)
        self._titlebar.copyClicked.connect(self._copy_last)
        self._titlebar.clearClicked.connect(self._clear_conversation)
        self._titlebar.stopClicked.connect(self._stop_generation)
        self._titlebar.menuClicked.connect(self._open_menu)
        self._titlebar.modelsClicked.connect(self._open_model_selector)
        self._titlebar.screenshotClicked.connect(self._start_capture)
        layout.addWidget(self._titlebar)

        self._chat_scroll, self._chat_container, self._chat_vbox = make_chat_area()
        layout.addWidget(self._chat_scroll, 1)
        self._scroller = ChatScroller(self._chat_scroll.verticalScrollBar(), self)

        self._footer = QLabel(self._footer_stable)
        self._footer.setObjectName("footer")
        self._footer.setWordWrap(True)
        layout.addWidget(self._footer)

        self.setCentralWidget(central)
        self.setStyleSheet(APP_QSS)
        self.setMinimumSize(MIN_W, MIN_H)
        self._install_native_filter()

    # ------------------------------------------------------------------ #
    # Helpers nativos
    # ------------------------------------------------------------------ #
    def _hwnd(self) -> int:
        try:
            return int(self.winId() or 0)
        except Exception:  # pragma: no cover
            return 0

    def _install_native_filter(self) -> None:
        if not self._native_filter_installed:
            app = QApplication.instance()
            if app is not None:
                app.installNativeEventFilter(self._native_filter)
                self._native_filter_installed = True

    def _uninstall_native_filter(self) -> None:
        if self._native_filter_installed:
            app = QApplication.instance()
            if app is not None:
                app.removeNativeEventFilter(self._native_filter)
            self._native_filter_installed = False

    # ------------------------------------------------------------------ #
    def showEvent(self, event) -> None:  # noqa: N802 (PySide)
        super().showEvent(event)
        hwnd = self._hwnd()
        if hwnd:
            apply_noactivate(hwnd)
            if self._cfg.hover_scroll_hook and not self._hook.installed:
                self._hook.install(hwnd)
            else:
                self._hook.update_target(hwnd)
        if self._show_onboarding and not self._onboarding_scheduled:
            self._onboarding_scheduled = True
            # Dejar que la ventana se pinte antes de abrir el diálogo modal.
            QTimer.singleShot(700, self._maybe_show_onboarding)

    def closeEvent(self, event) -> None:  # noqa: N802 (PySide)
        self._save_geometry()
        self.stop_workers()
        self._hook.uninstall()
        self._uninstall_native_filter()
        log.info("Ventana cerrada.")
        super().closeEvent(event)

    def stop_workers(self) -> None:
        """Detiene workers de forma ordenada (idempotente)."""
        if self._status_worker.isRunning():
            self._status_worker.stop()
            self._status_worker.wait(1500)
        if self._worker.isRunning():
            self._state.request_stop()
            self._worker.stop()
            if not self._worker.wait(2000):
                # Aborta sockets pendientes (p. ej. modelo cargándose) y reintenta.
                self._ollama.close()
                self._worker.wait(2000)
        self._stop_ocr_workers()

    # ------------------------------------------------------------------ #
    # Persistencia de geometría
    # ------------------------------------------------------------------ #
    def _restore_geometry(self) -> None:
        geometry = self._settings.value("geometry")
        restored = False
        if isinstance(geometry, (str, bytes)) and geometry:
            restored = bool(
                self.restoreGeometry(
                    geometry
                    if isinstance(geometry, bytes)
                    else geometry.encode("latin-1")
                )
            )
        screen = QGuiApplication.primaryScreen().availableGeometry()
        frame = self.frameGeometry()
        if not restored or not screen.intersects(frame):
            width = max(MIN_W, self._cfg.window_width)
            height = max(MIN_H, self._cfg.window_height)
            self.resize(width, height)
            # Posición inicial recomendada: esquina inferior derecha.
            self.move(
                screen.right() - width - 24,
                screen.bottom() - height - 24,
            )

    def _save_geometry(self) -> None:
        self._settings.setValue("geometry", bytes(self.saveGeometry().data()))
        self._settings.setValue("opacity", self._opacity)
        self._settings.setValue("onTop", self._on_top)

    def on_window_moved(self) -> None:
        self._save_geometry()

    def resizeEvent(self, event) -> None:  # noqa: N802 (PySide)
        super().resizeEvent(event)
        self._save_geometry()

    # ------------------------------------------------------------------ #
    # Redimensionado manual (frameless)
    # ------------------------------------------------------------------ #
    def _edge_at(self, pos) -> int:
        """Devuelve los bordes Qt para el cursor en ``pos`` (coords ventana)."""
        edges = 0
        width, height = self.width(), self.height()
        if pos.x() <= RESIZE_BAND:
            edges |= Qt.Edge.LeftEdge
        if pos.x() >= width - RESIZE_BAND:
            edges |= Qt.Edge.RightEdge
        if pos.y() >= height - RESIZE_BAND:
            edges |= Qt.Edge.BottomEdge
        if pos.y() <= RESIZE_BAND and not self._over_titlebar_controls(pos):
            edges |= Qt.Edge.TopEdge
        return edges

    def _over_titlebar_controls(self, pos) -> bool:
        return pos.y() <= TitleBar.HEIGHT and bool(self._titlebar.childAt(pos))

    def mousePressEvent(self, event) -> None:  # noqa: N802 (PySide)
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            edges = self._edge_at(pos)
            if edges:
                self._resize_origin = (
                    event.globalPosition().toPoint(),
                    self.frameGeometry(),
                    edges,
                )
                self._resize_edges = edges
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (PySide)
        pos = event.position().toPoint()
        if hasattr(self, "_resize_origin") and getattr(self, "_resize_origin", None):
            origin_global, origin_frame, edges = self._resize_origin
            delta = event.globalPosition().toPoint() - origin_global
            new = origin_frame.adjusted(
                delta.x() if edges & Qt.Edge.LeftEdge else 0,
                delta.y() if edges & Qt.Edge.TopEdge else 0,
                delta.x() if edges & Qt.Edge.RightEdge else 0,
                delta.y() if edges & Qt.Edge.BottomEdge else 0,
            )
            if new.width() >= MIN_W and new.height() >= MIN_H:
                self.setGeometry(new)
            event.accept()
            return
        edges = self._edge_at(pos)
        if edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
            if edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
                self.setCursor(
                    Qt.CursorShape.SizeFDiagCursor
                    if bool(edges & Qt.Edge.LeftEdge) == bool(edges & Qt.Edge.TopEdge)
                    else Qt.CursorShape.SizeBDiagCursor
                )
            else:
                self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (PySide)
        if getattr(self, "_resize_origin", None):
            self._resize_origin = None
            self.unsetCursor()
            self._save_geometry()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------ #
    # Slots de streaming
    # ------------------------------------------------------------------ #
    def _insert_message(self, widget: QWidget) -> None:
        self._chat_vbox.insertWidget(self._chat_vbox.count() - 1, widget)

    def _on_query_started(self, text: str) -> None:
        self._insert_message(UserMessage(text))
        self._streaming_widget = AssistantMessage(model=self._cfg.ollama_model)
        self._insert_message(self._streaming_widget)
        self._titlebar.set_generating(True)
        self._spinner_timer.start()
        self._set_footer_busy("generando…")
        self._scroller.ensure_bottom()

    def _on_chunk(self, delta: str) -> None:
        if self._streaming_widget is not None:
            self._streaming_widget.append_streaming(delta)
            self._scroller.ensure_bottom()

    def _on_finished(self, stats: dict) -> None:
        self._spinner_timer.stop()
        self._titlebar.set_generating(False)
        widget, self._streaming_widget = self._streaming_widget, None
        if widget is not None:
            self._last_assistant_text = widget.finalize()
        tokens_per_s = stats.get("tokens_per_s")
        if tokens_per_s:
            self._set_footer_stable(f"listo · {tokens_per_s:.1f} tok/s")
        else:
            self._set_footer_stable("listo")
        self._scroller.ensure_bottom()

    def _on_failed(self, message: str, kind: str) -> None:
        self._spinner_timer.stop()
        self._titlebar.set_generating(False)
        self._streaming_widget = None  # el parcial queda visible tal cual
        self._insert_message(SystemNote("⚠ " + message, error=True))
        self._set_footer_transient("Error: " + message, error=True, seconds=6)
        if kind == "model":
            self._status_worker.request_models()

    def _on_cancelled(self) -> None:
        self._spinner_timer.stop()
        self._titlebar.set_generating(False)
        self._streaming_widget = None
        self._insert_message(SystemNote("■ generación cancelada"))

    def _on_cleared(self) -> None:
        self._streaming_widget = None
        while self._chat_vbox.count() > 1:
            item = self._chat_vbox.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._last_assistant_text = ""
        self._insert_message(SystemNote("— conversación reiniciada —"))
        self._set_footer_transient("Conversación limpia. Historial vacío.", seconds=3)

    def _on_config_applied(self, changes: dict) -> None:
        if "model" in changes:
            self._titlebar.set_model(changes["model"])

    # ------------------------------------------------------------------ #
    # Indicadores de estado
    # ------------------------------------------------------------------ #
    def _on_status_changed(self, reachable: bool, detail: str, model_ok: bool) -> None:
        model = self._cfg.ollama_model
        if reachable and model_ok:
            self._titlebar.set_status(True, f"Ollama {detail} · modelo listo")
        elif reachable:
            self._titlebar.set_status(
                False, f"Ollama {detail} · el modelo '{model}' no está instalado"
            )
        else:
            self._titlebar.set_status(False, f"Ollama sin conexión: {detail}")

    def _on_models_ready(self, models: list) -> None:
        if models:
            self._set_footer_transient(
                "Modelos disponibles en Ollama: " + ", ".join(models[:6]), seconds=8
            )

    def _on_poll(self) -> None:
        pending = self._bus.qsize()
        generating = self._state.is_generating()
        if not generating and self._titlebar._stop.isVisible():
            self._titlebar.set_generating(False)
        suffix = f" · en cola: {pending}" if pending else ""
        if generating:
            frame = SPINNER_FRAMES[self._spinner_frame]
            self._footer_busy(frame + " generando…" + suffix)
        elif self._time() >= self._footer_transient_until:
            self._footer_busy(self._footer_stable + suffix)

    def _on_spinner_tick(self) -> None:
        self._spinner_frame = (self._spinner_frame + 1) % len(SPINNER_FRAMES)

    @staticmethod
    def _time() -> float:
        from time import monotonic

        return monotonic()

    # ------------------------------------------------------------------ #
    # Pie de ventana
    # ------------------------------------------------------------------ #
    def _footer_busy(self, text: str) -> None:
        self._footer.setText(text)

    def _show_footer_stable(self) -> None:
        self._footer_busy(self._footer_stable)

    def _set_footer_stable(self, text: str) -> None:
        self._footer_stable = text
        self._footer_busy(text)

    def _set_footer_busy(self, text: str) -> None:
        self._footer.setStyleSheet("")
        self._footer_busy(text)

    def _set_footer_transient(
        self, text: str, error: bool = False, seconds: float = 3.0
    ) -> None:
        self._footer_transient_text = text
        self._footer_transient_until = self._time() + seconds
        self._footer_is_error = error
        self._footer.setStyleSheet("color: #ff6b6b;" if error else "color: #3ddc97;")
        self._footer_busy(text)
        QTimer.singleShot(int(seconds * 1000), self._expire_footer_transient)

    def _expire_footer_transient(self) -> None:
        if self._time() >= self._footer_transient_until:
            self._footer.setStyleSheet("")
            self._footer_busy(self._footer_stable)

    # ------------------------------------------------------------------ #
    # Acciones
    # ------------------------------------------------------------------ #
    def _copy_last(self) -> None:
        if not self._last_assistant_text.strip():
            self._set_footer_transient("No hay respuestas que copiar.", seconds=2.5)
            return
        QApplication.clipboard().setText(self._last_assistant_text)
        self._set_footer_transient("Respuesta copiada al portapapeles.", seconds=2.5)

    def _clear_conversation(self) -> None:
        self._state.request_clear()  # aborta la generación si hay una en curso
        self._bus.publish(ClearConversation())
        self._set_footer_transient("Limpiando conversación…", seconds=2)

    def _stop_generation(self) -> None:
        self._state.request_stop()
        self._set_footer_transient("Deteniendo generación…", seconds=2.5)

    def set_on_top(self, enabled: bool) -> None:
        self._on_top = enabled
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        if self.isVisible():
            self.show()
        self._save_geometry()
        hwnd = self._hwnd()
        if hwnd:
            apply_noactivate(hwnd)

    def _open_menu(self) -> None:
        menu = QMenu(self)

        models_action = menu.addAction("🤖  Modelos de IA…")
        models_action.triggered.connect(lambda: self._open_model_selector())
        providers_action = menu.addAction("🔌  Proveedores de API…")
        providers_action.triggered.connect(self._open_providers)
        settings_action = menu.addAction("⚙  Ajustes…")
        settings_action.triggered.connect(lambda: self._open_settings())
        pairing_action = menu.addAction("🔗  Emparejar extensión…")
        pairing_action.triggered.connect(self._open_pairing)
        onboarding_action = menu.addAction("🚀  Asistente de inicio…")
        onboarding_action.triggered.connect(self._open_onboarding)

        menu.addSeparator()

        copy_action = menu.addAction("⧉  Copiar última respuesta")
        copy_action.triggered.connect(self._copy_last)
        clear_action = menu.addAction("⟲  Nueva conversación")
        clear_action.triggered.connect(self._clear_conversation)
        stop_action = menu.addAction("■  Detener generación")
        stop_action.triggered.connect(self._stop_generation)

        menu.addSeparator()

        pin_action = menu.addAction("📌  Siempre visible")
        pin_action.setCheckable(True)
        pin_action.setChecked(self._on_top)
        pin_action.toggled.connect(self.set_on_top)

        opacity_widget = QWidget()
        opacity_layout = QHBoxLayout(opacity_widget)
        opacity_layout.setContentsMargins(12, 6, 12, 6)
        opacity_label = QLabel("Opacidad")
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(40, 100)
        slider.setValue(self._opacity)
        slider.setFixedWidth(140)
        opacity_layout.addWidget(opacity_label)
        opacity_layout.addWidget(slider)
        opacity_action = QWidgetAction(menu)
        opacity_action.setDefaultWidget(opacity_widget)
        menu.addAction(opacity_action)
        slider.valueChanged.connect(self._on_opacity_changed)

        menu.popup(QCursor.pos())

    def _on_opacity_changed(self, value: int) -> None:
        self._apply_opacity(value)

    def _apply_opacity(self, value: int) -> None:
        self._opacity = max(40, min(100, int(value)))
        self.setWindowOpacity(self._opacity / 100.0)

    # ------------------------------------------------------------------ #
    # Selector de modelos / proveedores / ajustes / emparejamiento
    # ------------------------------------------------------------------ #
    def _open_model_selector(self, parent: Optional[QWidget] = None) -> None:
        from .dialogs import ModelSelectorDialog

        dialog = ModelSelectorDialog(
            self._ollama, self._cfg.ollama_model, parent=parent or self
        )
        dialog.modelSelected.connect(self.apply_model)
        dialog.exec()

    def apply_model(self, name: str) -> None:
        """Cambia el modelo local y vuelve a Ollama como proveedor activo."""
        if not name.strip():
            return
        name = name.strip()
        self._cfg.ollama_model = name
        self._state.set_model(name)
        self._state.set_provider("ollama", "")
        if self._settings_store is not None:
            try:
                self._settings_store.update({"ollama_model": name})
            except SettingsValidationError as exc:
                log.warning("No se pudo persistir el modelo: %s", exc)
        self._titlebar.set_model(name)
        self._set_footer_transient(f"Modelo activo: {name} (Ollama local).", seconds=3)

    def _use_remote_provider(self, provider_id: str, model: str) -> None:
        """Cambia a un proveedor de API remoto (desde ProvidersDialog)."""
        if not provider_id or not model:
            return
        self._state.set_provider(provider_id, model)
        label = f"{model} ({provider_id})"
        self._titlebar.set_model(label)
        if self._settings_store is not None:
            try:
                self._settings_store.update(
                    {
                        "provider": provider_id,
                        "remote_model": model,
                    }
                )
            except SettingsValidationError as exc:
                log.warning("No se pudo persistir el proveedor: %s", exc)
        self._set_footer_transient(
            f"Proveedor activo: {label}. Las consultas se enviarán a la API " "remota.",
            seconds=4,
        )

    def _open_providers(self) -> None:
        from .providers_dialog import ProvidersDialog

        dialog = ProvidersDialog(parent=self)
        dialog.providerChosen.connect(self._use_remote_provider)
        dialog.exec()

    def _open_settings(self, parent: Optional[QWidget] = None) -> None:
        from .dialogs import SettingsDialog

        dialog = SettingsDialog(self._cfg, parent=parent or self)
        dialog.settingsChanged.connect(self.apply_settings)
        dialog.exec()

    def apply_settings(self, changes: dict) -> None:
        """Aplica ajustes del diálogo: guarda, actualiza cfg y avisa."""
        if changes.get("__reset__"):
            if self._settings_store is not None:
                self._settings_store.reset()
                QMessageBox.information(
                    self,
                    "Ajustes restaurados",
                    "Tus ajustes guardados se borraron. Reinicia Extdator "
                    "para volver a los valores del archivo .env.",
                )
            return
        if self._settings_store is None:
            QMessageBox.warning(
                self,
                "Ajustes no disponibles",
                "Extdator arrancó sin almacén de ajustes; edita local-bridge\\.env.",
            )
            return
        try:
            applied = self._settings_store.update(changes)
        except SettingsValidationError as exc:
            QMessageBox.warning(self, "Ajuste inválido", str(exc))
            return
        for key, value in applied.items():
            setattr(self._cfg, key, value)
        live = {k: v for k, v in applied.items() if k in LIVE_KEYS}
        if live:
            # Cambios en caliente: el worker los aplica en la próxima petición
            # y la ventana refleja el modelo/prompt al instante.
            self._bus.publish(ConfigUpdate(changes=live))
            if "ollama_model" in live:
                self._titlebar.set_model(live["ollama_model"])
                self._state.set_model(live["ollama_model"])
        restart = restart_required_for(list(applied))
        if restart:
            QMessageBox.information(
                self,
                "Reinicio necesario",
                "Los siguientes ajustes requieren reiniciar Extdator para "
                f"aplicarse:\n· {chr(10).join(restart)}",
            )
        self._set_footer_transient(f"Ajustes guardados ({len(applied)}).", seconds=3)

    def _open_pairing(self) -> None:
        from .dialogs import PairingDialog

        if self._pairing is None or not self._cfg.token:
            self._set_footer_transient(
                "Emparejamiento no disponible (no hay token configurado).",
                error=True,
                seconds=4,
            )
            return
        dialog = PairingDialog(self._pairing, parent=self)
        dialog.exec()

    # ------------------------------------------------------------------ #
    # Asistente de primer inicio
    # ------------------------------------------------------------------ #
    def _maybe_show_onboarding(self) -> None:
        """Abre el asistente una sola vez, tras pintar la ventana."""
        if not self.isVisible() or self._onboarding_dialog is not None:
            return
        seen = bool(self._settings.value(ONBOARDING_SEEN_KEY, False, type=bool))
        if not should_show_onboarding(
            seen=seen, platform_name=QGuiApplication.platformName()
        ):
            return
        self._open_onboarding()

    def _open_onboarding(self, _checked: bool = False) -> None:
        if self._onboarding_dialog is not None:
            return
        dialog = OnboardingDialog(self._cfg, self._ollama, self._pairing, parent=self)
        dialog.openModelsRequested.connect(
            lambda: self._open_model_selector(parent=dialog)
        )
        dialog.openSettingsRequested.connect(lambda: self._open_settings(parent=dialog))
        # Se marca como visto al mostrarlo: cerrar el asistente no debe
        # hacer que reaparezca en cada arranque. Sigue disponible en ⚙.
        self._settings.setValue(ONBOARDING_SEEN_KEY, True)
        self._settings.sync()
        self._onboarding_dialog = dialog
        try:
            dialog.exec()
        finally:
            self._onboarding_dialog = None

    # ------------------------------------------------------------------ #
    # Captura de pantalla por región
    # ------------------------------------------------------------------ #
    def _start_capture(self) -> None:
        if self._state.is_generating():
            self._set_footer_transient(
                "Hay una generación en curso; espera o deténla antes de " "capturar.",
                error=True,
                seconds=4,
            )
            return
        from .screenshot import RegionSelector

        # El propio panel no debe salir en la captura.
        self._capture_was_visible = self.isVisible()
        self.hide()
        selector = RegionSelector(self)
        self._capture_selector = selector
        selector.captured.connect(self._on_capture_captured)
        selector.cancelled.connect(self._on_capture_cancelled)
        selector.start()

    def _on_capture_cancelled(self) -> None:
        self._capture_selector = None
        self._restore_after_capture()
        self._set_footer_transient("Captura cancelada.", seconds=2)

    def _on_capture_captured(self, pixmap: QPixmap) -> None:
        self._capture_selector = None
        self._restore_after_capture()
        self._show_capture_preview(pixmap)

    def _restore_after_capture(self) -> None:
        if self._capture_was_visible:
            self.show()

    def _detect_capture_methods(self) -> tuple[bool, bool]:
        """(visión disponible, OCR disponible) para el modelo activo."""
        vision = False
        try:
            caps = self._ollama.show(self._cfg.ollama_model).get("capabilities", [])
            vision = "vision" in caps
        except OllamaError:
            pass
        return vision, bool(ocr_manager.available_engines())

    def _show_capture_preview(self, pixmap: QPixmap) -> None:
        from .screenshot import ScreenshotPreviewDialog

        if pixmap.isNull():
            self._set_footer_transient(
                "La captura llegó vacía; inténtalo de nuevo.",
                error=True,
                seconds=4,
            )
            return
        vision, ocr_ok = self._detect_capture_methods()
        dialog = ScreenshotPreviewDialog(
            pixmap,
            vision_available=vision,
            ocr_available=ocr_ok,
            model_name=self._cfg.ollama_model,
            parent=self,
        )
        dialog.repeatRequested.connect(self._start_capture)
        result = dialog.exec()
        if result == QDialog.DialogCode.Accepted:
            self._dispatch_capture(pixmap, dialog.method(), dialog.instruction())

    def _dispatch_capture(self, pixmap: QPixmap, method: str, instruction: str) -> None:
        """Guarda temporal y enruta: visión directa o OCR local."""
        path = self._save_capture_temp(pixmap)
        if path is None:
            self._set_footer_transient(
                "No se pudo guardar la captura temporal.",
                error=True,
                seconds=4,
            )
            return
        if method == "vision":
            self._send_capture_vision(path, instruction)
        elif method == "ocr":
            self._start_ocr(path, instruction)
        else:
            self._delete_file(path)
            self._set_footer_transient(
                "No hay método de procesamiento disponible: instala un "
                "modelo con visión (🤖) o un motor OCR.",
                error=True,
                seconds=6,
            )

    @staticmethod
    def _delete_file(path: str) -> None:
        try:
            os.unlink(path)
        except OSError:
            log.warning("No se pudo borrar la captura temporal %s", path)

    def _save_capture_temp(self, pixmap: QPixmap) -> Optional[str]:
        """PNG temporal (se elimina al terminar el procesamiento)."""
        import time as _time

        base = os.path.join(tempfile.gettempdir(), "extdator_capture")
        path = f"{base}_{int(_time.time() * 1000)}.png"
        if not pixmap.save(path, "PNG"):
            return None
        return path

    def _send_capture_vision(self, path: str, instruction: str) -> None:
        """Modo B: imagen + instrucción → modelo multimodal activo."""
        try:
            with open(path, "rb") as handle:
                image_b64 = base64.b64encode(handle.read()).decode("ascii")
        except OSError as exc:
            self._delete_file(path)
            self._set_footer_transient(
                f"No se pudo leer la captura: {exc}",
                error=True,
                seconds=4,
            )
            return
        self._delete_file(path)  # el b64 ya está en memoria
        if len(image_b64) > MAX_CAPTURE_B64:
            self._set_footer_transient(
                "La captura es demasiado grande; reduce la región seleccionada.",
                error=True,
                seconds=5,
            )
            return
        self._bus.publish(
            ImageRequest(
                image_b64=image_b64,
                instruction=instruction,
                source_url="screenshot://desktop",
            )
        )
        self._set_footer_transient("Captura en cola para el modelo.", seconds=3)

    def _start_ocr(self, path: str, instruction: str) -> None:
        """Modo A: OCR local (QThread) → texto al pipeline normal."""
        if self._state.is_generating():
            self._delete_file(path)
            self._set_footer_transient(
                "Hay una generación en curso; espera o deténla.",
                error=True,
                seconds=4,
            )
            return
        engine = ocr_manager.recommend()  # el mejor disponible ahora
        worker = OCRWorker(path, engine=engine, parent=self)
        self._pending_ocr_instruction = instruction
        worker.transcribed.connect(self._on_ocr_done)
        worker.failed.connect(self._on_ocr_failed)
        self._ocr_workers.append(worker)
        self._set_footer_transient("Transcribiendo la captura (OCR local)…", seconds=4)
        worker.start()

    def _on_ocr_done(self, text: str, engine_label: str) -> None:
        instruction = self._pending_ocr_instruction or ""
        self._pending_ocr_instruction = None
        self._prune_ocr_workers()
        if not text.strip():
            self._set_footer_transient(
                "El OCR no reconoció texto legible en la captura.",
                error=True,
                seconds=5,
            )
            return
        payload = (
            f"{instruction}\n\n[Transcripción OCR local — {engine_label}]\n" f"{text}"
        )
        if self._state.is_generating():
            self._set_footer_transient(
                "El OCR terminó pero hay una generación en curso; vuelve a "
                "enviar la captura cuando esté libre.",
                error=True,
                seconds=6,
            )
            return
        self._bus.publish(
            SelectionRequest(
                text=payload,
                source_url="screenshot://ocr",
            )
        )

    def _on_ocr_failed(self, message: str) -> None:
        self._pending_ocr_instruction = None
        self._prune_ocr_workers()
        self._set_footer_transient(f"⚠ OCR: {message}", error=True, seconds=8)

    def _prune_ocr_workers(self) -> None:
        for worker in list(self._ocr_workers):
            if not worker.isRunning():
                worker.deleteLater()
                self._ocr_workers.remove(worker)

    def _stop_ocr_workers(self) -> None:
        for worker in self._ocr_workers:
            worker.stop_requested = True
            worker.wait(3000)
        ocr_manager.release_engine()  # libera memoria al cerrar
