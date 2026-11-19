"""Diálogos modales del panel: selector de modelos, ajustes y emparejamiento.

* ``ModelSelectorDialog`` — catálogo inteligente: detecta hardware, clasifica
  los modelos (🟢🟡🟠🔴) y permite seleccionar o descargar con confirmación.
* ``SettingsDialog`` — configuración centralizada sin editar archivos.
* ``PairingDialog`` — código efímero para vincular la extensión.

Estética: se reutilizan los colores y tipografía de ``styles.py``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from ..ollama_client import OllamaClient, OllamaError
from ..settings_store import ALLOWED_KEYS
from .styles import ACCENT, BORDER, MUTED, PANEL, TEXT
from .workers import HardwareWorker, PullWorker

TIER_ORDER = {"perfecto": 0, "regular": 1, "alto": 2, "imposible": 3}


def _fmt_gb(size_bytes: Optional[int]) -> str:
    if not size_bytes:
        return "tamaño desconocido"
    return f"{size_bytes / (1024 ** 3):.1f} GB"


# --------------------------------------------------------------------- #
# Fila de modelo
# --------------------------------------------------------------------- #
class ModelRow(QFrame):
    """Una fila del catálogo: insignia, metadatos, explicación y acción."""

    selectRequested = Signal(str)
    pullRequested = Signal(str)

    def __init__(
        self,
        name: str,
        meta: str,
        explanation: str,
        tier: str,
        color: str,
        emoji: str,
        action: str,
        enabled: bool,
        parent: Optional[QWidget] = None,
        tooltip: str = "",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("modelRow")
        self.name = name
        self.tier = tier
        self.setStyleSheet(
            f"#modelRow {{ background: {PANEL}; border: 1px solid {BORDER};"
            " border-radius: 8px; }"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        badge = QLabel(emoji)
        badge.setStyleSheet(f"color: {color}; font-size: 18px; border: none;")
        badge.setToolTip(tier.upper())
        layout.addWidget(badge)

        middle = QVBoxLayout()
        middle.setSpacing(2)
        title = QLabel(name)
        title.setStyleSheet(f"color: {TEXT}; font-weight: 700; border: none;")
        meta_label = QLabel(meta)
        meta_label.setStyleSheet(f"color: {MUTED}; font-size: 10px; border: none;")
        explanation_label = QLabel(explanation)
        explanation_label.setWordWrap(True)
        explanation_label.setStyleSheet(
            f"color: {MUTED}; font-size: 10px; border: none;"
        )
        middle.addWidget(title)
        middle.addWidget(meta_label)
        middle.addWidget(explanation_label)
        layout.addLayout(middle, 1)

        self.button = QPushButton(action)
        self.button.setEnabled(enabled)
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.setStyleSheet(
            f"QPushButton {{ background: #20242e; color: {TEXT};"
            f" border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 10px; }}"
            f"QPushButton:enabled:hover {{ border-color: {ACCENT}; }}"
            "QPushButton:disabled { color: #5b6374; }"
        )
        if tooltip:
            self.button.setToolTip(tooltip)
        if action == "Seleccionar":
            self.button.clicked.connect(lambda: self.selectRequested.emit(name))
        else:
            self.button.clicked.connect(lambda: self.pullRequested.emit(name))
        layout.addWidget(self.button)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.progress.setFixedWidth(140)
        layout.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setStyleSheet(f"color: {MUTED}; font-size: 10px; border: none;")
        layout.addWidget(self.status)

    def start_progress(self) -> None:
        self.button.setVisible(False)
        self.progress.setVisible(True)
        self.status.setText("descargando…")

    def set_progress(self, status: str, fraction: float) -> None:
        self.progress.setValue(int(fraction * 100))
        self.status.setText(status[:40])

    def mark_installed(self) -> None:
        self.progress.setVisible(False)
        self.button.setVisible(True)
        self.button.setText("Seleccionar")
        self.button.setEnabled(True)
        self.status.setText("instalado ✓")
        try:
            self.button.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.button.clicked.connect(lambda: self.selectRequested.emit(self.name))


# --------------------------------------------------------------------- #
# Selector de modelos
# --------------------------------------------------------------------- #
class ModelSelectorDialog(QDialog):
    """Ventana modal de selección de modelos locales (Ollama)."""

    modelSelected = Signal(str)

    def __init__(
        self, ollama: OllamaClient, current_model: str, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modelos de IA — Extdator")
        self.setMinimumSize(560, 560)
        self.setStyleSheet(
            f"QDialog {{ background: #12141a; color: {TEXT}; }}"
            f"QLabel {{ background: transparent; }}"
            f"QLineEdit {{ background: #0e1016; color: {TEXT};"
            f" border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 8px; }}"
            f"QComboBox {{ background: #20242e; color: {TEXT};"
            f" border: 1px solid {BORDER}; border-radius: 6px; padding: 4px; }}"
            f"QScrollBar:vertical {{ width: 10px; }}"
            f"QScrollBar::handle:vertical {{ background: #2a3140; border-radius: 5px; }}"
        )
        self._ollama = ollama
        self._current = current_model
        self._rows: Dict[str, ModelRow] = {}
        self._installed_data: List[dict] = []
        self._catalog_data: List[dict] = []
        self._pull_workers: List[PullWorker] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QLabel("Elige el modelo que responderá tus consultas")
        header.setStyleSheet(f"color: {TEXT}; font-size: 13px; font-weight: 700;")
        sub = QLabel(
            "La clasificación compara los requisitos estimados del modelo con "
            "la memoria disponible de tu equipo. Los modelos 🔴 están "
            "deshabilitados por seguridad."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        layout.addWidget(header)
        layout.addWidget(sub)

        filters = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Buscar modelo por nombre…")
        self._search.textChanged.connect(self._apply_filters)
        self._filter_combo = QComboBox()
        self._filter_combo.addItems(
            [
                "Todos",
                "🟢 PERFECTO",
                "🟡 REGULAR",
                "🟠 ALTO CONSUMO",
                "🔴 IMPOSIBLE",
                "Con visión",
            ]
        )
        self._filter_combo.currentIndexChanged.connect(self._apply_filters)
        filters.addWidget(self._search, 1)
        filters.addWidget(self._filter_combo)
        layout.addLayout(filters)

        self._status = QLabel("Detectando hardware y catálogo de Ollama…")
        self._status.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        layout.addWidget(self._status)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._container = QWidget()
        self._vbox = QVBoxLayout(self._container)
        self._vbox.setSpacing(6)
        self._vbox.addStretch(1)
        self._scroll.setWidget(self._container)
        layout.addWidget(self._scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._hardware_worker = HardwareWorker(ollama, configured_model=current_model)
        self._hardware_worker.ready.connect(self._populate)
        self._hardware_worker.failed.connect(self._on_load_failed)
        self._hardware_worker.start()

    # ------------------------------------------------------------------ #
    def _populate(self, data: dict) -> None:
        self._installed_data = data.get("installed", [])
        self._catalog_data = data.get("catalog", [])
        hw = data.get("hardware", {})
        self._status.setText(self._hw_summary(hw))
        self._build_rows()

    def _on_load_failed(self, message: str) -> None:
        self._status.setText(f"⚠ {message}")

    @staticmethod
    def _hw_summary(hw: dict) -> str:
        parts = [
            f"CPU: {hw.get('cpu_name', '?')} · "
            f"RAM: {hw.get('ram_total_mb', 0) // 1024} GB "
            f"({hw.get('ram_available_mb', 0) // 1024} GB libres)"
        ]
        gpus = hw.get("gpus") or []
        if gpus:
            gpu = gpus[0]
            vram = gpu.get("vram_total_mb")
            vram_text = f"{vram // 1024} GB" if vram else "?"
            parts.append(f"GPU: {gpu['name']} ({vram_text})")
        else:
            parts.append("GPU: no detectada (modo CPU)")
        if hw.get("ollama_gpu_active") is False:
            parts.append("· Ollama está usando CPU")
        elif hw.get("ollama_gpu_active") is True:
            parts.append("· aceleración GPU activa ✓")
        for note in (hw.get("notes") or [])[:2]:
            parts.append(f"· {note}")
        return "  |  ".join(parts)

    # ------------------------------------------------------------------ #
    def _build_rows(self) -> None:
        for row in list(self._rows.values()):
            row.deleteLater()
        self._rows.clear()
        self._clear_layout()

        self._add_section("Instalados en Ollama")
        for entry in sorted(
            self._installed_data,
            key=lambda e: TIER_ORDER.get(e["assessment"]["tier"], 9),
        ):
            self._add_installed_row(entry)
        self._add_section("Recomendados para descargar")
        if not self._catalog_data:
            empty = QLabel("Todo el catálogo recomendado ya está instalado.")
            empty.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
            self._vbox.insertWidget(self._vbox.count() - 1, empty)
        else:
            for entry in sorted(
                self._catalog_data,
                key=lambda e: TIER_ORDER.get(e["assessment"]["tier"], 9),
            ):
                self._add_catalog_row(entry)
        self._apply_filters()

    def _clear_layout(self) -> None:
        while self._vbox.count() > 1:
            item = self._vbox.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _add_section(self, title: str) -> None:
        label = QLabel(title)
        label.setStyleSheet(
            f"color: {ACCENT}; font-size: 11px; font-weight: 700;"
            "letter-spacing: 1px; border: none; padding-top: 4px;"
        )
        self._vbox.insertWidget(self._vbox.count() - 1, label)

    def _add_installed_row(self, entry: dict) -> None:
        assessment = entry["assessment"]
        caps = entry.get("capabilities") or []
        extras = []
        if "vision" in caps:
            extras.append("visión")
        if entry.get("quantization"):
            extras.append(entry["quantization"])
        meta = (
            f"{_fmt_gb(entry.get('size_bytes'))}"
            + (f" · {entry['parameter_size']}" if entry.get("parameter_size") else "")
            + (f" · {' · '.join(extras)}" if extras else "")
        )
        explanation = " — ".join(assessment["reasons"][:2]) or assessment["explanation"]
        active = entry["name"] == self._current
        row = ModelRow(
            name=entry["name"],
            meta=meta,
            explanation=explanation,
            tier=assessment["tier"],
            color=assessment["color"],
            emoji=assessment["emoji"],
            action="En uso" if active else "Seleccionar",
            enabled=assessment["tier"] != "imposible" and not active,
            tooltip=(
                "Este modelo no puede ejecutarse en tu equipo por " "seguridad."
                if assessment["tier"] == "imposible"
                else ""
            ),
        )
        row.selectRequested.connect(self._select)
        self._rows[entry["name"]] = row
        self._vbox.insertWidget(self._vbox.count() - 1, row)

    def _add_catalog_row(self, entry: dict) -> None:
        assessment = entry["assessment"]
        extras = []
        if entry.get("vision"):
            extras.append("visión")
        if entry.get("thinking"):
            extras.append("razonamiento")
        meta = (
            f"~{entry['size_gb']:.1f} GB · {entry['params_b']}B"
            + (f" · {' · '.join(extras)}" if extras else "")
            + f" · {entry['family']}"
        )
        impossible = assessment["tier"] == "imposible"
        explanation = f"{entry['description']} — " + (
            " — ".join(assessment["reasons"][:1]) or assessment["explanation"]
        )
        row = ModelRow(
            name=entry["name"],
            meta=meta,
            explanation=explanation,
            tier=assessment["tier"],
            color=assessment["color"],
            emoji=assessment["emoji"],
            action=("Descargar" if not impossible else "No compatible"),
            enabled=not impossible,
            tooltip=(
                f"Requiere ~{assessment['metrics'].get('disk_needed_gb', '?')} GB "
                "de disco y memoria suficiente."
                if not impossible
                else "Este modelo no cabe razonablemente en tu equipo; la "
                "descarga está deshabilitada."
            ),
        )
        row.pullRequested.connect(self._pull)
        row.selectRequested.connect(self._select)
        self._rows[entry["name"]] = row
        self._vbox.insertWidget(self._vbox.count() - 1, row)

    # ------------------------------------------------------------------ #
    def _apply_filters(self) -> None:
        text = self._search.text().strip().lower()
        index = self._filter_combo.currentIndex()
        tier_filter = {1: "perfecto", 2: "regular", 3: "alto", 4: "imposible"}.get(
            index
        )
        vision_only = index == 5
        for name, row in self._rows.items():
            entry = self._find_entry(name)
            visible = True
            if text and text not in name.lower():
                visible = False
            if tier_filter and entry and entry["assessment"]["tier"] != tier_filter:
                visible = False
            if vision_only:
                caps = (entry or {}).get("capabilities") or (entry or {}).get(
                    "vision", False
                )
                ok = ("vision" in caps) if isinstance(caps, list) else bool(caps)
                if not ok:
                    visible = False
            row.setVisible(visible)

    def _find_entry(self, name: str) -> Optional[dict]:
        for entry in self._installed_data:
            if entry["name"] == name:
                return entry
        for entry in self._catalog_data:
            if entry["name"] == name:
                return entry
        return None

    # ------------------------------------------------------------------ #
    def _select(self, name: str) -> None:
        self.modelSelected.emit(name)
        self.accept()

    def _pull(self, name: str) -> None:
        from PySide6.QtWidgets import QMessageBox

        row = self._rows.get(name)
        if row is None:
            return
        entry = self._find_entry(name) or {}
        size_gb = entry.get("size_gb") or entry.get("size_bytes", 0) / (1024**3)
        confirm_label = (
            f"Descargar {name} (~{size_gb:.1f} GB)?"
            if size_gb
            else f"Descargar {name}?"
        )
        answer = QMessageBox.question(
            self,
            "Descargar modelo",
            confirm_label + "\n\nNo se descarga nada sin tu confirmación.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        worker = PullWorker(self._ollama, name, parent=self)
        worker.progress.connect(row.set_progress)
        worker.succeeded.connect(lambda _n: self._on_pull_done(name))
        worker.failed.connect(self._on_pull_failed)
        self._pull_workers.append(worker)
        # start_progress() oculta el botón: no hay doble descarga posible.
        row.start_progress()
        worker.start()

    def _on_pull_done(self, name: str) -> None:
        row = self._rows.get(name)
        if row is not None:
            row.mark_installed()
        self._status.setText(f"'{name}' instalado. Ya puedes seleccionarlo.")

    def _on_pull_failed(self, name: str, message: str) -> None:
        row = self._rows.get(name)
        if row is not None:
            row.progress.setVisible(False)
            row.button.setVisible(True)
            row.status.setText("error")
        self._status.setText(f"⚠ {name}: {message}")

    def closeEvent(self, event) -> None:  # noqa: N802 (PySide)
        for worker in self._pull_workers:
            if worker.isRunning():
                worker.stop()
                worker.wait(2000)
        if self._hardware_worker.isRunning():
            self._hardware_worker.wait(3000)
        super().closeEvent(event)


# --------------------------------------------------------------------- #
# Ajustes
# --------------------------------------------------------------------- #
class SettingsDialog(QDialog):
    """Centro de configuración: todo lo habitual, sin editar archivos."""

    settingsChanged = Signal(dict)  # cambios validados → los aplica el panel

    def __init__(self, cfg: Config, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ajustes — Extdator")
        self.setMinimumSize(520, 500)
        self.setStyleSheet(
            f"QDialog {{ background: #12141a; color: {TEXT}; }}"
            f"QLabel {{ color: {TEXT}; background: transparent; }}"
            f"QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox "
            f"{{ background: #0e1016; color: {TEXT}; border: 1px solid {BORDER};"
            " border-radius: 6px; padding: 4px 6px; }"
            f"QPushButton {{ background: #20242e; color: {TEXT};"
            f" border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 12px; }}"
            f"QTabWidget::pane {{ border: 1px solid {BORDER}; }}"
            f"QTabBar::tab {{ background: #171a21; color: {MUTED}; padding: 6px 14px; }}"
            f"QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}"
            f"QToolTip {{ background: #171a21; color: {TEXT};"
            f" border: 1px solid {BORDER}; }}"
        )
        self._cfg = cfg
        self._widgets: Dict[str, QWidget] = {}

        tabs = QTabWidget()
        tabs.addTab(self._tab_modelo(), "Modelo")
        tabs.addTab(self._tab_ventana(), "Ventana")
        tabs.addTab(self._tab_ollama(), "Ollama")
        tabs.addTab(self._tab_avanzado(), "Avanzado")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.addWidget(tabs, 1)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ #
    def _spin(
        self,
        key: str,
        min_v: float,
        max_v: float,
        step: float = 1,
        decimals: int = 0,
        suffix: str = "",
    ) -> QWidget:
        spec = ALLOWED_KEYS.get(key, {})
        if decimals:
            widget = QDoubleSpinBox()
            widget.setDecimals(decimals)
            widget.setSingleStep(step)
        else:
            widget = QSpinBox()
            widget.setSingleStep(int(step))
        widget.setRange(min_v, max_v)
        widget.setSuffix(suffix)
        if spec.get("restart"):
            widget.setToolTip("Requiere reiniciar Extdator para aplicarse.")
        self._widgets[key] = widget
        return widget

    def _check(self, key: str, label: str) -> QCheckBox:
        widget = QCheckBox(label)
        if ALLOWED_KEYS.get(key, {}).get("restart"):
            widget.setToolTip("Requiere reiniciar Extdator para aplicarse.")
        self._widgets[key] = widget
        return widget

    def _text(self, key: str, placeholder: str = "") -> QWidget:
        widget = QLineEdit()
        widget.setPlaceholderText(placeholder)
        self._widgets[key] = widget
        return widget

    def _tab_modelo(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        form.setSpacing(8)

        model_combo = QComboBox()
        model_combo.setEditable(True)
        model_combo.addItem(self._cfg.ollama_model)
        self._widgets["ollama_model"] = model_combo
        form.addRow("Modelo activo", model_combo)
        hint = QLabel("Los modelos se eligen con el botón 🤖 de la barra de título.")
        hint.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        form.addRow("", hint)

        temp = self._spin("ollama_temperature", 0.0, 2.0, 0.05, 2)
        form.addRow("Temperatura (0.0–2.0)", temp)

        think = self._check(
            "ollama_think", "Razonar antes de responder (modelos qwen3/deepseek-r1)"
        )
        form.addRow("", think)

        extra = self._cfg.ollama_extra_options or {}
        num_ctx = self._spin("_num_ctx", 512, 131072, 512, suffix=" tokens")
        num_ctx.setValue(int(extra.get("num_ctx", 8192)))
        form.addRow("Contexto (num_ctx)", num_ctx)
        num_predict = self._spin("_num_predict", 128, 16384, 128, suffix=" tokens")
        num_predict.setValue(int(extra.get("num_predict", 2048)))
        form.addRow("Máx. respuesta (num_predict)", num_predict)

        history = self._spin("max_history_messages", 2, 200)
        form.addRow("Mensajes en historial", history)

        prompt = QPlainTextEdit()
        prompt.setPlainText(self._cfg.ollama_system_prompt)
        prompt.setMinimumHeight(120)
        prompt.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")
        self._widgets["ollama_system_prompt"] = prompt
        form.addRow("Prompt de sistema", prompt)
        return tab

    def _tab_ventana(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        opacity = self._spin("window_opacity", 40, 100, 5, suffix=" %")
        opacity.setValue(self._cfg.window_opacity)
        form.addRow("Opacidad", opacity)
        form.addRow("", self._check("always_on_top", "Siempre visible (encima)"))
        form.addRow(
            "", self._check("hover_scroll_hook", "Hover Scroll (rueda sin hacer clic)")
        )
        return tab

    def _tab_ollama(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        host = self._text("ollama_host", "http://127.0.0.1:11434")
        host.setText(self._cfg.ollama_host)
        form.addRow("Dirección de Ollama", host)
        timeout = self._spin("ollama_timeout", 10, 7200, 10, suffix=" s")
        timeout.setValue(int(self._cfg.ollama_timeout))
        form.addRow("Timeout de generación", timeout)
        keep = self._text("ollama_keep_alive", "5m / 1h / -1")
        keep.setText(self._cfg.ollama_keep_alive)
        form.addRow("Retención del modelo en memoria", keep)
        return tab

    def _tab_avanzado(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        level = QComboBox()
        level.addItems(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        level.setCurrentText(self._cfg.log_level)
        self._widgets["log_level"] = level
        form.addRow("Nivel de log", level)
        form.addRow(
            "", self._check("log_privacy", "Privacidad: no registrar contenido")
        )
        rate = self._spin("rate_limit_per_minute", 1, 600)
        rate.setValue(self._cfg.rate_limit_per_minute)
        form.addRow("Límite de solicitudes/minuto", rate)
        maxlen = self._spin("max_text_length", 64, 200_000, 1000)
        maxlen.setValue(self._cfg.max_text_length)
        form.addRow("Límite de texto seleccionado", maxlen)

        reset = QPushButton("Restaurar valores predeterminados")
        reset.clicked.connect(self._reset)
        form.addRow("", reset)
        return tab

    # ------------------------------------------------------------------ #
    def _save(self) -> None:
        from PySide6.QtWidgets import (
            QComboBox,
            QDoubleSpinBox,
            QLineEdit,
            QPlainTextEdit,
            QSpinBox,
        )

        changes: Dict[str, Any] = {}
        extra = dict(self._cfg.ollama_extra_options or {})
        for key, widget in self._widgets.items():
            if key == "_num_ctx":
                extra["num_ctx"] = int(widget.value())
                continue
            if key == "_num_predict":
                extra["num_predict"] = int(widget.value())
                continue
            if isinstance(widget, QCheckBox):
                changes[key] = widget.isChecked()
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                spec = ALLOWED_KEYS.get(key, {})
                value = widget.value()
                if spec.get("type") is int:
                    value = int(value)
                changes[key] = value
            elif isinstance(widget, QPlainTextEdit):
                changes[key] = widget.toPlainText().strip()
            elif isinstance(widget, QLineEdit):
                changes[key] = widget.text().strip()
            elif isinstance(widget, QComboBox):
                changes[key] = widget.currentText().strip()
        if extra:
            changes["ollama_extra_options"] = extra
        self.settingsChanged.emit(changes)
        self.accept()

    def _reset(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        answer = QMessageBox.question(
            self,
            "Restaurar ajustes",
            "Se borrarán tus ajustes guardados y volverán los valores del "
            "archivo .env.\n\n¿Continuar? (requiere reiniciar Extdator)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.settingsChanged.emit({"__reset__": True})
            self.reject()


# --------------------------------------------------------------------- #
# Emparejamiento
# --------------------------------------------------------------------- #
class PairingDialog(QDialog):
    """Muestra el código efímero para vincular la extensión."""

    def __init__(self, pairing, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._pairing = pairing
        self.setWindowTitle("Emparejar extensión — Extdator")
        self.setMinimumWidth(380)
        self.setStyleSheet(
            f"QDialog {{ background: #12141a; color: {TEXT}; }}"
            f"QLabel {{ color: {TEXT}; background: transparent; }}"
        )
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        intro = QLabel(
            "En la página de opciones de la extensión, pulsa «Conectar con la "
            "aplicación de escritorio» e introduce este código:"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._code = QLabel("")
        self._code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._code.setStyleSheet(
            f"color: {ACCENT}; font-size: 34px; font-weight: 700;"
            "letter-spacing: 8px;"
        )
        layout.addWidget(self._code)

        self._timer_label = QLabel("")
        self._timer_label.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        self._timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._timer_label)

        refresh = QPushButton("Generar código nuevo")
        refresh.clicked.connect(self._refresh)
        layout.addWidget(refresh)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._refresh()
        self._timer.start()

    def _refresh(self) -> None:
        self._code.setText(self._pairing.current_code())
        self._tick()

    def _tick(self) -> None:
        left = self._pairing.seconds_left()
        self._timer_label.setText(
            f"Válido durante {left // 60}:{left % 60:02d} · un solo uso"
        )
