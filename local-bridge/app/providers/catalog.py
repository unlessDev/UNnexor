"""Catálogo unificado de proveedores (local + remotos) para la interfaz y
el enrutador.

Estados honestos: una clave aceptada por el catálogo de modelos demuestra
que la clave es válida, **no** que la cuenta tenga saldo ni acceso a todos
los modelos; eso se confirma al usar cada modelo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .access import AccessTier
from .presets import PRESETS


class ConnectionStatus(str, Enum):
    LOCAL = "local"
    NOT_CONNECTED = "not_connected"
    KEY_VALID = "key_valid"  # el catálogo respondió con esta clave
    MODEL_CONFIRMED = "model_confirmed"  # al menos una respuesta real
    UNVERIFIED = "unverified"  # no se pudo comprobar (red, servicio caído)
    FAILED = "failed"  # la última verificación falló (clave, saldo…)

    @property
    def label(self) -> str:
        return {
            ConnectionStatus.LOCAL: "Ejecución local",
            ConnectionStatus.NOT_CONNECTED: "No conectado",
            ConnectionStatus.KEY_VALID: "Clave válida · acceso a modelos sin confirmar",
            ConnectionStatus.MODEL_CONFIRMED: "En uso con éxito",
            ConnectionStatus.UNVERIFIED: "No se pudo verificar el acceso",
            ConnectionStatus.FAILED: "La última verificación falló",
        }[self]


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    label: str
    access: AccessTier
    capabilities: frozenset[str]
    local: bool
    protocol: str
    description: str = ""
    docs: str = ""
    access_note: str = ""  # texto documentado (límites, créditos…)
    verified_at: str = ""
    key_format: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def needs_key(self) -> bool:
        return not self.local


OLLAMA = ProviderInfo(
    id="ollama",
    label="Ollama (local)",
    access=AccessTier.LOCAL,
    capabilities=frozenset({"chat", "vision", "embeddings"}),
    local=True,
    protocol="ollama",
    description="Modelos ejecutados en tu equipo; la visión y los embeddings "
    "dependen del modelo instalado.",
    docs="https://ollama.com/library",
    access_note="Sin facturación por API; consume CPU/GPU, RAM y disco.",
)


def provider_catalog() -> list[ProviderInfo]:
    items = [OLLAMA]
    for preset in PRESETS:
        items.append(
            ProviderInfo(
                id=preset["id"],
                label=preset["label"],
                access=AccessTier(preset.get("access", AccessTier.UNKNOWN.value)),
                capabilities=frozenset(preset.get("capabilities", ["chat"])),
                local=False,
                protocol=preset.get("protocol", "openai"),
                description=preset.get("description", ""),
                docs=preset.get("docs", ""),
                access_note=preset.get("kind", ""),
                verified_at=preset.get("verified_at", ""),
                key_format=preset.get("key_format", ""),
            )
        )
    return items


def get_provider(provider_id: str) -> ProviderInfo | None:
    return next((p for p in provider_catalog() if p.id == provider_id), None)


def connection_status(
    provider: ProviderInfo,
    *,
    has_key: bool,
    confirmed: bool = False,
    last_error_kind: str | None = None,
) -> ConnectionStatus:
    if provider.local:
        return ConnectionStatus.LOCAL
    if not has_key:
        return ConnectionStatus.NOT_CONNECTED
    if last_error_kind in ("auth", "payment"):
        return ConnectionStatus.FAILED
    if last_error_kind == "network":
        return ConnectionStatus.UNVERIFIED
    return ConnectionStatus.MODEL_CONFIRMED if confirmed else ConnectionStatus.KEY_VALID
