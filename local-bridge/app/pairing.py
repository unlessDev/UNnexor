"""Emparejamiento extensión ↔ puente (código efímero de 6 dígitos).

Flujo: el usuario abre el menú del panel → "Emparejar extensión"; el panel
muestra un código de 6 dígitos válido 10 minutos; la página de opciones de
la extensión lo introduce y recibe el ``BRIDGE_TOKEN``.

Seguridad: el servidor escucha solo en 127.0.0.1, el endpoint exige un
origen ``chrome-extension://`` (o cliente no-navegador), la confirmación
está limitada a 5 intentos/minuto y el código caduca y rota. Nunca se
entrega el token por otros caminos.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Optional


class PairingManager:
    """Códigos de emparejamiento con caducidad y rotación."""

    TTL_S = 600  # el código vive 10 minutos
    CODE_ATTEMPTS = 5  # confirmaciones por minuto (aplica el servidor)

    def __init__(self, token: str, ttl_s: int = TTL_S) -> None:
        self._token = token
        self._ttl = ttl_s
        self._lock = threading.Lock()
        self._code = ""
        self._expires_at = 0.0

    def current_code(self) -> str:
        """Devuelve el código vigente (lo crea si no existe o caducó)."""
        with self._lock:
            now = time.monotonic()
            if not self._code or now >= self._expires_at:
                self._code = f"{secrets.randbelow(1_000_000):06d}"
                self._expires_at = now + self._ttl
            return self._code

    def confirm(self, code: str) -> Optional[str]:
        """Devuelve el token si el código coincide y está vigente."""
        supplied = (code or "").strip()
        with self._lock:
            now = time.monotonic()
            if not self._code or now >= self._expires_at:
                return None
            if not secrets.compare_digest(
                supplied.encode("utf-8"), self._code.encode("utf-8")
            ):
                return None
            # Un solo uso: rota inmediatamente tras confirmar.
            self._code = ""
            self._expires_at = 0.0
            return self._token

    def revoke(self) -> None:
        with self._lock:
            self._code = ""
            self._expires_at = 0.0

    def seconds_left(self) -> int:
        with self._lock:
            if not self._code:
                return 0
            return max(0, int(self._expires_at - time.monotonic()))
