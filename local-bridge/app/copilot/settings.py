"""Ajustes del copiloto (JSON sin secretos en ``copilot_dir()``)."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .. import paths
from ..routing.router import RoutingPolicy, Strategy, Task


@dataclass
class CopilotSettings:
    # Voz
    stt_engine: str = "faster-whisper:base"
    stt_language: str = "es"  # "" = detección automática
    capture_system: bool = True
    capture_microphone: bool = False
    system_device: str = ""  # "" = predeterminado
    microphone_device: str = ""
    show_partials: bool = True
    # Asistencia
    active_profile: str = ""
    auto_answer: bool = True
    answer_microphone_questions: bool = False
    sensitivity: str = "medium"  # low | medium | high
    use_knowledge: bool = True
    # Ventanas de escucha (ver copilot/listening.py): continuous | timed | manual
    listen_mode: str = "continuous"
    listen_seconds: int = 30
    # Traducción
    translation_source: str = ""  # "" = automático
    translation_target: str = "en"
    # Desafíos
    periodic_interval_s: int = 15
    # Enrutamiento
    strategy: str = Strategy.LOCAL_FIRST.value
    pinned: dict[str, str] = field(default_factory=dict)  # task → "prov:model"
    remote_models: dict[str, list[str]] = field(default_factory=dict)
    # Temperatura por tarea (Recomendar configuración); vacío = la de cada flujo.
    task_temperature: dict[str, float] = field(default_factory=dict)
    # Ventana flotante y atajos
    overlay_opacity: int = 92
    overlay_on_top: bool = True
    hotkey_toggle_capture: str = "Ctrl+Alt+Shift+C"
    hotkey_ask_manual: str = "Ctrl+Alt+Shift+A"
    hotkey_screen_challenge: str = "Ctrl+Alt+Shift+S"
    hotkey_listen: str = "Ctrl+Alt+Shift+L"  # iniciar / terminar la escucha
    hotkey_listen_cancel: str = "Ctrl+Alt+Shift+X"  # descartar la escucha
    # Mediciones de rendimiento consentidas: "stt:base", "llm:qwen3:8b"…
    benchmarks: dict[str, dict[str, Any]] = field(default_factory=dict)

    def measured_stt_call_s(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for key, value in self.benchmarks.items():
            call = value.get("call_s") if isinstance(value, dict) else None
            if key.startswith("stt:") and isinstance(call, int | float):
                out[key[4:]] = float(call)
        return out

    def validate(self) -> None:
        if self.sensitivity not in ("low", "medium", "high"):
            raise ValueError("Sensibilidad no válida")
        Strategy(self.strategy)
        if not 5 <= int(self.periodic_interval_s) <= 600:
            raise ValueError("El intervalo debe estar entre 5 y 600 s")
        if not 40 <= int(self.overlay_opacity) <= 100:
            raise ValueError("Opacidad fuera de rango")
        if self.listen_mode not in ("continuous", "timed", "manual"):
            raise ValueError("Modo de escucha no válido")
        if not 5 <= int(self.listen_seconds) <= 600:
            raise ValueError("La duración de la escucha debe estar entre 5 y 600 s")
        for task in self.pinned:
            Task(task)

    def policy(
        self,
        remote_allowed: frozenset[str],
        cost_allowed: frozenset[str],
        documents_allowed: frozenset[str],
    ) -> RoutingPolicy:
        return RoutingPolicy(
            strategy=Strategy(self.strategy),
            remote_allowed=remote_allowed,
            cost_allowed=cost_allowed,
            documents_allowed=documents_allowed,
            pinned={Task(k): v for k, v in self.pinned.items()},
            selected=frozenset(self.pinned.values())
            | frozenset(
                f"{prov}:{model}"
                for prov, models in self.remote_models.items()
                for model in models
            ),
        )


class SettingsRepository:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (paths.copilot_dir() / "settings.json")
        self._lock = threading.Lock()

    def load(self) -> CopilotSettings:
        try:
            data: Any = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return CopilotSettings()
        known = CopilotSettings.__dataclass_fields__
        settings = CopilotSettings(**{k: v for k, v in data.items() if k in known})
        try:
            settings.validate()
        except (ValueError, TypeError):
            return CopilotSettings()
        return settings

    def save(self, settings: CopilotSettings) -> None:
        settings.validate()
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(asdict(settings), ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            tmp.replace(self._path)
