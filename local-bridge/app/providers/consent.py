"""Consentimientos explícitos del usuario por proveedor.

Ámbitos:

* ``remote_data`` — el usuario aceptó enviar contenido (audio, texto,
  capturas) a este servicio externo.
* ``costs`` — el usuario aceptó que su uso puede generar cargos o consumir
  saldo/créditos.
* ``documents`` — el usuario aceptó enviar extractos de sus documentos
  personales (perfil/base de conocimiento) a este servicio.

El enrutador nunca usa un proveedor remoto sin ``remote_data`` ni uno que
puede costar dinero sin ``costs``. Se guarda en ``consents.json`` (sin
secretos) y se puede revocar en cualquier momento.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from .. import paths

SCOPES = ("remote_data", "costs", "documents")


def default_path() -> Path:
    return paths.data_dir() / "consents.json"


class ConsentStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or default_path()
        self._lock = threading.Lock()

    def _read(self) -> dict[str, dict[str, str]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {
                    str(k): {str(s): str(t) for s, t in v.items() if s in SCOPES}
                    for k, v in data.items()
                    if isinstance(v, dict)
                }
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            pass
        return {}

    def _write(self, data: dict[str, dict[str, str]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self._path)

    def grant(self, provider_id: str, scope: str) -> None:
        if scope not in SCOPES:
            raise ValueError(f"Ámbito de consentimiento desconocido: {scope}")
        with self._lock:
            data = self._read()
            data.setdefault(provider_id, {})[scope] = datetime.now(
                timezone.utc
            ).isoformat()
            self._write(data)

    def revoke(self, provider_id: str, scope: str | None = None) -> None:
        with self._lock:
            data = self._read()
            if scope is None:
                data.pop(provider_id, None)
            else:
                data.get(provider_id, {}).pop(scope, None)
            self._write(data)

    def has(self, provider_id: str, scope: str) -> bool:
        with self._lock:
            return scope in self._read().get(provider_id, {})

    def providers_with(self, scope: str) -> frozenset[str]:
        with self._lock:
            return frozenset(p for p, s in self._read().items() if scope in s)

    def clear(self) -> None:
        with self._lock:
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass
