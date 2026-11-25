"""Modalidades de acceso y coste (vocabulario común de catálogos).

Se distinguen expresamente categorías que NO son equivalentes: gratuito
con límites no es lo mismo que créditos promocionales ni que ejecución
local. Ningún valor se presenta como permanente: todos los catálogos
llevan fecha de verificación y enlace a la documentación oficial.
"""

from __future__ import annotations

from enum import Enum


class AccessTier(str, Enum):
    LOCAL = "local"
    FREE = "free"
    FREE_LIMITED = "free_limited"
    CREDITS = "credits"
    PAY_AS_YOU_GO = "pay_as_you_go"
    SUBSCRIPTION = "subscription"
    MIXED = "mixed"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return _LABELS[self]

    @property
    def may_cost_money(self) -> bool:
        """¿Usarlo puede generar cargos (o consumir saldo/créditos)?"""
        return self in (
            AccessTier.CREDITS,
            AccessTier.PAY_AS_YOU_GO,
            AccessTier.SUBSCRIPTION,
            AccessTier.MIXED,
            AccessTier.UNKNOWN,
        )

    @property
    def is_remote(self) -> bool:
        return self is not AccessTier.LOCAL


_LABELS = {
    AccessTier.LOCAL: "Ejecución local (sin facturación por API)",
    AccessTier.FREE: "Gratuito sin costo",
    AccessTier.FREE_LIMITED: "Gratuito con límites de uso",
    AccessTier.CREDITS: "Créditos iniciales o promocionales",
    AccessTier.PAY_AS_YOU_GO: "Pago por consumo",
    AccessTier.SUBSCRIPTION: "Suscripción",
    AccessTier.MIXED: "Mixto: modelos gratuitos con límites y de pago",
    AccessTier.UNKNOWN: "Estado del plan desconocido",
}

PLAN_DISCLAIMER = (
    "Planes, precios y límites pueden cambiar sin aviso: consulta la "
    "documentación oficial del proveedor antes de depender de ellos."
)
