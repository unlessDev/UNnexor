"""Redimensionado de ventanas sin marco desde cualquier borde.

Problema que resuelve: en las ventanas sin marco (panel principal y panel
flotante del copiloto) el contenido ocupa todo el borde, así que los
eventos del ratón en la franja del borde los recibían los widgets hijos y
la ventana nunca se podía agrandar ni reducir.

Solución: un filtro de eventos de la aplicación detecta la pulsación dentro
de la franja del borde de una ventana registrada —sin importar qué hijo esté
debajo— y delega en el redimensionado nativo del sistema
(``QWindow.startSystemResize``). Un sondeo ligero del cursor muestra el
puntero de redimensionado al pasar por el borde.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication, QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

BAND = 6  # píxeles sensibles en cada borde


def edges_at(widget: QWidget, global_pos: QPoint, band: int = BAND) -> Qt.Edge:
    """Bordes de ``widget`` bajo ``global_pos`` (vacío si no hay ninguno)."""
    rect = widget.frameGeometry()
    if not rect.adjusted(-1, -1, 1, 1).contains(global_pos):
        return Qt.Edge(0)
    x, y = global_pos.x(), global_pos.y()
    edges = Qt.Edge(0)
    if x <= rect.left() + band:
        edges |= Qt.Edge.LeftEdge
    if x >= rect.right() - band:
        edges |= Qt.Edge.RightEdge
    if y <= rect.top() + band:
        edges |= Qt.Edge.TopEdge
    if y >= rect.bottom() - band:
        edges |= Qt.Edge.BottomEdge
    return edges


def cursor_for(edges: Qt.Edge) -> Qt.CursorShape | None:
    if not edges:
        return None
    horizontal = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
    vertical = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
    if horizontal and vertical:
        same = bool(edges & Qt.Edge.LeftEdge) == bool(edges & Qt.Edge.TopEdge)
        return (
            Qt.CursorShape.SizeFDiagCursor if same else Qt.CursorShape.SizeBDiagCursor
        )
    return Qt.CursorShape.SizeHorCursor if horizontal else Qt.CursorShape.SizeVerCursor


class FramelessResizer(QObject):
    """Habilita el redimensionado por bordes de ventanas registradas."""

    _instance: FramelessResizer | None = None

    @classmethod
    def instance(cls) -> FramelessResizer:
        if cls._instance is None:
            cls._instance = FramelessResizer()
        return cls._instance

    def __init__(self) -> None:
        super().__init__()
        self._windows: list[QWidget] = []
        self._cursor_set = False
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._update_cursor)
        self._timer.start()

    def shutdown(self) -> None:
        """Retira el filtro y el sondeo (y el cursor si estaba cambiado)."""
        self._timer.stop()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        if self._cursor_set:
            QGuiApplication.restoreOverrideCursor()
            self._cursor_set = False
        self._windows.clear()

    def manage(self, window: QWidget) -> None:
        if window not in self._windows:
            self._windows.append(window)
            window.destroyed.connect(lambda _o=None, w=window: self._forget(w))

    def _forget(self, window: QWidget) -> None:
        if window in self._windows:
            self._windows.remove(window)

    def _target(self, global_pos: QPoint) -> tuple[QWidget | None, Qt.Edge]:
        for window in self._windows:
            try:
                if (
                    not window.isVisible()
                    or window.isMaximized()
                    or window.isMinimized()
                ):
                    continue
            except RuntimeError:  # objeto C++ ya destruido
                continue
            edges = edges_at(window, global_pos)
            if edges:
                return window, edges
        return None, Qt.Edge(0)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and isinstance(event, QMouseEvent)
            and event.button() == Qt.MouseButton.LeftButton
        ):
            window, edges = self._target(event.globalPosition().toPoint())
            if (
                window is not None
                and isinstance(obj, QWidget)
                and obj.window() is window
            ):
                handle = window.windowHandle()
                if handle is not None and handle.startSystemResize(edges):
                    return True
        return False

    def _update_cursor(self) -> None:
        _window, edges = self._target(QCursor.pos())
        shape = cursor_for(edges)
        if shape is not None:
            if self._cursor_set:
                QGuiApplication.changeOverrideCursor(QCursor(shape))
            else:
                QGuiApplication.setOverrideCursor(QCursor(shape))
                self._cursor_set = True
        elif self._cursor_set:
            QGuiApplication.restoreOverrideCursor()
            self._cursor_set = False


def enable_frameless_resize(window: QWidget) -> None:
    FramelessResizer.instance().manage(window)
