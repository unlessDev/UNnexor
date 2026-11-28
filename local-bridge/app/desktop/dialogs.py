"""Diálogos y paneles de configuración del panel principal.

* ``ModelsPanel`` / ``ModelSelectorDialog`` — catálogo de modelos: modelo en
  uso, instalados, recomendados y biblioteca en línea, con clasificación de
  consumo (🟢🟡🟠🔴), rendimiento esperado y acciones siempre visibles.
  Las descargas van al ``DownloadManager`` común: continúan aunque se
  cierre la ventana y muestran progreso real.
* ``SettingsDialog`` — configuración centralizada sin editar archivos.
* ``PairingDialog`` — código efímero para vincular la extensión.

Estética: ``ui_kit.DIALOG_QSS`` (común a todas las ventanas).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from ..ollama_client import OllamaClient
from ..ollama_pull import CANCELLED, COMPLETED, FAILED, PullSnapshot, format_bytes
from ..settings_store import ALLOWED_KEYS
from .downloads import KIND_OLLAMA, DownloadJob, DownloadManager
from .styles import ACCENT, BORDER, MUTED, PANEL, TEXT
from .ui_kit import (
    DIALOG_QSS,
    DownloadProgress,
    TierChip,
    card,
    fit_to_screen,
    section,
    wrap_label,
)
from .workers import HardwareWorker

TIER_ORDER = {"perfecto": 0, "regular": 1, "alto": 2, "imposible": 3}
ACTION_COLUMN_WIDTH = 200

FILTERS = [
    ("Todos los modelos", None),
    ("Solo instalados", "installed"),
    ("Solo para descargar", "download"),
    ("🟢 Óptimos para tu equipo", "perfecto"),
    ("🟡 Compatibles", "regular"),
    ("🟠 Exigentes", "alto"),
    ("🔴 No compatibles", "imposible"),
    ("Con visión (imágenes)", "vision"),
]


def _fmt_gb(size_bytes: Optional[int]) -> str:
    if not size_bytes:
        return "tamaño desconocido"
    return f"{size_bytes / (1024 ** 3):.1f} GB"


def performance_hint(assessment: dict) -> str:
    """Rendimiento esperado a partir de la evaluación del catálogo."""
    metrics = assessment.get("metrics") or {}
    tps = metrics.get("expected_tokens_per_s") or metrics.get("tokens_per_s")
    tier = assessment.get("tier")
    base = {
        "perfecto": "Rendimiento esperado: fluido",
        "regular": "Rendimiento esperado: aceptable",
        "alto": "Rendimiento esperado: lento, exige mucha memoria",
        "imposible": "No se ejecutaría de forma estable en este equipo",
    }.get(str(tier), "Rendimiento sin evaluar")
    if tps:
        base += f" (~{float(tps):.0f} tokens/s)"
    return base


# --------------------------------------------------------------------- #
# Fila de modelo
# --------------------------------------------------------------------- #
class ModelRow(QFrame):
    """Tarjeta de un modelo: clasificación, datos, explicación y acción.

    La columna de acciones tiene ancho fijo: el botón Descargar/Seleccionar
    nunca queda fuera de la vista aunque la descripción sea larga.
    """

    selectRequested = Signal(str)
    pullRequested = Signal(str)
    cancelRequested = Signal(str)

    def __init__(
        self,
        name: str,
        meta: str,
        explanation: str,
        tier: str,
        color: str = "",
        emoji: str = "",
        action: str = "Seleccionar",
        enabled: bool = True,
        parent: Optional[QWidget] = None,
        tooltip: str = "",
        *,
        performance: str = "",
        active: bool = False,
        installed: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("modelRow")
        self.name = name
        self.tier = tier
        self.installed = installed
        border = ACCENT if active else BORDER
        self.setStyleSheet(
            f"QFrame#modelRow {{ background: {PANEL}; border: 1px solid {border};"
            " border-radius: 10px; }"
        )
        grid = QGridLayout(self)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title = QLabel(name)
        title.setStyleSheet(f"color: {TEXT}; font-weight: 700; font-size: 14px;")
        title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        title_row.addWidget(title)
        self.chip = TierChip(tier)
        title_row.addWidget(self.chip)
        if active:
            in_use = QLabel("● EN USO")
            in_use.setStyleSheet(
                f"color: {ACCENT}; font-weight: 800; font-size: 11px;"
            )
            title_row.addWidget(in_use)
        title_row.addStretch(1)
        grid.addLayout(title_row, 0, 0)

        meta_label = wrap_label(meta, "muted")
        grid.addWidget(meta_label, 1, 0)
        if performance:
            grid.addWidget(wrap_label(performance, "muted"), 2, 0)
        explanation_label = wrap_label(explanation)
        explanation_label.setStyleSheet("color: #b9bfcc; font-size: 12px;")
        grid.addWidget(explanation_label, 3, 0)
        grid.setColumnStretch(0, 1)

        actions = QVBoxLayout()
        actions.setSpacing(6)
        self.button = QPushButton(action)
        self.button.setEnabled(enabled)
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        if action in ("Descargar", "Seleccionar"):
            self.button.setProperty("kind", "primary")
        if tooltip:
            self.button.setToolTip(tooltip)
        self.button.clicked.connect(self._on_button)
        self._action = action
        self.cancel_button = QPushButton("Cancelar descarga")
        self.cancel_button.setProperty("kind", "danger")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(lambda: self.cancelRequested.emit(self.name))
        self.progress_view = DownloadProgress()
        self.progress_view.setVisible(False)
        # Compatibilidad con código existente (row.progress.setVisible…).
        self.progress = self.progress_view.bar
        self.status = wrap_label("", "muted")
        for widget in (self.button, self.cancel_button, self.progress_view, self.status):
            actions.addWidget(widget)
        actions.addStretch(1)
        holder = QWidget()
        holder.setLayout(actions)
        holder.setFixedWidth(ACTION_COLUMN_WIDTH)
        grid.addWidget(holder, 0, 1, 4, 1, Qt.AlignmentFlag.AlignTop)

    def _on_button(self) -> None:
        if self._action == "Seleccionar":
            self.selectRequested.emit(self.name)
        else:
            self.pullRequested.emit(self.name)

    def start_progress(self) -> None:
        self.button.setVisible(False)
        self.cancel_button.setVisible(True)
        self.progress_view.setVisible(True)
        self.status.setText("")

    def apply_snapshot(self, snapshot: PullSnapshot) -> None:
        if snapshot.phase in (COMPLETED,):
            self.mark_installed()
            return
        if snapshot.phase in (FAILED, CANCELLED):
            self.progress_view.setVisible(False)
            self.cancel_button.setVisible(False)
            self.button.setVisible(True)
            self.button.setText("Reintentar descarga")
            self.status.setText(snapshot.describe())
            self.status.setProperty("role", "error" if snapshot.phase == FAILED else "muted")
            return
        if not self.progress_view.isVisible():
            self.start_progress()
        self.progress_view.apply(snapshot)

    def set_progress(self, status: str, fraction: float) -> None:
        """API anterior (estado + fracción); se conserva por compatibilidad."""
        self.progress.setRange(0, 100)
        self.progress.setValue(int(fraction * 100))
        self.status.setText(status[:60])

    def mark_installed(self) -> None:
        self.installed = True
        self.progress_view.setVisible(False)
        self.cancel_button.setVisible(False)
        self.button.setVisible(True)
        self.button.setText("Seleccionar")
        self.button.setEnabled(True)
        self._action = "Seleccionar"
        self.status.setText("Instalado y verificado ✓")


# --------------------------------------------------------------------- #
# Panel de modelos
# --------------------------------------------------------------------- #
class ModelsPanel(QWidget):
    """Catálogo de modelos reutilizable (diálogo o página del centro de control)."""

    modelSelected = Signal(str)

    def __init__(
        self,
        ollama: OllamaClient,
        current_model: str,
        parent: Optional[QWidget] = None,
        *,
        downloads: Optional[DownloadManager] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("pageRoot")
        self._ollama = ollama
        self._current = current_model
        self._rows: Dict[str, ModelRow] = {}
        self._installed_data: List[dict] = []
        self._catalog_data: List[dict] = []
        self._downloads = downloads
        if self._downloads is None and ollama is not None:
            self._downloads = DownloadManager.instance(ollama)
        if self._downloads is not None:
            self._downloads.jobProgress.connect(self._on_job_snapshot)
            self._downloads.jobFinished.connect(self._on_job_finished)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(10)

        title = QLabel("Modelos de IA")
        title.setProperty("role", "title")
        layout.addWidget(title)
        layout.addWidget(
            wrap_label(
                "Elige el modelo que responde tus consultas o descarga uno nuevo. "
                "La clasificación compara lo que pide cada modelo con la memoria "
                "real de tu equipo. No se descarga nada sin tu confirmación.",
                "muted",
            )
        )

        current = card()
        current_layout = QHBoxLayout(current)
        current_layout.setContentsMargins(14, 10, 14, 10)
        self._current_label = wrap_label("")
        self._current_label.setStyleSheet("font-size: 14px;")
        current_layout.addWidget(self._current_label, 1)
        self._current_chip = TierChip("sin_evaluar")
        current_layout.addWidget(self._current_chip)
        layout.addWidget(current)
        self._set_current_label(current_model, None)

        self._status = wrap_label("Detectando hardware y catálogo de Ollama…", "muted")
        layout.addWidget(self._status)

        filters = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Buscar modelo por nombre…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filters)
        self._filter_combo = QComboBox()
        for label, _key in FILTERS:
            self._filter_combo.addItem(label)
        self._filter_combo.currentIndexChanged.connect(self._apply_filters)
        self._refresh_btn = QPushButton("Actualizar catálogo")
        self._refresh_btn.setToolTip(
            "Consulta ollama.com/library y la última versión de Ollama. "
            "No descarga ningún modelo."
        )
        self._refresh_btn.clicked.connect(self._refresh_library)
        filters.addWidget(self._search, 1)
        filters.addWidget(self._filter_combo)
        filters.addWidget(self._refresh_btn)
        layout.addLayout(filters)
        self._library = wrap_label("", "muted")
        layout.addWidget(self._library)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._container = QWidget()
        self._vbox = QVBoxLayout(self._container)
        self._vbox.setContentsMargins(0, 0, 6, 0)
        self._vbox.setSpacing(8)
        self._vbox.addStretch(1)
        self._scroll.setWidget(self._container)
        layout.addWidget(self._scroll, 1)

        self._legend = wrap_label(
            "🟢 ÓPTIMO: cabe con margen · 🟡 COMPATIBLE: funciona con margen justo · "
            "🟠 EXIGENTE: lento o al límite de memoria · 🔴 NO COMPATIBLE: "
            "descarga deshabilitada. Las descargas continúan aunque cierres esta ventana.",
            "muted",
        )
        layout.addWidget(self._legend)

        self._hardware_worker = HardwareWorker(ollama, configured_model=current_model)
        self._hardware_worker.ready.connect(self._populate)
        self._hardware_worker.failed.connect(self._on_load_failed)
        self._hardware_worker.start()

    # ------------------------------------------------------------------ #
    def _set_current_label(self, name: str, entry: Optional[dict]) -> None:
        self._current_label.setText(
            f"<b>Modelo en uso:</b> {name or 'ninguno'}"
            + (" · instalado en Ollama" if entry else "")
        )
        if entry:
            self._current_chip.set_tier(entry["assessment"]["tier"])
            self._current_chip.setVisible(True)
        else:
            self._current_chip.setVisible(False)

    def _populate(self, data: dict) -> None:
        self._installed_data = data.get("installed", [])
        self._catalog_data = data.get("catalog", [])
        hw = data.get("hardware", {})
        self._status.setText(self._hw_summary(hw))
        self._library.setText(self._library_summary(data.get("library") or {}))
        current = next(
            (e for e in self._installed_data if e["name"] == self._current), None
        )
        self._set_current_label(self._current, current)
        self._build_rows()

    @staticmethod
    def _library_summary(info: dict) -> str:
        status = info.get("status", "unavailable")
        date = (info.get("fetched_at") or "")[:16].replace("T", " ")
        if status == "online":
            text = f"Catálogo de ollama.com actualizado ({date}, {info.get('models')} modelos"
            text += f", {info['new']} nuevos)." if info.get("new") else ")."
        elif status == "cache":
            text = f"Catálogo sin conexión: se muestra la copia del {date}."
        else:
            text = "Catálogo en línea no disponible: solo se muestran los recomendados."
        message = info.get("version_message", "")
        if info.get("version_state") == "update":
            message = "⬆ " + message
        return f"{text} {message}".strip()

    def _refresh_library(self) -> None:
        from .workers import CatalogRefreshWorker

        self._refresh_btn.setEnabled(False)
        self._library.setText("Consultando ollama.com…")
        worker = CatalogRefreshWorker(self)
        worker.finished_refresh.connect(self._after_refresh)
        self._catalog_worker = worker
        worker.start()

    def _after_refresh(self, message: str) -> None:
        self._refresh_btn.setEnabled(True)
        self._library.setText(message)
        self.reload()

    def reload(self) -> None:
        if self._hardware_worker.isRunning():
            return
        self._hardware_worker = HardwareWorker(
            self._ollama, configured_model=self._current
        )
        self._hardware_worker.ready.connect(self._populate)
        self._hardware_worker.failed.connect(self._on_load_failed)
        self._hardware_worker.start()

    def set_current_model(self, name: str) -> None:
        self._current = name
        self.reload()

    def _on_load_failed(self, message: str) -> None:
        self._status.setText(f"⚠ {message}")

    @staticmethod
    def _hw_summary(hw: dict) -> str:
        parts = [
            f"Tu equipo — CPU: {hw.get('cpu_name', '?')} · "
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
            parts.append("Ollama está usando la CPU")
        elif hw.get("ollama_gpu_active") is True:
            parts.append("aceleración por GPU activa ✓")
        for note in (hw.get("notes") or [])[:2]:
            parts.append(note)
        return " · ".join(parts)

    # ------------------------------------------------------------------ #
    def _build_rows(self) -> None:
        for row in list(self._rows.values()):
            row.deleteLater()
        self._rows.clear()
        self._clear_layout()

        self._add_section(f"Instalados en tu equipo ({len(self._installed_data)})")
        if not self._installed_data:
            self._add_note(
                "Todavía no hay modelos instalados en Ollama. Descarga uno de los "
                "recomendados o usa «Recomendar configuración»."
            )
        for entry in sorted(
            self._installed_data,
            key=lambda e: (
                e["name"] != self._current,
                TIER_ORDER.get(e["assessment"]["tier"], 9),
            ),
        ):
            self._add_installed_row(entry)
        curated = [e for e in self._catalog_data if not e.get("estimated")]
        online = [e for e in self._catalog_data if e.get("estimated")]
        self._add_section("Recomendados para descargar")
        if not curated:
            self._add_note("Todo el catálogo recomendado ya está instalado.")
        else:
            for entry in sorted(
                curated,
                key=lambda e: TIER_ORDER.get(e["assessment"]["tier"], 9),
            ):
                self._add_catalog_row(entry)
        if online:
            self._add_section("Biblioteca de Ollama (en línea · tamaños estimados)")
            for entry in sorted(
                online,
                key=lambda e: (
                    not e.get("new"),
                    TIER_ORDER.get(e["assessment"]["tier"], 9),
                ),
            ):
                self._add_catalog_row(entry)
        self._apply_filters()
        self._restore_download_states()

    def _clear_layout(self) -> None:
        while self._vbox.count() > 1:
            item = self._vbox.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

    def _add_section(self, title: str) -> None:
        self._vbox.insertWidget(self._vbox.count() - 1, section(title))

    def _add_note(self, text: str) -> None:
        self._vbox.insertWidget(self._vbox.count() - 1, wrap_label(text, "muted"))

    def _add_installed_row(self, entry: dict) -> None:
        assessment = entry["assessment"]
        caps = entry.get("capabilities") or []
        extras = []
        if "vision" in caps:
            extras.append("visión")
        if "thinking" in caps:
            extras.append("razonamiento")
        if entry.get("quantization"):
            extras.append(entry["quantization"])
        meta = (
            f"{_fmt_gb(entry.get('size_bytes'))}"
            + (f" · {entry['parameter_size']}" if entry.get("parameter_size") else "")
            + (f" · {' · '.join(extras)}" if extras else "")
        )
        explanation = " — ".join(assessment["reasons"][:2]) or assessment["explanation"]
        active = entry["name"] == self._current
        impossible = assessment["tier"] == "imposible"
        row = ModelRow(
            name=entry["name"],
            meta=meta,
            explanation=explanation,
            tier=assessment["tier"],
            color=assessment["color"],
            emoji=assessment["emoji"],
            action="En uso" if active else "Seleccionar",
            enabled=not impossible and not active,
            tooltip=(
                "Este modelo no puede ejecutarse en tu equipo por seguridad."
                if impossible
                else ""
            ),
            performance=performance_hint(assessment),
            active=active,
            installed=True,
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
        estimated = " (estimado)" if entry.get("estimated") else ""
        meta = (
            ("🆕 nuevo · " if entry.get("new") else "")
            + f"descarga ~{entry['size_gb']:.1f} GB{estimated} · {entry['params_b']:g}B"
            + (f" · {' · '.join(extras)}" if extras else "")
            + f" · {entry['family']}"
            + (f" · {entry['pulls']} descargas" if entry.get("pulls") else "")
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
            performance=performance_hint(assessment),
        )
        row.pullRequested.connect(self._pull)
        row.selectRequested.connect(self._select)
        row.cancelRequested.connect(self._cancel)
        self._rows[entry["name"]] = row
        self._vbox.insertWidget(self._vbox.count() - 1, row)

    # ------------------------------------------------------------------ #
    def _apply_filters(self) -> None:
        text = self._search.text().strip().lower()
        key = FILTERS[self._filter_combo.currentIndex()][1]
        for name, row in self._rows.items():
            entry = self._find_entry(name) or {}
            visible = not text or text in name.lower()
            if key == "installed":
                visible = visible and row.installed
            elif key == "download":
                visible = visible and not row.installed
            elif key == "vision":
                caps = entry.get("capabilities") or entry.get("vision", False)
                ok = ("vision" in caps) if isinstance(caps, list) else bool(caps)
                visible = visible and ok
            elif key and entry:
                visible = visible and entry["assessment"]["tier"] == key
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
        self._current = name
        self.modelSelected.emit(name)

    def _pull(self, name: str) -> None:
        row = self._rows.get(name)
        if row is None or self._downloads is None:
            return
        entry = self._find_entry(name) or {}
        size_gb = entry.get("size_gb") or (entry.get("size_bytes") or 0) / (1024**3)
        disk = (entry.get("assessment") or {}).get("metrics", {}).get("disk_needed_gb")
        text = (
            f"¿Descargar «{name}»"
            + (f" (~{size_gb:.1f} GB)" if size_gb else "")
            + "?\n\nSe descargará desde el repositorio oficial de Ollama"
            + (f" y ocupará unos {disk} GB de disco" if disk else "")
            + ". Puedes seguir usando UNnexor mientras tanto; la descarga continúa "
            "aunque cierres esta ventana."
        )
        answer = QMessageBox.question(
            self,
            "Descargar modelo",
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        job = DownloadJob(KIND_OLLAMA, name, expected_bytes=int(size_gb * 1024**3))
        if self._downloads.enqueue(job):
            row.start_progress()
            active = self._downloads.active_job()
            if active is not None and active.key != job.key:
                row.status.setText(f"En cola: espera a que termine {active.name}.")

    def _cancel(self, name: str) -> None:
        if self._downloads is not None:
            self._downloads.cancel(KIND_OLLAMA, name)

    def _restore_download_states(self) -> None:
        if self._downloads is None:
            return
        for name, row in self._rows.items():
            if self._downloads.is_busy(KIND_OLLAMA, name):
                row.start_progress()
                snapshot = self._downloads.last_snapshot(KIND_OLLAMA, name)
                if snapshot is not None:
                    row.apply_snapshot(snapshot)

    def _row_for(self, snapshot: PullSnapshot) -> Optional[ModelRow]:
        kind, _, name = snapshot.model.partition(":")
        if kind != KIND_OLLAMA:
            return None
        return self._rows.get(name)

    def _on_job_snapshot(self, snapshot: PullSnapshot) -> None:
        row = self._row_for(snapshot)
        if row is not None:
            row.apply_snapshot(snapshot)

    def _on_job_finished(self, snapshot: PullSnapshot) -> None:
        row = self._row_for(snapshot)
        name = snapshot.model.partition(":")[2]
        if row is not None:
            row.apply_snapshot(snapshot)
        if snapshot.phase == COMPLETED:
            self._status.setText(f"«{name}» instalado y verificado. Ya puedes seleccionarlo.")
        elif snapshot.phase == FAILED:
            self._status.setText(f"⚠ {name}: {snapshot.error}")

    def shutdown(self) -> None:
        if self._hardware_worker.isRunning():
            self._hardware_worker.wait(3000)
        if self._downloads is not None:
            for signal, slot in (
                (self._downloads.jobProgress, self._on_job_snapshot),
                (self._downloads.jobFinished, self._on_job_finished),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass


class ModelSelectorDialog(QDialog):
    """Ventana «Modelos de IA» (envoltorio de ``ModelsPanel``)."""

    modelSelected = Signal(str)

    def __init__(
        self,
        ollama: OllamaClient,
        current_model: str,
        parent: Optional[QWidget] = None,
        *,
        downloads: Optional[DownloadManager] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modelos de IA — UNnexor")
        self.setStyleSheet(DIALOG_QSS)
        fit_to_screen(self, QSize(1000, 760), QSize(720, 520), anchor=parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 10)
        self.panel = ModelsPanel(ollama, current_model, self, downloads=downloads)
        self.panel.modelSelected.connect(self._select)
        layout.addWidget(self.panel, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.setContentsMargins(20, 0, 20, 0)
        layout.addWidget(buttons)

    # Atajos usados por pruebas y scripts existentes.
    @property
    def _rows(self) -> Dict[str, ModelRow]:
        return self.panel._rows

    def _populate(self, data: dict) -> None:
        self.panel._populate(data)

    def _select(self, name: str) -> None:
        self.modelSelected.emit(name)
        self.accept()

    def done(self, result: int) -> None:
        self.panel.shutdown()
        super().done(result)

    def closeEvent(self, event) -> None:
        self.panel.shutdown()
        super().closeEvent(event)


# --------------------------------------------------------------------- #
# Ajustes
# --------------------------------------------------------------------- #
class SettingsDialog(QDialog):
    """Centro de configuración: todo lo habitual, sin editar archivos."""

    settingsChanged = Signal(dict)  # cambios validados → los aplica el panel

    def __init__(self, cfg: Config, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ajustes — UNnexor")
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
    ) -> QSpinBox | QDoubleSpinBox:
        spec = ALLOWED_KEYS.get(key, {})
        widget: QSpinBox | QDoubleSpinBox
        if decimals:
            double = QDoubleSpinBox()
            double.setDecimals(decimals)
            double.setSingleStep(step)
            double.setRange(min_v, max_v)
            widget = double
        else:
            integer = QSpinBox()
            integer.setSingleStep(int(step))
            integer.setRange(int(min_v), int(max_v))
            widget = integer
        widget.setSuffix(suffix)
        if spec.get("restart"):
            widget.setToolTip("Requiere reiniciar UNnexor para aplicarse.")
        self._widgets[key] = widget
        return widget

    def _check(self, key: str, label: str) -> QCheckBox:
        widget = QCheckBox(label)
        # Sin esto las casillas se mostraban desmarcadas y al guardar se
        # desactivaban opciones activas (p. ej. "Siempre visible").
        widget.setChecked(bool(getattr(self._cfg, key, False)))
        if ALLOWED_KEYS.get(key, {}).get("restart"):
            widget.setToolTip("Requiere reiniciar UNnexor para aplicarse.")
        self._widgets[key] = widget
        return widget

    def _text(self, key: str, placeholder: str = "") -> QLineEdit:
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
        autostart = self._check(
            "ollama_autostart", "Iniciar Ollama al abrir UNnexor si no está abierto"
        )
        autostart.setToolTip(
            "Si Ollama ya está abierto no se toca. Si lo inicia UNnexor, se "
            "detiene al cerrar UNnexor. Requiere reiniciar UNnexor."
        )
        form.addRow("", autostart)
        online = self._check(
            "online_catalog",
            "Consultar el catálogo de ollama.com y la versión de Ollama al iniciar",
        )
        online.setToolTip(
            "Solo lectura: lista modelos nuevos y avisa si hay una versión más "
            "reciente de Ollama. Nunca descarga ni instala nada por sí solo."
        )
        form.addRow("", online)
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
            if key in ("_num_ctx", "_num_predict") and isinstance(
                widget, (QSpinBox, QDoubleSpinBox)
            ):
                extra[key[1:]] = int(widget.value())
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
            "archivo .env.\n\n¿Continuar? (requiere reiniciar UNnexor)",
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
        self.setWindowTitle("Emparejar extensión — UNnexor")
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
