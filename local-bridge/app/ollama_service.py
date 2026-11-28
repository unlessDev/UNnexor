"""Arranque y parada de Ollama con propiedad explícita.

Reglas (prompt maestro §9.2):

* Si Ollama YA estaba en ejecución al abrir UNnexor, no se toca: ni se
  reinicia ni se detiene al salir (es un servicio global del usuario).
* Si no estaba en ejecución y la opción ``OLLAMA_AUTOSTART`` está activa,
  UNnexor lanza ``ollama serve`` (sin ventana) y anota el PID. Solo ese
  proceso —y los runners que él mismo haya creado— se detiene al cerrar.
* Nunca se busca ni se mata ningún otro proceso por nombre.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx

log = logging.getLogger("unnexor.ollama_service")

START_TIMEOUT_S = 20.0


@dataclass
class OllamaStartup:
    """Resultado de la comprobación de arranque (para la interfaz y logs)."""

    reachable: bool
    version: str = ""
    started_by_us: bool = False
    executable: str = ""
    detail: str = ""


def find_executable() -> str:
    """Ruta de ``ollama.exe`` (PATH o instalación por usuario) o ``""``."""
    found = shutil.which("ollama")
    if found:
        return found
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        candidates = [
            Path(local) / "Programs" / "Ollama" / "ollama.exe",
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
            / "Ollama"
            / "ollama.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    return ""


def is_local_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


class OllamaSupervisor:
    """Comprueba Ollama al iniciar y, si lo arrancó, lo detiene al salir."""

    def __init__(
        self,
        base_url: str,
        autostart: bool = True,
        *,
        find: Callable[[], str] = find_executable,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        http_get: Callable[..., httpx.Response] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.autostart = autostart
        self._find = find
        self._popen = popen
        self._get = http_get or httpx.get
        self._process: subprocess.Popen | None = None
        self.startup: OllamaStartup | None = None

    # ------------------------------------------------------------------
    def probe(self, timeout: float = 2.5) -> tuple[bool, str]:
        try:
            response = self._get(f"{self.base_url}/api/version", timeout=timeout)
            if response.status_code == 200:
                return True, str(response.json().get("version", ""))
        except (httpx.HTTPError, ValueError):
            pass
        return False, ""

    def ensure_running(self) -> OllamaStartup:
        reachable, version = self.probe()
        if reachable:
            self.startup = OllamaStartup(True, version, detail="ya estaba en ejecución")
            return self.startup
        if not self.autostart:
            self.startup = OllamaStartup(
                False, detail="no responde (arranque automático desactivado)"
            )
            return self.startup
        if not is_local_url(self.base_url):
            self.startup = OllamaStartup(
                False, detail=f"no responde en {self.base_url} (servidor remoto)"
            )
            return self.startup
        exe = self._find()
        if not exe:
            self.startup = OllamaStartup(
                False, detail="no está instalado (https://ollama.com/download)"
            )
            return self.startup
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            self._process = self._popen(
                [exe, "serve"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
                close_fds=True,
            )
        except OSError as exc:
            self.startup = OllamaStartup(
                False, executable=exe, detail=f"no se pudo iniciar: {exc}"
            )
            return self.startup
        log.info("Ollama iniciado por UNnexor (PID %s).", self._process.pid)
        deadline = time.monotonic() + START_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                # Terminó enseguida: casi siempre porque otro Ollama ocupa el
                # puerto (la app de bandeja arrancando a la vez). No es nuestro.
                self._process = None
                reachable, version = self.probe()
                self.startup = OllamaStartup(
                    reachable,
                    version,
                    executable=exe,
                    detail=(
                        "otra instancia tomó el puerto"
                        if reachable
                        else "el proceso terminó al arrancar"
                    ),
                )
                return self.startup
            reachable, version = self.probe(timeout=1.0)
            if reachable:
                self.startup = OllamaStartup(True, version, True, exe, "iniciado")
                return self.startup
            time.sleep(0.4)
        self.startup = OllamaStartup(
            False, started_by_us=True, executable=exe, detail="no respondió a tiempo"
        )
        return self.startup

    @property
    def owns_process(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def stop(self, timeout: float = 5.0) -> bool:
        """Detiene SOLO el ``ollama serve`` que arrancó UNnexor."""
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return False
        log.info("Deteniendo el Ollama iniciado por UNnexor (PID %s).", process.pid)
        if sys.platform == "win32":
            # /T: también los runners hijos de ESE proceso (llama-server).
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
        else:  # pragma: no cover - plataformas no Windows
            process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:  # pragma: no cover
            process.kill()
        return True
