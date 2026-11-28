"""Centro de control de UNnexor: un único lugar para configurar y operar.

Antes, cada tarea abría su propia ventana desde un menú (modelos, ajustes,
recomendación, emparejamiento, diagnóstico…) y el usuario tenía que
descubrir dónde estaba cada cosa. El centro de control reúne:

* **Inicio** — estado real (conexión con Ollama, modelo y proveedor
  activos, descargas en curso), guía de uso en tres pasos y las acciones
  frecuentes a un clic.
* **Modelos de IA** — el catálogo completo integrado (``ModelsPanel``), con
  descargas que continúan en segundo plano.
* Las tareas puntuales (recomendar, ajustes, proveedores, emparejar,
  diagnóstico, copiloto) se abren como diálogos contextuales desde aquí.

El panel flotante sigue siendo la superficie de respuestas (no roba el
foco del navegador); el centro de control es la superficie de gestión.
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QSize
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..ollama_pull import PullSnapshot
from . import icons
from .brand import LogoLabel
from .dialogs import ModelsPanel
from .downloads import DownloadManager
from .ui_kit import (
    DIALOG_QSS,
    DownloadProgress,
    card,
    fit_to_screen,
    scrollable,
    section,
    wrap_label,
)

PAGES = [("home", "Inicio", "home"), ("models", "Modelos de IA", "models")]


class HomePage(QWidget):
    def __init__(self, center: "ControlCenter") -> None:
        super().__init__()
        self._center = center
        window = center.bridge
        content = QWidget()
        content.setObjectName("pageRoot")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        head = QHBoxLayout()
        head.addWidget(LogoLabel(26))
        title = QLabel("UNnexor")
        title.setProperty("role", "title")
        head.addWidget(title)
        head.addStretch(1)
        layout.addLayout(head)

        layout.addWidget(section("Estado"))
        status_card = card()
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(16, 12, 16, 12)
        self.connection = wrap_label("Comprobando la conexión con Ollama…")
        self.model = wrap_label("")
        self.downloads = wrap_label("Sin descargas en curso.", "muted")
        self.download_view = DownloadProgress()
        self.download_view.setVisible(False)
        for widget in (self.connection, self.model, self.downloads, self.download_view):
            status_layout.addWidget(widget)
        layout.addWidget(status_card)

        layout.addWidget(section("Cómo se usa"))
        guide = card()
        guide_layout = QVBoxLayout(guide)
        guide_layout.setContentsMargins(16, 12, 16, 12)
        guide_layout.addWidget(
            wrap_label(
                "<b>1.</b> Selecciona texto en el navegador y pulsa la <b>X</b> de "
                "UNnexor: <b>Enviar directamente</b> manda el texto tal cual; "
                "<b>Consultar…</b> te deja escribir una pregunta u orden sobre él."
                "<br><b>2.</b> La respuesta aparece en el panel flotante, con "
                "fórmulas y código formateados. <b>Capturar</b> analiza una zona "
                "de la pantalla (ejercicios, gráficos, fórmulas)."
                "<br><b>3.</b> El <b>copiloto</b> escucha una conversación y te "
                "propone qué decir, en primera persona."
            )
        )
        layout.addWidget(guide)

        layout.addWidget(section("Acciones"))
        grid = QGridLayout()
        grid.setSpacing(10)
        actions = [
            ("sparkles", "Recomendar configuración", "Analiza tu equipo y lo deja listo",
             window.open_recommendations),
            ("models", "Elegir o descargar modelo", "Catálogo con compatibilidad",
             lambda: center.open_page("models")),
            ("copilot", "Copiloto en tiempo real", "Conversación, traducción, desafíos",
             window.open_copilot),
            ("capture", "Capturar pantalla", "Visión u OCR sobre una región",
             window._start_capture),
            ("link", "Emparejar la extensión", "Vincula Chrome o Edge",
             window._open_pairing),
            ("settings", "Ajustes", "Comportamiento, ventana y privacidad",
             lambda: window._open_settings(center)),
            ("providers", "Proveedores de API", "Modelos remotos opcionales",
             window._open_providers),
            ("diagnostics", "Exportar diagnóstico", "ZIP sin datos personales",
             window._export_diagnostics),
        ]
        for index, (icon, label, hint, slot) in enumerate(actions):
            button = QPushButton(icons.menu_icon(icon), f"  {label}")
            button.setToolTip(hint)
            button.setIconSize(QSize(18, 18))
            button.setMinimumHeight(44)
            button.setStyleSheet("text-align: left; padding-left: 12px;")
            button.clicked.connect(lambda _c=False, s=slot: s())
            grid.addWidget(button, index // 2, index % 2)
        layout.addLayout(grid)
        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(content))

    # ------------------------------------------------------------------
    def set_connection(self, reachable: Optional[bool], detail: str) -> None:
        if reachable is None:
            text = "⚪ Comprobando la conexión con Ollama…"
        elif reachable:
            text = "🟢 <b>Ollama conectado.</b> Todo listo para responder."
        else:
            text = (
                f"🔴 <b>Ollama no responde</b> ({detail}). Ábrelo o instálalo desde "
                "ollama.com; UNnexor lo detectará automáticamente."
            )
        self.connection.setText(text)

    def set_model(self, label: str) -> None:
        self.model.setText(f"<b>Modelo activo:</b> {label or 'ninguno'}")

    def apply_snapshot(self, snapshot: Optional[PullSnapshot], pending: int) -> None:
        if snapshot is None:
            self.download_view.setVisible(False)
            self.downloads.setText("Sin descargas en curso.")
            return
        name = snapshot.model.partition(":")[2]
        extra = f" · {pending} más en cola" if pending else ""
        self.downloads.setText(f"<b>Descargando {name}</b>{extra}")
        self.download_view.setVisible(True)
        self.download_view.apply(snapshot)


class ControlCenter(QMainWindow):
    """Ventana de gestión con navegación lateral."""

    def __init__(self, bridge: Any, downloads: Optional[DownloadManager]) -> None:
        super().__init__()
        self.bridge = bridge
        self._downloads = downloads
        self.setWindowTitle("UNnexor — Centro de control")
        self.setStyleSheet(DIALOG_QSS)
        fit_to_screen(self, QSize(1120, 800), QSize(760, 540))

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(220)
        self.nav.setIconSize(QSize(18, 18))
        for _key, label, icon in PAGES:
            self.nav.addItem(QListWidgetItem(icons.icon(icon, "#c9cde0"), label))
        layout.addWidget(self.nav)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.home = HomePage(self)
        self.models = ModelsPanel(
            bridge._ollama, bridge._cfg.ollama_model, self, downloads=downloads
        )
        self.models.modelSelected.connect(self._on_model_selected)
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.models)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        self.statusBar().showMessage(
            "Las descargas continúan aunque cierres esta ventana."
        )

        if downloads is not None:
            downloads.jobProgress.connect(self._on_download)
            downloads.jobFinished.connect(self._on_download_finished)
        self.refresh()

    # ------------------------------------------------------------------
    def open_page(self, key: str) -> None:
        keys = [k for k, _l, _i in PAGES]
        if key in keys:
            self.nav.setCurrentRow(keys.index(key))
        self.bring_to_front()

    def bring_to_front(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def refresh(self) -> None:
        self.home.set_connection(self.bridge.connection_state(), self.bridge.connection_detail())
        self.home.set_model(self.bridge.active_model_label())

    def _on_model_selected(self, name: str) -> None:
        self.bridge.apply_model(name)
        self.models.set_current_model(name)
        self.refresh()
        self.statusBar().showMessage(f"Modelo activo: {name}", 5000)

    def _on_download(self, snapshot: PullSnapshot) -> None:
        pending = len(self._downloads.pending()) if self._downloads else 0
        self.home.apply_snapshot(snapshot, pending)

    def _on_download_finished(self, snapshot: PullSnapshot) -> None:
        self.statusBar().showMessage(
            f"{snapshot.model.partition(':')[2]}: {snapshot.describe()}", 8000
        )
        if self._downloads is not None and self._downloads.active_job() is None:
            self.home.apply_snapshot(None, 0)

    def shutdown(self) -> None:
        self.models.shutdown()
        if self._downloads is not None:
            for signal, slot in (
                (self._downloads.jobProgress, self._on_download),
                (self._downloads.jobFinished, self._on_download_finished),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self.close()


__all__ = ["ControlCenter", "HomePage"]

