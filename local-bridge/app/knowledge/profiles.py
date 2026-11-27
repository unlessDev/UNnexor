"""Perfiles de contexto del copiloto (JSON por perfil, sin secretos)."""

from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from .. import paths

MAX_TEXT_FIELD = 20_000


class Tone(str, Enum):
    PROFESSIONAL = "professional"
    TECHNICAL = "technical"
    CONCISE = "concise"
    EXPLANATORY = "explanatory"
    CUSTOM = "custom"

    @property
    def label(self) -> str:
        return {
            Tone.PROFESSIONAL: "Profesional",
            Tone.TECHNICAL: "Técnico",
            Tone.CONCISE: "Conciso",
            Tone.EXPLANATORY: "Explicativo",
            Tone.CUSTOM: "Personalizado",
        }[self]

    @property
    def instruction(self) -> str:
        return {
            Tone.PROFESSIONAL: "Usa un tono profesional, claro y cordial.",
            Tone.TECHNICAL: "Usa un tono técnico y preciso, con terminología correcta.",
            Tone.CONCISE: "Sé muy conciso: ve al grano sin rodeos.",
            Tone.EXPLANATORY: "Explica con claridad, con ejemplos cuando ayuden.",
            Tone.CUSTOM: "",
        }[self]


class Detail(str, Enum):
    BRIEF = "brief"
    NORMAL = "normal"
    DETAILED = "detailed"

    @property
    def label(self) -> str:
        return {"brief": "Breve", "normal": "Normal", "detailed": "Detallado"}[
            self.value
        ]

    @property
    def instruction(self) -> str:
        return {
            Detail.BRIEF: "Responde en 2–3 frases que se puedan decir en voz alta.",
            Detail.NORMAL: "Responde en un párrafo corto o 3–5 viñetas.",
            Detail.DETAILED: "Responde de forma completa y estructurada.",
        }[self]


@dataclass
class ContextProfile:
    name: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    role: str = ""  # puesto / actividad del usuario
    about: str = ""  # experiencia, formación, habilidades (texto libre)
    goal: str = ""  # objetivo de la conversación
    company: str = ""  # empresa o contexto de la contraparte
    language: str = "es"  # idioma de las respuestas
    tone: Tone = Tone.PROFESSIONAL
    custom_tone: str = ""
    detail: Detail = Detail.BRIEF
    glossary: str = ""  # términos para el reconocimiento de voz
    allow_remote_documents: bool = False  # enviar extractos a remotos
    created_at: str = field(default_factory=lambda: _now())
    updated_at: str = field(default_factory=lambda: _now())

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("El perfil necesita un nombre.")
        if not re.fullmatch(r"[a-f0-9]{6,32}", self.id):
            raise ValueError("Identificador de perfil no válido.")
        for name in ("role", "about", "goal", "company", "custom_tone", "glossary"):
            if len(getattr(self, name)) > MAX_TEXT_FIELD:
                raise ValueError(f"El campo '{name}' es demasiado largo.")

    def tone_instruction(self) -> str:
        if self.tone is Tone.CUSTOM:
            return self.custom_tone.strip()
        return self.tone.instruction

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tone"] = self.tone.value
        data["detail"] = self.detail.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextProfile:
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in data.items() if k in known}
        clean["tone"] = Tone(clean.get("tone", Tone.PROFESSIONAL.value))
        clean["detail"] = Detail(clean.get("detail", Detail.BRIEF.value))
        clean["allow_remote_documents"] = bool(clean.get("allow_remote_documents"))
        return cls(**clean)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProfileStore:
    def __init__(self, root: Path | None = None) -> None:
        self._root = root or (paths.copilot_dir() / "profiles")
        self._lock = threading.Lock()

    def _path(self, profile_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{6,32}", profile_id):
            raise ValueError("Identificador de perfil no válido.")
        return self._root / f"{profile_id}.json"

    def list(self) -> list[ContextProfile]:
        if not self._root.is_dir():
            return []
        profiles = []
        for file in sorted(self._root.glob("*.json")):
            try:
                profiles.append(
                    ContextProfile.from_dict(
                        json.loads(file.read_text(encoding="utf-8"))
                    )
                )
            except (ValueError, TypeError, json.JSONDecodeError, OSError):
                continue
        return sorted(profiles, key=lambda p: p.name.lower())

    def get(self, profile_id: str) -> ContextProfile | None:
        try:
            data = json.loads(self._path(profile_id).read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            return None
        return ContextProfile.from_dict(data)

    def save(self, profile: ContextProfile) -> ContextProfile:
        profile.validate()
        profile.updated_at = _now()
        with self._lock:
            self._root.mkdir(parents=True, exist_ok=True)
            target = self._path(profile.id)
            tmp = target.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(profile.to_dict(), ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            tmp.replace(target)
        return profile

    def delete(self, profile_id: str) -> None:
        with self._lock:
            try:
                self._path(profile_id).unlink()
            except FileNotFoundError:
                pass
