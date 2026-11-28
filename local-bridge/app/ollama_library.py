"""Catálogo en línea de Ollama y comprobación de versión.

Fuentes oficiales y verificables (solo lectura, sin credenciales ni datos
del usuario):

* ``https://ollama.com/library`` — biblioteca pública de modelos: nombre,
  descripción, capacidades (visión, herramientas, razonamiento,
  embeddings) y variantes de tamaño (``4b``, ``8b``…).
* ``https://api.github.com/repos/ollama/ollama/releases/latest`` — última
  versión publicada de Ollama.

Reglas:

* Consultar el catálogo **no descarga ni instala nada**; instalar un modelo
  es siempre una acción explícita del usuario.
* Sin conexión se conserva la última información válida (caché en el
  directorio de datos) y se indica su antigüedad.
* Los tamaños de descarga de los modelos de la biblioteca se **estiman**
  (cuantización Q4 típica) y se marcan como tales: la página no los publica.
* Se distinguen los modelos nuevos (no vistos en consultas anteriores).
"""

from __future__ import annotations

import html
import json
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from . import paths

log = logging.getLogger("unnexor.ollama_library")

LIBRARY_URL = "https://ollama.com/library?sort=newest"
RELEASES_URL = "https://api.github.com/repos/ollama/ollama/releases/latest"
CACHE_NAME = "ollama_library.json"
TIMEOUT_S = 12.0
USER_AGENT = "UNnexor-catalog/1.0 (+local desktop app)"
MAX_VARIANT_PARAMS_B = 72.0  # variantes mayores no tienen sentido en local
# Estimación de descarga en Q4_K_M: ~0,6 GB por mil millones de parámetros
# más ~0,4 GB de vocabulario/metadatos (coincide con qwen3:4b/8b, llama3.1:8b).
GB_PER_B = 0.6
GB_OVERHEAD = 0.4


@dataclass
class LibraryModel:
    name: str
    description: str = ""
    capabilities: list[str] = field(default_factory=list)
    sizes: list[str] = field(default_factory=list)  # "4b", "8b", "e2b"…
    pulls: str = ""
    first_seen: str = ""  # ISO-8601: primera vez que UNnexor lo vio

    @property
    def is_cloud_only(self) -> bool:
        return "cloud" in self.capabilities and not self.sizes

    @property
    def is_embedding(self) -> bool:
        return "embedding" in self.capabilities


@dataclass
class LibrarySnapshot:
    models: list[LibraryModel] = field(default_factory=list)
    fetched_at: str = ""
    source: str = LIBRARY_URL
    status: str = "unavailable"  # online | cache | unavailable
    error: str = ""
    new_names: list[str] = field(default_factory=list)
    latest_ollama: str = ""


# ---------------------------------------------------------------- parsing
_CARD = re.compile(
    r'<a href="/library/([a-z0-9][a-z0-9._-]*)" class="group w-full', re.I
)
_DESC = re.compile(r'<p class="max-w-lg[^"]*">(.*?)</p>', re.S)
_BADGE = re.compile(
    r'<span\s+class="inline-flex items-center rounded-md ([^"]*)">\s*([^<]+?)\s*</span>'
)
_PULLS = re.compile(r"<span\s*>([\d.,]+[KMB]?)</span>\s*<span[^>]*>&nbsp;Pulls")


def parse_library(page: str) -> list[LibraryModel]:
    """Extrae los modelos de la página de la biblioteca (tolerante a cambios)."""
    starts = [(m.start(), m.group(1)) for m in _CARD.finditer(page)]
    models: list[LibraryModel] = []
    seen: set[str] = set()
    for index, (start, name) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(page)
        block = page[start:end]
        if name in seen:
            continue
        seen.add(name)
        desc_match = _DESC.search(block)
        description = (
            html.unescape(re.sub(r"\s+", " ", desc_match.group(1))).strip()
            if desc_match
            else ""
        )
        capabilities: list[str] = []
        sizes: list[str] = []
        for classes, text in _BADGE.findall(block):
            label = html.unescape(text).strip().lower()
            if "indigo" in classes or "cyan" in classes:
                capabilities.append(label)
            elif "ddf4ff" in classes or "blue" in classes:
                sizes.append(label)
        pulls_match = _PULLS.search(block)
        models.append(
            LibraryModel(
                name=name,
                description=description,
                capabilities=capabilities,
                sizes=sizes,
                pulls=pulls_match.group(1) if pulls_match else "",
            )
        )
    return models


def size_to_params(size: str) -> float | None:
    """``"8b"`` → 8.0 · ``"270m"`` → 0.27 · ``"e4b"`` → 4.0 · ``"8x7b"`` → 56."""
    text = size.strip().lower()
    match = re.fullmatch(r"(?:(\d+)x)?e?(\d+(?:\.\d+)?)([bm])", text)
    if not match:
        return None
    experts = int(match.group(1) or 1)
    value = float(match.group(2)) * experts
    return value / 1000 if match.group(3) == "m" else value


def estimate_size_gb(params_b: float) -> float:
    return round(params_b * GB_PER_B + GB_OVERHEAD, 1)


