"""Sección de análisis y resolución de desafíos en pantalla.

Reutiliza el selector de regiones existente (``desktop/screenshot.py``).
Las ventanas del copiloto se ocultan durante la captura para no salir en
ella. Nada se captura sin una acción del usuario: el análisis periódico
solo corre mientras está activado expresamente y se detiene al instante.
"""

from __future__ import annotations

import base64

import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QIcon, QImage, QPixmap, QScreen
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...copilot.challenges import ChallengeResult, ChangeDetector, fingerprint
from ..widgets import AssistantMessage
from .common import hint, title
from .controller import CopilotController

MAX_IMAGE_WIDTH = 1600
MAX_IMAGES = 4


def pixmap_to_b64(pixmap: QPixmap) -> str:
    if pixmap.width() > MAX_IMAGE_WIDTH:
        pixmap = pixmap.scaledToWidth(
            MAX_IMAGE_WIDTH, Qt.TransformationMode.SmoothTransformation
        )
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    return base64.b64encode(bytes(data.data())).decode("ascii")


def pixmap_fingerprint(pixmap: QPixmap) -> int:
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_Grayscale8)
    image = image.scaled(90, 80, Qt.AspectRatioMode.IgnoreAspectRatio)
    stride = image.bytesPerLine()
    raw = np.frombuffer(bytes(image.constBits()), dtype=np.uint8)
    gray = raw.reshape(image.height(), stride)[:, : image.width()]
    return fingerprint(gray)


