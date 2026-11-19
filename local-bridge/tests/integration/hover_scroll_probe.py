"""Sonda de integración: Hover Scroll y no-activación en Windows REAL.

Ejecutar como script independiente (plataforma Qt "windows"):

    python tests/integration/hover_scroll_probe.py

Qué verifica empíricamente con SendInput/SetCursorPos:
  1. wheel_scroll      — la rueda sobre el panel lo desplaza SIN clic previo.
  2. focus_kept        — la ventana en primer plano no cambia al usar la rueda.
  3. click_noactivate  — hacer clic sobre el panel no lo activa.
  4. drag_moves        — arrastrar la barra de título mueve la ventana.

Imprime un JSON con los resultados y termina con código 0 si todo pasa.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "windows"
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.config import Config  # noqa: E402
from app.desktop.window import BridgeWindow  # noqa: E402
from app.ollama_client import OllamaClient  # noqa: E402
from app.queue_manager import Bus, RuntimeState  # noqa: E402

user32 = ctypes.windll.user32

WM_MOUSEWHEEL = 0x020A
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


def send_wheel(delta: int) -> None:
    inp = INPUT(type=0)
    inp.mi = MOUSEINPUT(0, 0, delta & 0xFFFFFFFF, MOUSEEVENTF_WHEEL, 0, None)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def send_click() -> None:
    down = INPUT(type=0)
    down.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, None)
    up = INPUT(type=0)
    up.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, None)
    user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
    time.sleep(0.05)
    user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))


def pump(app, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def main() -> int:
    app = QApplication(sys.argv)

    cfg = Config()
    cfg.token = ""
    cfg.hover_scroll_hook = True
    cfg.always_on_top = True
    cfg.window_width, cfg.window_height = 480, 600

    bus, state = Bus(), RuntimeState(model="probe")
    ollama = OllamaClient(cfg, base_url="http://127.0.0.1:9")  # no llamado en la sonda

    window = BridgeWindow(cfg, bus, state, ollama)
    screen = QGuiApplication.primaryScreen().availableGeometry()
    window.resize(cfg.window_width, cfg.window_height)
    window.move(
        screen.right() - cfg.window_width - 60,
        screen.bottom() - cfg.window_height - 60,
    )

    # Contenido largo para que exista scroll real.
    from PySide6.QtWidgets import QLabel

    vbox = window._chat_vbox
    for i in range(60):
        label = QLabel(f"línea de prueba {i:02d} " + "contenido " * 8)
        label.setWordWrap(True)
        vbox.insertWidget(vbox.count() - 1, label)

    window.show()
    hwnd = window._hwnd()
    assert hwnd, "No se obtuvo HWND nativo"
    pump(app, 0.6)

    results = {"wheel_scroll": False, "focus_kept": False,
               "click_noactivate": False, "drag_moves": False}

    geo = window.frameGeometry()

    # --- 1/2: rueda sobre el panel sin clic, foco intacto -----------------
    center_x = geo.left() + geo.width() // 2
    center_y = geo.top() + geo.height() // 2
    user32.SetCursorPos(center_x, center_y)
    pump(app, 0.25)

    foreground_before = user32.GetForegroundWindow()
    assert foreground_before != hwnd, \
        "El panel ya era la ventana en primer plano antes de la prueba"

    start_value = window._chat_scroll.verticalScrollBar().value()
    for _ in range(5):
        send_wheel(-120)  # hacia abajo
        pump(app, 0.08)
    pump(app, 0.3)
    end_value = window._chat_scroll.verticalScrollBar().value()
    foreground_after = user32.GetForegroundWindow()

    results["wheel_scroll"] = end_value > start_value
    results["focus_kept"] = (foreground_after == foreground_before)
    print(f"[probe] scroll {start_value} -> {end_value}", file=sys.stderr)

    # --- 3: clic sobre la barra de título no activa ------------------------
    title_x = geo.left() + 80
    title_y = geo.top() + 10
    user32.SetCursorPos(title_x, title_y)
    pump(app, 0.2)
    fg_before_click = user32.GetForegroundWindow()
    send_click()
    pump(app, 0.3)
    fg_after_click = user32.GetForegroundWindow()
    results["click_noactivate"] = (fg_after_click == fg_before_click) and \
        (fg_after_click != hwnd)

    # --- 4: arrastre de la barra de título ---------------------------------
    user32.SetCursorPos(title_x, title_y)
    pump(app, 0.2)
    pos_before = window.frameGeometry().topLeft()
    down = INPUT(type=0)
    down.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, None)
    user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
    pump(app, 0.1)
    for step in range(10):
        user32.SetCursorPos(title_x - step * 12, title_y + step * 2)
        pump(app, 0.02)
    up = INPUT(type=0)
    up.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, None)
    user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))
    pump(app, 0.2)
    pos_after = window.frameGeometry().topLeft()
    results["drag_moves"] = abs(pos_after.x() - pos_before.x()) >= 40

    # Limpieza: cerrar la ventana sin efectos secundarios.
    window.stop_workers()
    window._hook.uninstall()
    window._uninstall_native_filter()
    window.close()
    pump(app, 0.2)

    print(json.dumps(results))
    ok = all(results.values())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
