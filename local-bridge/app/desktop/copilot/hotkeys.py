"""Atajos globales del copiloto (Windows ``RegisterHotKey``).

* Combinaciones configurables tipo ``Ctrl+Alt+Shift+C``.
* Si otra aplicación (o el sistema) ya usa la combinación, Windows rechaza
  el registro: se informa el conflicto en lugar de fallar en silencio.
* ``MOD_NOREPEAT`` evita disparos repetidos al mantener pulsada la tecla.
* Fuera de Windows no se registra nada (y se informa).
"""

from __future__ import annotations

import ctypes
import logging
import sys
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QAbstractNativeEventFilter, QByteArray

log = logging.getLogger("extdator.copilot.hotkeys")

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312
_MODS = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
}
_SPECIAL = {f"f{i}": 0x6F + i for i in range(1, 13)} | {
    "space": 0x20,
    "enter": 0x0D,
    "tab": 0x09,
    "pause": 0x13,
}


class HotkeyError(ValueError):
    pass


def parse_hotkey(text: str) -> tuple[int, int]:
    """``"Ctrl+Alt+C"`` → (modificadores, código virtual)."""
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    if len(parts) < 2:
        raise HotkeyError(
            "Usa al menos un modificador y una tecla (p. ej. Ctrl+Alt+C)."
        )
    mods = 0
    for part in parts[:-1]:
        if part not in _MODS:
            raise HotkeyError(f"Modificador desconocido: {part}")
        mods |= _MODS[part]
    key = parts[-1]
    if key in _SPECIAL:
        vk = _SPECIAL[key]
    elif len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key.upper())
    else:
        raise HotkeyError(f"Tecla no admitida: {key}")
    if mods in (MOD_SHIFT, 0):
        raise HotkeyError("Incluye Ctrl, Alt o Win para no interferir al escribir.")
    return mods, vk


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, dispatch: Callable[[int], None]) -> None:
        super().__init__()
        self._dispatch = dispatch

    def nativeEventFilter(self, event_type: QByteArray | bytes, message: int) -> Any:
        kind = event_type.data() if isinstance(event_type, QByteArray) else event_type
        if bytes(kind) not in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            return False, 0
        try:
            import ctypes.wintypes as wt

            msg = wt.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                self._dispatch(int(msg.wParam))
                return True, 0
        except Exception:  # nunca romper el bucle de eventos
            log.debug("Filtro de atajos con error", exc_info=True)
        return False, 0


# Combinaciones del sistema o de uso universal que no deben capturarse.
RESERVED = {
    "Ctrl+Alt+Delete": "reservada por Windows",
    "Ctrl+Shift+Escape": "Administrador de tareas",
    "Alt+F4": "cerrar ventana",
    "Alt+Tab": "cambiar de ventana",
    "Win+L": "bloquear el equipo",
    "Win+D": "mostrar el escritorio",
    "Win+E": "Explorador de archivos",
    "Win+R": "Ejecutar",
    "Win+Tab": "vista de tareas",
    "Ctrl+C": "copiar",
    "Ctrl+V": "pegar",
    "Ctrl+X": "cortar",
    "Ctrl+Z": "deshacer",
    "Ctrl+A": "seleccionar todo",
    "Ctrl+S": "guardar",
}


def _reserved_keys() -> dict[tuple[int, int], str]:
    out: dict[tuple[int, int], str] = {}
    for combo, why in RESERVED.items():
        parts = [p.strip().lower() for p in combo.split("+")]
        mods = 0
        for part in parts[:-1]:
            mods |= _MODS[part]
        key = parts[-1]
        vk = {"delete": 0x2E, "escape": 0x1B, "tab": 0x09}.get(key)
        if vk is None:
            vk = _SPECIAL.get(key) or ord(key.upper())
        out[(mods, vk)] = f"{combo} ({why})"
    return out


def find_conflicts(bindings: dict[str, str]) -> list[str]:
    """Conflictos entre atajos de UNnexor y con combinaciones reservadas.

    ``bindings``: {etiqueta: combinación}. Las vacías se ignoran. No detecta
    los atajos de OTRAS aplicaciones: eso lo informa ``register`` cuando
    Windows rechaza la combinación.
    """
    problems: list[str] = []
    seen: dict[tuple[int, int], str] = {}
    reserved = _reserved_keys()
    for label, combo in bindings.items():
        if not combo.strip():
            continue
        try:
            key = parse_hotkey(combo)
        except HotkeyError as exc:
            problems.append(f"«{label}»: {exc}")
            continue
        if key in reserved:
            problems.append(f"«{label}»: {combo} coincide con {reserved[key]}.")
        if key in seen:
            problems.append(
                f"«{label}» y «{seen[key]}» usan la misma combinación ({combo})."
            )
        else:
            seen[key] = label
    return problems


class HotkeyManager:
    def __init__(self) -> None:
        self._actions: dict[int, Callable[[], None]] = {}
        self._next_id = 0xE100
        self._filter: _Filter | None = None
        self.available = sys.platform == "win32"

    def register(self, combo: str, action: Callable[[], None]) -> str:
        """Registra el atajo; devuelve "" o el motivo del fallo."""
        if not combo.strip():
            return ""
        try:
            mods, vk = parse_hotkey(combo)
        except HotkeyError as exc:
            return str(exc)
        if not self.available:
            return "Los atajos globales solo están disponibles en Windows."
        self._ensure_filter()
        hotkey_id = self._next_id
        user32 = ctypes.windll.user32  # type: ignore[attr-defined,unused-ignore]
        if not user32.RegisterHotKey(None, hotkey_id, mods | MOD_NOREPEAT, vk):
            return f"{combo} ya está en uso por otra aplicación o por el sistema."
        self._next_id += 1
        self._actions[hotkey_id] = action
        return ""

    def unregister_all(self) -> None:
        if self.available:
            user32 = ctypes.windll.user32  # type: ignore[attr-defined,unused-ignore]
            for hotkey_id in list(self._actions):
                user32.UnregisterHotKey(None, hotkey_id)
        self._actions.clear()

    def _ensure_filter(self) -> None:
        if self._filter is not None:
            return
        from PySide6.QtCore import QCoreApplication

        app = QCoreApplication.instance()
        if app is None:
            return
        self._filter = _Filter(self._dispatch)
        app.installNativeEventFilter(self._filter)

    def _dispatch(self, hotkey_id: int) -> None:
        action = self._actions.get(hotkey_id)
        if action is not None:
            try:
                action()
            except Exception:
                log.exception("Fallo ejecutando un atajo global")
