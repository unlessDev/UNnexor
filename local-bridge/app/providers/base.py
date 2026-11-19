"""Base común de los adaptadores de proveedores."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class ProviderError(RuntimeError):
    """Error de proveedor con mensaje orientado al usuario."""

    # kind: auth (credenciales), quota (límites/créditos), network,
    #        payment (saldo insuficiente), general
    def __init__(self, message: str, kind: str = "general") -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind


@dataclass(frozen=True)
class ProviderModel:
    """Modelo devuelto por el catálogo dinámico de un proveedor."""

    id: str
    provider: str
    owned_by: str = ""
    context_window: Optional[int] = None  # solo si la API lo informa
    raw: dict | None = None
