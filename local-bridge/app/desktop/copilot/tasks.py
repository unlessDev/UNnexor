"""Trabajo pesado fuera del hilo de la interfaz (QThreadPool).

``run_async(fn, on_done=..., on_error=...)`` ejecuta ``fn`` en el pool
global y entrega el resultado en el hilo de la GUI mediante señales.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger("unnexor.copilot.ui")

_alive: set[_Bridge] = set()


class _Bridge(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Job(QRunnable):
    def __init__(self, fn: Callable[[], Any], bridge: _Bridge) -> None:
        super().__init__()
        self._fn = fn
        self._bridge = bridge

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # se entrega a la GUI como texto
            log.exception("Tarea en segundo plano fallida")
            self._deliver(
                self._bridge.failed, getattr(exc, "message", None) or str(exc)
            )
            return
        self._deliver(self._bridge.done, result)

    @staticmethod
    def _deliver(signal: Any, value: Any) -> None:
        try:
            signal.emit(value)
        except RuntimeError:
            # La interfaz ya se cerró: el resultado se descarta.
            log.debug("Resultado descartado: la ventana ya no existe.")


def run_async(
    fn: Callable[[], Any],
    *,
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
) -> None:
    bridge = _Bridge()
    _alive.add(bridge)

    def finish_ok(result: Any) -> None:
        _alive.discard(bridge)
        if on_done is not None:
            on_done(result)

    def finish_error(message: str) -> None:
        _alive.discard(bridge)
        if on_error is not None:
            on_error(message)

    bridge.done.connect(finish_ok)
    bridge.failed.connect(finish_error)
    QThreadPool.globalInstance().start(_Job(fn, bridge))
