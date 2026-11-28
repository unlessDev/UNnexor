"""Integración con la Windows API para la ventana flotante.

Piezas clave del requisito "Hover Scroll" (la rueda del ratón debe desplazar
el panel aunque el foco siga en el navegador):

1. ``WS_EX_NOACTIVATE`` (via ``Qt.WindowDoesNotAcceptFocus`` + refuerzo
   nativo) → hacer clic sobre el panel no le roba el foco al navegador.
2. Windows 10/11 enruta ``WM_MOUSEWHEEL`` a la ventana bajo el cursor cuando
   "Desplazar ventanas inactivas al pasar el ratón" está activado (por
   defecto lo está).
3. ``HoverScrollHook`` — hook global de rueda de bajo nivel
   (``WH_MOUSE_LL``): si el cursor está sobre nuestro panel, redirige el
   mensaje al panel con ``PostMessageW`` y lo traga (devuelve 1), de modo que
   el desplazamiento funciona **aunque el ajuste del sistema esté
   desactivado**. El callback es O(1) para no provocar timeouts del hook.
4. ``NoActivateFilter`` — filtro de eventos nativos de Qt que responde
   ``MA_NOACTIVATE`` a ``WM_MOUSEACTIVATE`` (refuerzo extra).

Todo usa ctypes (sin dependencias externas: sin pywin32).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
from typing import Any, Callable

from PySide6.QtCore import QAbstractNativeEventFilter, QByteArray

log = logging.getLogger("unnexor.win32")

try:  # pragma: no cover - depende del entorno
    _user32: Any = ctypes.windll.user32
    IS_WINDOWS = True
except (AttributeError, OSError):  # pragma: no cover
    _user32 = None
    IS_WINDOWS = False

# Constantes Win32
WM_MOUSEACTIVATE = 0x0021
MA_NOACTIVATE = 1
WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E
WH_MOUSE_LL = 14
WS_EX_NOACTIVATE = 0x08000000
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080
WS_MINIMIZEBOX = 0x00020000
GWL_EXSTYLE = -20
GWL_STYLE = -16
WM_APP_QUIT_HOOK = 0x0402  # mensaje interno para desinstalar el hook de forma segura


if IS_WINDOWS:

    class MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", wt.HWND),
            ("message", wt.UINT),
            ("wParam", wt.WPARAM),
            ("lParam", wt.LPARAM),
            ("time", wt.DWORD),
            ("pt", wt.POINT),
        ]

    class POINT(ctypes.Structure):
        _fields_ = [("x", wt.LONG), ("y", wt.LONG)]

    class MOUSEHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("pt", POINT),
            ("hwnd", wt.HWND),
            ("wHitTestCode", wt.UINT),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class MOUSEHOOKSTRUCTEX(ctypes.Structure):
        _fields_ = [("mouse", MOUSEHOOKSTRUCT), ("mouseData", wt.DWORD)]

    HOOKPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t
    )

    _user32.SetWindowsHookExW.restype = wt.HHOOK
    _user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int,
        HOOKPROC,
        wt.HINSTANCE,
        wt.DWORD,
    ]
    _user32.UnhookWindowsHookEx.restype = wt.BOOL
    _user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
    _user32.CallNextHookEx.restype = ctypes.c_ssize_t
    _user32.CallNextHookEx.argtypes = [
        wt.HHOOK,
        ctypes.c_int,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
    ]
    _user32.PostMessageW.restype = wt.BOOL
    _user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    _user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
    _user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _user32.GetForegroundWindow.restype = wt.HWND
else:  # pragma: no cover
    MSG = POINT = MOUSEHOOKSTRUCTEX = None  # type: ignore[assignment,misc]
    HOOKPROC = None  # type: ignore[assignment]


def apply_noactivate(hwnd: int, taskbar: bool = True) -> None:
    """Refuerza ``WS_EX_NOACTIVATE`` en el HWND (por si Qt no lo aplicó).

    Windows oculta de la barra de tareas las ventanas con
    ``WS_EX_NOACTIVATE`` salvo que tengan ``WS_EX_APPWINDOW``: sin él, al
    minimizar el panel desaparecía por completo. ``WS_MINIMIZEBOX`` permite
    minimizar/restaurar desde el botón de la barra de tareas aunque la
    ventana no tenga marco.
    """
    if not IS_WINDOWS or not hwnd:
        return
    try:
        handle = wt.HWND(hwnd)
        exstyle = _user32.GetWindowLongPtrW(handle, GWL_EXSTYLE)
        wanted = exstyle | WS_EX_NOACTIVATE
        if taskbar:
            wanted = (wanted | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW
        if wanted != exstyle:
            _user32.SetWindowLongPtrW(handle, GWL_EXSTYLE, wanted)
        if taskbar:
            style = _user32.GetWindowLongPtrW(handle, GWL_STYLE)
            if not (style & WS_MINIMIZEBOX):
                _user32.SetWindowLongPtrW(handle, GWL_STYLE, style | WS_MINIMIZEBOX)
    except Exception as exc:  # pragma: no cover
        log.debug("No se pudo reforzar el estilo de ventana: %s", exc)


class NoActivateFilter(QAbstractNativeEventFilter):
    """Devuelve MA_NOACTIVATE cuando alguien intenta activar nuestro panel."""

    def __init__(self, hwnd_provider: Callable[[], int]) -> None:
        super().__init__()
        self._hwnd_provider = hwnd_provider

    def nativeEventFilter(self, event_type, message):
        if not IS_WINDOWS:
            return False, 0
        try:
            if event_type == QByteArray(b"windows_generic_MSG"):
                msg = ctypes.cast(int(message), ctypes.POINTER(MSG)).contents
                if (
                    msg.message == WM_MOUSEACTIVATE
                    and msg.hwnd
                    and int(msg.hwnd or 0) == (self._hwnd_provider() or 0)
                ):
                    return True, MA_NOACTIVATE
        except Exception:  # pragma: no cover - nunca romper el bucle de eventos
            pass
        return False, 0


class HoverScrollHook:
    """``WH_MOUSE_LL``: redirige la rueda al panel sin activarlo.

    Cuando el cursor está sobre el panel, intercepta WM_MOUSEWHEEL /
    WM_MOUSEHWHEEL, publica el mensaje en el HWND del panel y traga el evento
    (retorno 1) para que la ventana con foco (el navegador) no lo reciba.
    """

    def __init__(self) -> None:
        self._hook: Any = None
        self._proc: Any = None  # referencia viva: GC no puede tocar el callback
        self._target: int = 0

    # ------------------------------------------------------------------
    def install(self, target_hwnd: int) -> bool:
        if not IS_WINDOWS:
            log.info("HoverScrollHook: no disponible fuera de Windows.")
            return False
        if self._hook is not None:
            self._target = target_hwnd
            return True
        try:
            self._target = target_hwnd
            self._proc = HOOKPROC(self._on_mouse)
            self._hook = _user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, None, 0)
        except Exception as exc:
            log.warning("No se pudo instalar el hook de rueda: %s", exc)
            self._hook = None
            return False
        if not self._hook:
            log.warning("SetWindowsHookExW devolvió NULL (hook no instalado).")
            return False
        log.info("HoverScrollHook instalado (WH_MOUSE_LL) sobre HWND %s", target_hwnd)
        return True

    def update_target(self, hwnd: int) -> None:
        self._target = hwnd

    def uninstall(self) -> None:
        if self._hook is not None:
            try:
                _user32.UnhookWindowsHookEx(self._hook)
            except Exception:  # pragma: no cover
                pass
            self._hook = None
            self._proc = None
            log.info("HoverScrollHook desinstalado.")

    # ------------------------------------------------------------------
    @property
    def installed(self) -> bool:
        return self._hook is not None

    def _on_mouse(self, n_code: int, w_param: int, l_param: int) -> int:
        try:
            if n_code == 0 and w_param in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
                info = ctypes.cast(
                    ctypes.c_void_p(l_param), ctypes.POINTER(MOUSEHOOKSTRUCTEX)
                ).contents
                hwnd = info.mouse.hwnd
                target = self._target
                if target and hwnd and int(hwnd or 0) == target:
                    delta = ctypes.c_short((info.mouseData >> 16) & 0xFFFF).value
                    w_pack = (delta & 0xFFFF) << 16
                    packed_lparam = ((info.mouse.pt.y & 0xFFFF) << 16) | (
                        info.mouse.pt.x & 0xFFFF
                    )
                    _user32.PostMessageW(
                        wt.HWND(target), w_param, w_pack, packed_lparam
                    )
                    return 1  # tragar el evento: el foco (navegador) no lo recibe
        except Exception as exc:  # pragma: no cover - el hook jamás debe explotar
            log.debug("Error en hook de rueda: %s", exc)
        return _user32.CallNextHookEx(None, n_code, w_param, l_param)