class ChallengesPage(QWidget):
    hideWindowsRequested = Signal()
    restoreWindowsRequested = Signal()

    def __init__(self, controller: CopilotController) -> None:
        super().__init__()
        self._c = controller
        self._images: list[QPixmap] = []
        self._region: tuple[QRect, QScreen] | None = None
        self._selector: object | None = None
        self._detector = ChangeDetector()
        self._answer: AssistantMessage | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._periodic_tick)

        layout = QVBoxLayout(self)
        layout.addWidget(title("Análisis y resolución de desafíos"))
        layout.addWidget(
            hint(
                "Captura una región, la pantalla completa o varias regiones y "
                "pide una solución estructurada. Se usa un modelo con visión si "
                "está permitido; si no, OCR local + modelo de texto."
            )
        )
        buttons = QHBoxLayout()
        self.region = QPushButton("✂ Capturar región")
        self.full = QPushButton("🖥 Pantalla completa")
        self.add = QPushButton("＋ Añadir región")
        self.clear_btn = QPushButton("Vaciar")
        for button in (self.region, self.full, self.add, self.clear_btn):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.thumbs = QListWidget()
        self.thumbs.setViewMode(QListWidget.ViewMode.IconMode)
        self.thumbs.setIconSize(QPixmap(120, 70).size())
        self.thumbs.setMaximumHeight(110)
        layout.addWidget(self.thumbs)
        self.instruction = QPlainTextEdit()
        self.instruction.setPlaceholderText(
            "Instrucción opcional (p. ej. 'Explica y resuelve el ejercicio 3 en Python')"
        )
        self.instruction.setMaximumHeight(70)
        layout.addWidget(self.instruction)
        run_row = QHBoxLayout()
        self.solve = QPushButton("🧠 Analizar")
        self.solve.setObjectName("primary")
        self.cancel = QPushButton("Detener")
        self.plan = QLabel("")
        self.plan.setObjectName("statusLine")
        run_row.addWidget(self.solve)
        run_row.addWidget(self.cancel)
        run_row.addWidget(self.plan, 1)
        layout.addLayout(run_row)
        periodic = QHBoxLayout()
        self.periodic = QCheckBox(
            "Análisis periódico de la última región/pantalla cada"
        )
        self.interval = QSpinBox()
        self.interval.setRange(5, 600)
        self.interval.setSuffix(" s")
        self.interval.setValue(controller.settings.periodic_interval_s)
        periodic.addWidget(self.periodic)
        periodic.addWidget(self.interval)
        periodic.addStretch(1)
        layout.addLayout(periodic)
        layout.addWidget(
            hint(
                "El análisis periódico solo procesa la pantalla cuando cambia de "
                "forma relevante y se detiene al desmarcarlo."
            )
        )
        self.result_holder = QVBoxLayout()
        holder = QWidget()
        holder.setLayout(self.result_holder)
        layout.addWidget(holder, 1)

        self.region.clicked.connect(lambda: self.capture_region(append=False))
        self.add.clicked.connect(lambda: self.capture_region(append=True))
        self.full.clicked.connect(self.capture_full)
        self.clear_btn.clicked.connect(self.clear_images)
        self.solve.clicked.connect(self.run)
        self.cancel.clicked.connect(controller.solver.cancel)
        self.periodic.toggled.connect(self._toggle_periodic)
        controller.challengeStarted.connect(self._on_started)
        controller.challengeChunk.connect(self._on_chunk)
        controller.challengeDone.connect(self._on_done)
        controller.challengeFailed.connect(self._on_failed)
        controller.notice.connect(self._on_notice)

    # ------------------------------------------------------------ captura
    def capture_region(self, append: bool = False) -> None:
        from ..screenshot import RegionSelector

        self.hideWindowsRequested.emit()
        selector = RegionSelector(self)
        self._selector = selector

        def captured(pixmap: QPixmap) -> None:
            rect, screen = selector.last_rect, selector.last_screen
            if rect is not None and screen is not None:
                self._region = (QRect(rect), screen)
            self._add_image(pixmap, append)
            self.restoreWindowsRequested.emit()

        selector.captured.connect(captured)
        selector.cancelled.connect(self.restoreWindowsRequested)
        QTimer.singleShot(150, selector.start)

    def capture_full(self) -> None:
        self.hideWindowsRequested.emit()

        def grab() -> None:
            screen = (
                QGuiApplication.screenAt(QCursor.pos())
                or QGuiApplication.primaryScreen()
            )
            if screen is not None:
                self._region = (QRect(), screen)  # QRect vacío = pantalla completa
                self._add_image(screen.grabWindow(0), append=False)
            self.restoreWindowsRequested.emit()

        QTimer.singleShot(250, grab)

    def _grab_current(self) -> QPixmap | None:
        if self._region is None:
            return None
        rect, screen = self._region
        full = screen.grabWindow(0)
        if rect.isNull():
            return full
        dpr = full.devicePixelRatio() or 1.0
        return full.copy(
            QRect(
                int(rect.x() * dpr),
                int(rect.y() * dpr),
                int(rect.width() * dpr),
                int(rect.height() * dpr),
            )
        )

    def _add_image(self, pixmap: QPixmap, append: bool) -> None:
        if pixmap.isNull():
            return
        if not append:
            self.clear_images()
        if len(self._images) >= MAX_IMAGES:
            QMessageBox.information(
                self, "Capturas", f"Máximo {MAX_IMAGES} capturas por análisis."
            )
            return
        self._images.append(pixmap)
        item = QListWidgetItem(QIcon(pixmap), f"Captura {len(self._images)}")
        self.thumbs.addItem(item)
        self.plan.setText(self._c.solver.plan()[1] if not self._c.solver.busy else "")

    def clear_images(self) -> None:
        self._images.clear()
        self.thumbs.clear()

    # ------------------------------------------------------------ análisis
    def run(self) -> None:
        if not self._images:
            QMessageBox.information(
                self, "Desafíos", "Primero captura una región o la pantalla."
            )
            return
        images = [pixmap_to_b64(p) for p in self._images]
        self._c.solve_challenge(images, self.instruction.toPlainText().strip())

    def _toggle_periodic(self, enabled: bool) -> None:
        if enabled:
            if self._region is None:
                self.periodic.setChecked(False)
                QMessageBox.information(
                    self,
                    "Análisis periódico",
                    "Captura primero la región o pantalla que quieres vigilar.",
                )
                return
            s = self._c.settings
            s.periodic_interval_s = self.interval.value()
            self._c.save_settings(s)
            self._detector.reset()
            self._timer.start(self.interval.value() * 1000)
            self._periodic_tick()
        else:
            self._timer.stop()

    def _periodic_tick(self) -> None:
        if self._c.solver.busy:
            return
        pixmap = self._grab_current()
        if pixmap is None or pixmap.isNull():
            return
        if not self._detector.should_process(pixmap_fingerprint(pixmap)):
            self.plan.setText("Sin cambios relevantes en la pantalla.")
            return
        self._images = [pixmap]
        self.thumbs.clear()
        self.thumbs.addItem(QListWidgetItem(QIcon(pixmap), "Captura periódica"))
        self._c.solve_challenge(
            [pixmap_to_b64(pixmap)], self.instruction.toPlainText().strip()
        )

    def stop_periodic(self) -> None:
        self.periodic.setChecked(False)

    # ------------------------------------------------------------ resultados
    def _clear_result(self) -> None:
        while self.result_holder.count():
            child = self.result_holder.takeAt(0)
            widget = child.widget() if child is not None else None
            if widget is not None:
                widget.deleteLater()

    def _on_started(self, _instruction: str) -> None:
        self._clear_result()
        self._answer = AssistantMessage("desafío", role="COPILOTO")
        self.result_holder.addWidget(self._answer)
        self.solve.setEnabled(False)

    def _on_chunk(self, delta: str) -> None:
        if self._answer is not None:
            self._answer.append_streaming(delta)

    def _on_done(self, result: ChallengeResult) -> None:
        # El resultado final manda sobre lo recibido en streaming.
        if self._answer is None or self._answer.text().strip() != result.text.strip():
            self._clear_result()
            self._answer = AssistantMessage("desafío", role="COPILOTO")
            self._answer.append_streaming(result.text)
            self.result_holder.addWidget(self._answer)
        self._answer.finalize()
        method = "visión" if result.method == "vision" else "OCR + texto"
        self.plan.setText(f"Resuelto con {result.model} ({method}).")
        self.solve.setEnabled(True)

    def _on_failed(self, message: str) -> None:
        if self._answer is not None:
            self._answer.append_streaming(f"⚠ {message}")
            self._answer.mark_error()
        self.plan.setText(message)
        self.solve.setEnabled(True)

    def _on_notice(self, message: str) -> None:
        if self.isVisible():
            self.plan.setText(message)

    def current_text(self) -> str:
        return self._answer.text() if self._answer is not None else ""

    def capture_and_solve(self) -> None:
        """Atajo global: capturar región y analizar al terminar."""
        self.capture_region(append=False)
        selector = self._selector
        if selector is not None:
            selector.captured.connect(lambda _p: QTimer.singleShot(200, self.run))  # type: ignore[attr-defined]
