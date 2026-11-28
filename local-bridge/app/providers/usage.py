"""Historial local de uso por proveedor y tarea.

Solo contadores (peticiones, errores, segundos de audio, tokens o
caracteres) y los últimos encabezados de límites que informó el proveedor.
**Nunca** contenido, prompts, transcripciones ni claves. Retención: 90 días.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .. import paths

log = logging.getLogger("unnexor.providers.usage")

_COUNTERS = (
    "requests",
    "errors",
    "audio_seconds",
    "input_tokens",
    "output_tokens",
    "chars_in",
    "chars_out",
)


def default_path() -> Path:
    return paths.copilot_dir() / "usage.json"


class UsageLedger:
    def __init__(self, path: Path | None = None, retention_days: int = 90) -> None:
        self._path = path or default_path()
        self._retention = retention_days
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("days"), dict):
                data.setdefault("rate_limits", {})
                return data
        except FileNotFoundError:
            pass
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("usage.json ilegible (%s); se reinicia.", exc)
        return {"version": 1, "days": {}, "rate_limits": {}}

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        cutoff = (date.today() - timedelta(days=self._retention)).isoformat()
        data["days"] = {k: v for k, v in data["days"].items() if k >= cutoff}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self._path)

    # ------------------------------------------------------------------ #
    def record(
        self,
        provider_id: str,
        task: str,
        *,
        requests: int = 1,
        errors: int = 0,
        audio_seconds: float = 0.0,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        chars_in: int = 0,
        chars_out: int = 0,
        rate_limits: dict[str, str] | None = None,
        day: date | None = None,
    ) -> None:
        values = {
            "requests": requests,
            "errors": errors,
            "audio_seconds": round(audio_seconds, 2),
            "input_tokens": input_tokens or 0,
            "output_tokens": output_tokens or 0,
            "chars_in": chars_in,
            "chars_out": chars_out,
        }
        key = (day or date.today()).isoformat()
        with self._lock:
            data = self._read()
            bucket = data["days"].setdefault(key, {}).setdefault(provider_id, {})
            counters = bucket.setdefault(task, {name: 0 for name in _COUNTERS})
            for name, value in values.items():
                counters[name] = round(counters.get(name, 0) + value, 2)
            if rate_limits:
                data["rate_limits"][provider_id] = {
                    **{k.lower(): str(v) for k, v in rate_limits.items()},
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                }
            try:
                self._write(data)
            except OSError as exc:  # el uso nunca debe romper una sesión
                log.warning("No se pudo guardar el historial de uso: %s", exc)

    def summary(self, days: int = 30) -> dict[str, dict[str, float]]:
        """Totales por proveedor en los últimos ``days`` días."""
        cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
        totals: dict[str, dict[str, float]] = {}
        with self._lock:
            data = self._read()
        for day, providers in data["days"].items():
            if day < cutoff:
                continue
            for provider_id, tasks in providers.items():
                target = totals.setdefault(provider_id, {n: 0 for n in _COUNTERS})
                for counters in tasks.values():
                    for name in _COUNTERS:
                        target[name] = round(target[name] + counters.get(name, 0), 2)
        return totals

    def by_task(self, provider_id: str, days: int = 30) -> dict[str, dict[str, float]]:
        cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
        out: dict[str, dict[str, float]] = {}
        with self._lock:
            data = self._read()
        for day, providers in data["days"].items():
            if day < cutoff:
                continue
            for task, counters in providers.get(provider_id, {}).items():
                target = out.setdefault(task, {n: 0 for n in _COUNTERS})
                for name in _COUNTERS:
                    target[name] = round(target[name] + counters.get(name, 0), 2)
        return out

    def rate_limits(self, provider_id: str) -> dict[str, str]:
        with self._lock:
            return dict(self._read()["rate_limits"].get(provider_id, {}))

    def clear(self) -> None:
        with self._lock:
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass


def extract_rate_limit_headers(headers: Any) -> dict[str, str]:
    """Encabezados de límites (Groq ``x-ratelimit-*``, Anthropic
    ``anthropic-ratelimit-*``, ``retry-after``)."""
    out: dict[str, str] = {}
    try:
        items = headers.items()
    except AttributeError:
        return out
    for key, value in items:
        name = str(key).lower()
        if "ratelimit" in name or name == "retry-after":
            out[name] = str(value)
    return out