def catalog_entries(snapshot: LibrarySnapshot) -> list[dict[str, Any]]:
    """Variantes descargables (``nombre:tamaño``) con datos estimados."""
    new = set(snapshot.new_names)
    entries: list[dict[str, Any]] = []
    for model in snapshot.models:
        if model.is_cloud_only or model.is_embedding:
            continue
        for size in model.sizes:
            params = size_to_params(size)
            if params is None or params > MAX_VARIANT_PARAMS_B:
                continue
            size_gb = estimate_size_gb(params)
            entries.append(
                {
                    "name": f"{model.name}:{size}",
                    "size_gb": size_gb,
                    "size_bytes": int(size_gb * (1024**3)),
                    "params_b": params,
                    "family": model.name,
                    "vision": "vision" in model.capabilities,
                    "thinking": "thinking" in model.capabilities,
                    "tools": "tools" in model.capabilities,
                    "description": model.description,
                    "pulls": model.pulls,
                    "estimated": True,
                    "new": model.name in new,
                    "source": f"https://ollama.com/library (consulta {snapshot.fetched_at[:10]})",
                }
            )
    return entries


# ---------------------------------------------------------------- versión
def _version_tuple(text: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", text.split("-")[0])
    return tuple(int(n) for n in numbers[:3]) if numbers else ()


def version_status(installed: str, latest: str) -> tuple[str, str]:
    """(estado, mensaje): ``ok`` | ``update`` | ``unknown``."""
    if not installed:
        return "unknown", "No se pudo leer la versión de Ollama instalada."
    if not latest:
        return "unknown", f"Ollama {installed} (sin datos de la última versión)."
    if _version_tuple(installed) and _version_tuple(installed) < _version_tuple(latest):
        return (
            "update",
            f"Ollama {installed} instalada; hay una versión más reciente "
            f"({latest.lstrip('v')}). Actualízala desde https://ollama.com/download "
            "(UNnexor no la actualiza por su cuenta).",
        )
    return "ok", f"Ollama {installed} está al día."


# ---------------------------------------------------------------- caché
class LibraryCatalog:
    """Consulta, caché y detección de novedades (thread-safe)."""

    def __init__(
        self,
        cache_path: Path | None = None,
        http_get: Callable[..., httpx.Response] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.cache_path = cache_path or paths.data_dir() / CACHE_NAME
        self._get = http_get or httpx.get
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()

    def load_cached(self) -> LibrarySnapshot:
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            models = [LibraryModel(**m) for m in raw.get("models", [])]
            return LibrarySnapshot(
                models=models,
                fetched_at=raw.get("fetched_at", ""),
                status="cache" if models else "unavailable",
                new_names=list(raw.get("new_names", [])),
                latest_ollama=raw.get("latest_ollama", ""),
            )
        except (OSError, ValueError, TypeError):
            return LibrarySnapshot()

    def _save(self, snapshot: LibrarySnapshot) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "fetched_at": snapshot.fetched_at,
            "source": snapshot.source,
            "latest_ollama": snapshot.latest_ollama,
            "new_names": snapshot.new_names,
            "models": [asdict(m) for m in snapshot.models],
        }
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.cache_path)

    def _fetch_text(self, url: str) -> str:
        response = self._get(
            url,
            timeout=TIMEOUT_S,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.text

    def latest_version(self) -> str:
        try:
            response = self._get(
                RELEASES_URL,
                timeout=TIMEOUT_S,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/vnd.github+json",
                },
                follow_redirects=True,
            )
            response.raise_for_status()
            return str(response.json().get("tag_name", "")).lstrip("v")
        except (httpx.HTTPError, ValueError) as exc:
            log.info("No se pudo consultar la última versión de Ollama: %s", exc)
            return ""

    def refresh(self) -> LibrarySnapshot:
        """Consulta en línea; si falla, devuelve la caché anterior."""
        with self._lock:
            previous = self.load_cached()
            try:
                models = parse_library(self._fetch_text(LIBRARY_URL))
                if len(models) < 10:
                    raise ValueError(
                        "la página de la biblioteca cambió de formato "
                        f"({len(models)} modelos reconocidos)"
                    )
            except (httpx.HTTPError, ValueError) as exc:
                log.info("Catálogo de Ollama sin actualizar: %s", exc)
                previous.error = str(exc)
                if previous.models:
                    previous.status = "cache"
                return previous
            now = self._clock().isoformat(timespec="seconds")
            known = {m.name: m.first_seen for m in previous.models}
            for model in models:
                model.first_seen = known.get(model.name) or now
            # "Nuevo" = no estaba en la consulta anterior (si había una).
            new_names = [m.name for m in models if m.name not in known] if known else []
            snapshot = LibrarySnapshot(
                models=models,
                fetched_at=now,
                status="online",
                new_names=new_names,
                latest_ollama=self.latest_version() or previous.latest_ollama,
            )
            try:
                self._save(snapshot)
            except OSError as exc:  # pragma: no cover - disco lleno, permisos
                log.warning("No se pudo guardar la caché del catálogo: %s", exc)
            return snapshot


_default: LibraryCatalog | None = None


def default_catalog() -> LibraryCatalog:
    global _default
    if _default is None:
        _default = LibraryCatalog()
    return _default
