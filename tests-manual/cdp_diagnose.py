"""Diagnóstico E2E de la extensión Extdator con un Chromium real (v2).

Estrategia:
  * Chrome 137+ ignora --load-extension en builds estables. Se usa el comando
    CDP ``Extensions.loadUnpacked`` (disponible con
    ``--enable-unsafe-extension-debugging``), que además devuelve el ID real.
  * Si el navegador no soporta ese comando, se prueba el siguiente de la
    lista; como último recurso se informa con precisión.
  * SIEMPRE se ejecuta la prueba de página: inyección del content script,
    selección real con eventos de ratón de confianza, capturas de pantalla,
    clic sobre el botón y verificación en el puente local.

Uso:  local-bridge/.venv/Scripts/python.exe tests-manual/cdp_diagnose.py
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websocket

ROOT = Path(__file__).resolve().parent.parent
EXT_DIR = ROOT / "browser-extension"
PROFILE = ROOT / ".cdp-profile"
SHOT_DIR = ROOT / "tests-manual"
DEBUG_PORT = 9333
PAGE_URL = "http://127.0.0.1:8901/testpage.html"
EXPECTED_ID = "akfoafndddmjfjolnmkjoabemocofcfo"

BROWSERS = [
    ("chrome", [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]),
    ("brave", [r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"]),
    ("edge", [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]),
]


def http_json(url: str) -> dict | list:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


class CDP:
    """Cliente CDP mínimo con coincidencia de ids y cola de eventos."""

    def __init__(self, ws_url: str) -> None:
        self.ws = websocket.create_connection(ws_url, timeout=20)
        self._id = 0
        self.events: list[dict] = []

    def call(self, method: str, **params) -> dict:
        self._id += 1
        msg_id = self._id
        self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            message = json.loads(self.ws.recv())
            if message.get("id") == msg_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})
            if "method" in message:
                self.events.append(message)
        raise TimeoutError(method)

    def try_call(self, method: str, **params) -> tuple[bool, str]:
        try:
            self.call(method, **params)
            return True, ""
        except RuntimeError as exc:
            return False, str(exc)

    def drain(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        self.ws.settimeout(0.2)
        while time.monotonic() < deadline:
            try:
                message = json.loads(self.ws.recv())
                if "method" in message:
                    self.events.append(message)
            except websocket.WebSocketTimeoutException:
                continue
        self.ws.settimeout(20)

    def evaluate(self, expression: str):
        result = self.call("Runtime.evaluate", expression=expression,
                           returnByValue=True, awaitPromise=True)
        return result.get("result", {}).get("value")

    def mouse(self, type_: str, x: float, y: float, **kw) -> None:
        self.call("Input.dispatchMouseEvent", type=type_, x=x, y=y, **kw)

    def screenshot(self, path: Path) -> None:
        data = self.call("Page.captureScreenshot", format="png")
        path.write_bytes(base64.b64decode(data["data"]))

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def launch_browser(headless: bool, tried: list) -> tuple[str, subprocess.Popen | None]:
    for name, paths in BROWSERS:
        exe = next((p for p in paths if Path(p).exists()), None)
        if not exe:
            tried.append(f"{name}: no encontrado en rutas habituales")
            continue
        args = [
            exe,
            f"--remote-debugging-port={DEBUG_PORT}",
            f"--user-data-dir={PROFILE}",
            "--enable-unsafe-extension-debugging",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1200,900",
            "--disable-gpu",
            "about:blank",
        ]
        if headless:
            args.insert(1, "--headless=new")
        process = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 12
        opened = False
        while time.monotonic() < deadline:
            try:
                http_json(f"http://127.0.0.1:{DEBUG_PORT}/json/version")
                opened = True
                break
            except Exception:
                time.sleep(0.3)
        if opened:
            return name, process
        process.terminate()
        try:
            process.wait(timeout=5)
        except Exception:
            process.kill()
        tried.append(f"{name}: no abrió el puerto de depuración")
        time.sleep(1)
    return "", None


def main() -> int:
    report: dict = {"notes": []}
    tried: list[str] = []

    name, process = launch_browser(headless=True, tried=tried)
    if not name:
        report["notes"].append("headless falló; reintentando en modo ventana")
        name, process = launch_browser(headless=False, tried=tried)
    if not name:
        report["fatal"] = tried
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    report["browser"] = name

    # ---- 1) cargar la extensión con Extensions.loadUnpacked (nivel browser)
    version = http_json(f"http://127.0.0.1:{DEBUG_PORT}/json/version")
    browser_cdp = CDP(version["webSocketDebuggerUrl"])
    result = None
    try:
        result = browser_cdp.call("Extensions.loadUnpacked", path=str(EXT_DIR))
        ok, error = True, ""
    except RuntimeError as exc:
        ok, error = False, str(exc)
    ext_id = ""
    if ok:
        ext_id = str((result or {}).get("id", ""))
        report["extension_loaded"] = True
        report["extension_id"] = ext_id
        report["extension_id_matches_env"] = ext_id == EXPECTED_ID
    else:
        report["extension_loaded"] = False
        report["loadUnpacked_error"] = error
        report["notes"].append(
            "Extensions.loadUnpacked no disponible en este navegador/build"
        )

    # ---- 2) prueba de página: inyección + selección + botón + clic
    page = next(t for t in http_json(f"http://127.0.0.1:{DEBUG_PORT}/json")
                if t.get("type") == "page")
    cdp = CDP(page["webSocketDebuggerUrl"])
    cdp.call("Runtime.enable")
    cdp.call("Page.enable")
    cdp.call("Page.navigate", url=PAGE_URL)
    # Espera activa: readyState complete Y el párrafo presente (max ~10 s).
    for _ in range(20):
        state = cdp.evaluate(
            "document.readyState + '|' + !!document.getElementById('parrafo')"
        )
        if isinstance(state, str) and state.endswith("|true"):
            break
        cdp.drain(0.5)
    else:
        report["fatal"] = f"la página no cargó (estado: {state!r})"

    injected = cdp.evaluate("!!document.querySelector('div[data-extdator=\"host\"]')")
    report["content_injected"] = bool(injected)
    if not injected:
        # ¿la extensión quedó cargada aunque loadUnpacked no respondiera?
        report["notes"].append(
            "Host no presente: el content script NO se inyectó en la página"
        )

    rect = cdp.evaluate(
        "JSON.stringify((id => {const r = document.getElementById(id).getBoundingClientRect();"
        "return {left: r.left, top: r.top};})('parrafo'))"
    )
    rect = json.loads(rect)
    x1, y1 = rect["left"] + 12, rect["top"] + 20
    x2, y2 = rect["left"] + 430, rect["top"] + 20

    cdp.mouse("mousePressed", x1, y1, button="left", buttons=1, clickCount=1)
    for step in range(1, 13):
        cdp.mouse("mouseMoved", x1 + (x2 - x1) * step / 12, y1,
                  button="left", buttons=1)
    cdp.mouse("mouseReleased", x2, y2, button="left", buttons=0, clickCount=1)
    cdp.drain(0.9)

    selection = cdp.evaluate("window.getSelection().toString()")
    report["selection_made"] = bool(selection and selection.strip())
    report["selection_text"] = (selection or "")[:70]

    anchor = cdp.evaluate(
        "JSON.stringify((() => {const s = window.getSelection();"
        "if (!s.rangeCount) return null;"
        "const r = s.getRangeAt(0).getClientRects()[0];"
        "return r ? {left: r.left, top: r.top, bottom: r.bottom} : null;})())"
    )
    anchor = json.loads(anchor) if anchor else None
    button_x = button_y = None
    if anchor:
        button_x = max(6, min(anchor["left"], 1180 - 36))
        button_y = anchor["top"] - 38
        if button_y < 6:
            button_y = anchor["bottom"] + 8
    report["button_expected_pos"] = {"x": button_x, "y": button_y}
    cdp.screenshot(SHOT_DIR / "diag_after_selection.png")

    # ---- errores de consola de la página/extensión
    errors = []
    for event in cdp.events:
        if event.get("method") == "Runtime.consoleAPICalled" \
                and event["params"].get("type") in ("error", "warning"):
            text = " ".join(
                str(a.get("value", a.get("description", "")))
                for a in event["params"].get("args", [])
            )
            errors.append(f"console: {text[:200]}")
        elif event.get("method") == "Runtime.exceptionThrown":
            detail = event["params"].get("exceptionDetails", {})
            errors.append("exception: " + str(detail.get("text", ""))[:200])
    report["console_errors"] = errors

    # ---- 3) clic sobre el botón y verificación en el puente
    if button_x is not None:
        cdp.mouse("mousePressed", button_x + 15, button_y + 15,
                  button="left", buttons=1, clickCount=1)
        cdp.mouse("mouseReleased", button_x + 15, button_y + 15,
                  button="left", buttons=0, clickCount=1)
        cdp.drain(1.5)
        cdp.screenshot(SHOT_DIR / "diag_after_click.png")

    cdp.close()
    browser_cdp.close()
    if process is not None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except Exception:
            process.kill()

    # estado del puente tras el clic
    try:
        env = {}
        for line in (ROOT / "local-bridge" / ".env").read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip()
        import urllib.parse
        req = urllib.request.Request(
            "http://127.0.0.1:5000/api/status",
            headers={"Authorization": "Bearer " + env["BRIDGE_TOKEN"]},
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            report["bridge_status_after_click"] = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        report["bridge_status_after_click"] = f"no disponible: {exc}"

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # nunca salir sin informe
        print(json.dumps({"fatal": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False, indent=2))
        sys.exit(1)
