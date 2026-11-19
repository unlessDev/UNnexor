"""Seguridad del puente local: token, origen y límite de peticiones.

El servidor escucha exclusivamente en 127.0.0.1. Aun así, un navegador con
una página maliciosa abierta podría intentar alcanzar el puerto; por eso:

* Se exige un token compartido (``Authorization: Bearer <token>``).
* Se valida el encabezado ``Origin``: los orígenes web (http/https) que no
  coincidan con la lista autorizada se rechazan con 403.
* Se aplica un límite de peticiones por minuto.
"""

from __future__ import annotations

import math
import re
import secrets
import time
from collections import defaultdict, deque
from typing import Deque, Dict, List, Optional, Pattern, Sequence, Tuple


def compile_origin_regex(pattern: str) -> Pattern[str]:
    """Convierte un patrón de origen con comodines en regex anclada.

    Ej.: ``chrome-extension://*`` → ``^chrome-extension://.*$`` (case-insensitive).
    """
    pattern = pattern.strip()
    if pattern == "*":
        return re.compile(r"^.*$", re.IGNORECASE)
    body = "".join(".*" if ch == "*" else re.escape(ch) for ch in pattern)
    return re.compile(f"^{body}$", re.IGNORECASE)


def origins_to_regex_string(origins: Sequence[str]) -> str:
    """Une varios patrones de origen en una regex para CORS (Starlette)."""
    return "|".join(
        compile_origin_regex(pattern).pattern for pattern in origins if pattern.strip()
    )


class RequestGuard:
    """Valida token compartido, origen del navegador y requisitos de auth."""

    def __init__(
        self,
        token: str,
        allowed_origins: Sequence[str],
        auth_required: bool = True,
    ) -> None:
        self._token = token
        self._auth_required = auth_required
        self._origin_regexes: List[Pattern[str]] = [
            compile_origin_regex(pattern) for pattern in allowed_origins if pattern.strip()
        ]

    def token_ok(self, header_token: Optional[str], body_token: Optional[str]) -> Tuple[bool, str]:
        if not self._auth_required:
            return True, ""
        supplied = (header_token or "").strip()
        # Acepta "Bearer <token>" (Authorization) o el token desnudo.
        lowered = supplied.lower()
        if lowered.startswith("bearer "):
            supplied = supplied[7:].strip()
        elif lowered.startswith("token "):
            supplied = supplied[6:].strip()
        if not supplied:
            supplied = (body_token or "").strip()
        if not self._token:
            return False, "El servidor no tiene BRIDGE_TOKEN configurado."
        if not supplied:
            return False, "Falta el token de autenticación."
        # compare_digest exige longitudes seguras y evita comparaciones con timing.
        if not secrets.compare_digest(supplied.encode("utf-8"), self._token.encode("utf-8")):
            return False, "Token inválido."
        return True, ""

    def origin_ok(self, origin: Optional[str]) -> Tuple[bool, str]:
        # Sin Origin → cliente no-navegador (curl, GUI local, pruebas): se
        # permite pasar a la comprobación de token.
        if origin is None:
            return True, ""
        candidate = origin.strip().rstrip("/")
        for regex in self._origin_regexes:
            if regex.match(candidate):
                return True, ""
        return False, f"Origen no autorizado: {origin}"


class RateLimiter:
    """Ventana deslizante de 60 s por clave (IP de origen)."""

    def __init__(self, max_per_minute: int) -> None:
        self.max = max(1, int(max_per_minute))
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, key: str = "local") -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > 60.0:
            hits.popleft()
        if len(hits) >= self.max:
            return False
        hits.append(now)
        return True

    def retry_after(self, key: str = "local") -> int:
        hits = self._hits.get(key)
        if not hits:
            return 1
        now = time.monotonic()
        return max(1, math.ceil(60.0 - (now - hits[0])))
