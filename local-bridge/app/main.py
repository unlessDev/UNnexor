"""Punto de entrada del puente local de Extdator.

Modos:
  * Puente completo (por defecto): servidor HTTP (hilo uvicorn) + panel
    flotante PySide6 (hilo principal) + worker de generación (QThread).
  * ``--server-only``: solo el servidor HTTP, sin GUI.
  * ``--check``: auto-verificación headless (arranca servidor, prueba
    endpoints y termina con código de salida).

El panel flotante nunca bloquea su hilo con Ollama: la generación vive en el
``GenerationWorker`` y el streaming llega a la GUI por señales Qt.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, paths
from .config import LOG_DIR, Config, ConfigError, load_config
from .env_setup import clear_env_secrets, ensure_env_file
from .ollama_client import OLLAMA_HEALTH_TIMEOUT, OllamaClient
from .pairing import PairingManager
from .queue_manager import Bus, RuntimeState
from .server import create_app
from .settings_store import SettingsStore

log = logging.getLogger("extdator.main")

# /health consulta Ollama de forma síncrona. El cliente debe esperar más
# que ese sondeo para no descartar una respuesta 200 válida y lenta.
HEALTH_REQUEST_TIMEOUT = OLLAMA_HEALTH_TIMEOUT + 3.0


def setup_logging(cfg: Config) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "bridge.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(cfg.log_level)
    root.addHandler(file_handler)
    # En el ejecutable windowed no hay consola: un StreamHandler sobre
    # sys.stderr=None fallaría al emitir.
    if not paths.is_frozen() and sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)


def report_error(message: str) -> None:
    """Muestra un error al usuario aunque no haya consola."""
    if paths.is_frozen() and sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "Extdator", 0x10)
            return
        except Exception:  # pragma: no cover - fallback defensivo
            pass
    print(message)


def purge_secrets() -> int:
    """Elimina credenciales de proveedores y secretos del ``.env``.

    Se ejecuta antes de cargar la configuración para funcionar incluso con
    un ``.env`` ausente o dañado. En modo empaquetado no imprime nada: el
    desinstalador lo invoca con la ventana oculta.
    """
    from .providers import manager

    env_path = paths.env_file()
    providers = manager.purge_all_keys()
    env_result = clear_env_secrets(env_path)
    if not paths.is_frozen():
        print(f"Credenciales de proveedores eliminadas ({len(providers)} revisadas).")
        if env_result["found"]:
            if env_result["token_cleared"]:
                print(f"Token del puente eliminado de {env_path}.")
            if env_result["keys_removed"]:
                print(
                    "Claves API retiradas del .env: "
                    + ", ".join(env_result["keys_removed"])
                )
            if env_result["error"]:
                print(f"No se pudo reescribir {env_path}: {env_result['error']}")
        else:
            print(f"No existe {env_path}; no había secretos en el archivo.")
    return 1 if env_result["error"] else 0


def start_uvicorn_thread(
    cfg: Config,
    bus: Bus,
    state: RuntimeState,
    ollama: OllamaClient,
    settings_store: SettingsStore | None = None,
    pairing: PairingManager | None = None,
):
    """Arranca uvicorn en un hilo daemon; devuelve (hilo, servidor)."""
    import uvicorn

    app = create_app(
        cfg, bus, state, ollama, settings_store=settings_store, pairing=pairing
    )
    uconfig = uvicorn.Config(
        app,
        host=cfg.host,
        port=cfg.port,
        log_level="warning",
        access_log=False,
        log_config=None,
    )
    server = uvicorn.Server(uconfig)

    def _serve() -> None:
        try:
            server.run()
        except Exception as exc:  # uvicorn puede fallar al arrancar (puerto en uso)
            log.error("El servidor HTTP no arrancó: %s", exc)

    thread = threading.Thread(target=_serve, name="uvicorn", daemon=True)
    thread.start()
    return thread, server


def wait_for_health(cfg: Config, timeout: float = 30.0) -> dict | None:
    """Espera a que /health responda; devuelve el JSON o None.

    En un equipo recién instalado, Defender y la carga inicial de módulos
    pueden retrasar el arranque más de 12 s; el auto-chequeo debe tolerarlo.
    La espera por petición cubre el sondeo de Ollama que hace /health.
    """
    import httpx

    deadline = time.monotonic() + timeout
    url = f"http://{cfg.host}:{cfg.port}/health"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=HEALTH_REQUEST_TIMEOUT)
            if response.status_code == 200:
                return response.json()
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    return None


def port_in_use(cfg: Config) -> tuple[bool, str]:
    import httpx

    try:
        response = httpx.get(
            f"http://{cfg.host}:{cfg.port}/health", timeout=HEALTH_REQUEST_TIMEOUT
        )
        body = response.json() if response.status_code == 200 else {}
        if body.get("app") == "extdator-bridge":
            return True, "extdator-bridge"
        return True, "otra-aplicacion"
    except httpx.HTTPError:
        return False, ""


def run_server_only(
    cfg: Config, bus: Bus, state: RuntimeState, ollama: OllamaClient
) -> int:
    """Modo headless: uvicorn en el hilo principal (gestiona Ctrl+C)."""
    import uvicorn

    app = create_app(cfg, bus, state, ollama)
    uconfig = uvicorn.Config(
        app, host=cfg.host, port=cfg.port, log_level="info", access_log=False
    )
    # ``uvicorn.run`` espera una aplicación ASGI, no un ``Config``: pasarle
    # el ``Config`` arrancaba en 127.0.0.1:8000 con una "app" no invocable.
    uvicorn.Server(uconfig).run()
    return 0


def write_self_check_report(results: dict, ok: bool) -> Path | None:
    """Escribe un informe legible del auto-chequeo para soporte y builds.

    En un ejecutable sin consola el informe es la única salida visible, y
    permite validar rutas de instalación desde el proceso empaquetado.
    """
    report = {
        "app": "extdator-bridge",
        "version": __version__,
        "frozen": paths.is_frozen(),
        "data_dir": str(paths.data_dir()),
        "env_file": str(paths.env_file()),
        "logs_dir": str(paths.logs_dir()),
        "ok": ok,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    try:
        paths.ensure_data_dir()
        target = paths.data_dir() / "self-check.json"
        target.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return target
    except OSError as exc:
        log.warning("No se pudo escribir el informe de auto-chequeo: %s", exc)
        return None


def run_check(cfg: Config, bus: Bus, state: RuntimeState, ollama: OllamaClient) -> int:
    """Auto-verificación: arranca el servidor, prueba endpoints y termina."""
    import httpx

    thread, server = start_uvicorn_thread(cfg, bus, state, ollama)
    body = wait_for_health(cfg)
    results: dict = {"health": None, "selection": None, "clear": None}
    if body is not None:
        results["health"] = body
        headers = {"Authorization": f"Bearer {cfg.token}"} if cfg.token else {}
        url = f"http://{cfg.host}:{cfg.port}"
        try:
            r = httpx.post(
                url + "/api/selection",
                json={
                    "text": "prueba de self-check",
                    "source_url": "self-check",
                    "timestamp": "",
                },
                headers=headers,
                timeout=5,
            )
            results["selection"] = {"status": r.status_code, "body": r.json()}
            r = httpx.post(url + "/api/clear", headers=headers, timeout=5)
            results["clear"] = {"status": r.status_code, "body": r.json()}
        except httpx.HTTPError as exc:
            results["selection"] = {"error": str(exc)}
    else:
        in_use, who = port_in_use(cfg)
        results["error"] = (
            f"El servidor no respondió. ¿Puerto ocupado por {who}?"
            if in_use
            else "El servidor no respondió a tiempo."
        )
    server.should_exit = True
    thread.join(timeout=5)
    # Componentes del copiloto (informativo: su ausencia no invalida el
    # puente, pero el build completo debe incluirlos).
    from .copilot.components import check_components

    results["copilot_components"] = check_components()
    ok = bool(
        results.get("health")
        and results["selection"]
        and results["selection"].get("status") == 202
    )
    report_path = write_self_check_report(results, ok)
    if not paths.is_frozen():
        print(results)
        if report_path:
            print(f"Informe de auto-chequeo: {report_path}")
    return 0 if ok else 1


def run_bridge(
    cfg: Config,
    bus: Bus,
    state: RuntimeState,
    ollama: OllamaClient,
    settings_store: SettingsStore,
    pairing: PairingManager,
) -> int:
    """Modo completo: servidor HTTP en hilo + GUI PySide6 en el hilo principal."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import QApplication

    in_use, who = port_in_use(cfg)
    if in_use:
        if who == "extdator-bridge":
            if paths.is_frozen():
                report_error("Extdator ya se está ejecutando.")
            else:
                report_error(
                    f"Extdator ya se está ejecutando (puerto {cfg.port}). Usa "
                    "scripts\\stop.ps1 para detenerlo."
                )
            return 3
        if paths.is_frozen():
            report_error(
                f"El puerto {cfg.port} está ocupado por otra aplicación. "
                "Ciérrala y vuelve a abrir Extdator."
            )
        else:
            report_error(
                f"El puerto {cfg.port} está ocupado por otra aplicación. "
                "Cambia BRIDGE_PORT en local-bridge\\.env o detén ese proceso."
            )
        return 2

    thread, server = start_uvicorn_thread(
        cfg, bus, state, ollama, settings_store, pairing
    )
    body = wait_for_health(cfg)
    if body is None:
        if paths.is_frozen():
            report_error(
                "No se pudo iniciar el servidor local de Extdator. "
                "Revisa el registro en la carpeta de datos de la aplicación."
            )
        else:
            report_error(
                f"El servidor HTTP no arrancó en {cfg.host}:{cfg.port}. "
                "Revisa logs/bridge.log"
            )
        server.should_exit = True
        thread.join(timeout=3)
        return 2
    log.info("Servidor HTTP activo en http://%s:%d", cfg.host, cfg.port)

    app = QApplication(sys.argv)
    app.setApplicationName("Extdator")
    app.setOrganizationName("Extdator")
    app.setStyle("Fusion")
    icon_path = paths.resource_path("assets/extdator.ico")
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    font = QFont()
    font.setFamilies(["Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier New"])
    font.setPointSize(10)
    app.setFont(font)

    # Evitar iconos Borland/Windows clásicos en menús contextuales de Qt.
    app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)

    from .desktop.window import BridgeWindow

    window = BridgeWindow(
        cfg, bus, state, ollama, settings_store=settings_store, pairing=pairing
    )
    window.show()

    def _shutdown() -> None:
        window.stop_workers()
        server.should_exit = True
        thread.join(timeout=5)
        log.info("Puente detenido correctamente.")

    app.aboutToQuit.connect(_shutdown)
    return app.exec()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="extdator-bridge",
        description="Puente local entre la extensión Extdator y Ollama.",
    )
    parser.add_argument(
        "--server-only",
        action="store_true",
        help="Ejecuta solo el servidor HTTP, sin panel flotante.",
    )
    parser.add_argument(
        "--check", action="store_true", help="Auto-verificación headless y salida."
    )
    parser.add_argument(
        "--purge-secrets",
        action="store_true",
        help="Elimina las claves API del keyring y los secretos del .env y sale.",
    )
    parser.add_argument("--port", type=int, default=None, help="Puerto del servidor.")
    parser.add_argument("--host", type=str, default=None, help="Host del servidor.")
    args = parser.parse_args(argv)

    if args.purge_secrets:
        return purge_secrets()

    try:
        env_path, generated_token, created = ensure_env_file()
        if generated_token and not paths.is_frozen():
            print(f"Token generado y guardado en {env_path} (clave BRIDGE_TOKEN).")
        cfg = load_config(env_path)
    except ConfigError as exc:
        report_error(f"Configuración inválida: {exc}")
        return 2
    if args.port is not None:
        cfg.port = args.port
    if args.host is not None:
        cfg.host = args.host
    try:
        cfg.validate()
    except ConfigError as exc:
        report_error(f"Configuración inválida: {exc}")
        return 2

    setup_logging(cfg)
    log.info(
        "Extdator bridge %s (modo %s)",
        __version__,
        "check" if args.check else "server-only" if args.server_only else "bridge",
    )

    # Los ajustes del usuario (GUI) se aplican ANTES de crear el estado:
    # el modelo/proveedor guardados por la interfaz tienen prioridad sobre
    # los valores base del .env.
    settings_store = SettingsStore()
    applied_user = settings_store.load() and settings_store.apply_to(cfg) or []
    if applied_user:
        log.info("Ajustes de usuario aplicados: %s", ", ".join(applied_user))

    bus = Bus()
    state = RuntimeState(model=cfg.ollama_model)
    # Restaurar el proveedor remoto persistido por la GUI (si lo hay).
    saved_provider = getattr(cfg, "provider", "ollama") or "ollama"
    saved_remote = getattr(cfg, "remote_model", "") or ""
    if saved_provider != "ollama" and saved_remote:
        state.set_provider(saved_provider, saved_remote)
    ollama = OllamaClient(cfg)
    pairing = PairingManager(cfg.token)

    try:
        if args.check:
            return run_check(cfg, bus, state, ollama)
        if args.server_only:
            return run_server_only(cfg, bus, state, ollama)
        return run_bridge(cfg, bus, state, ollama, settings_store, pairing)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
