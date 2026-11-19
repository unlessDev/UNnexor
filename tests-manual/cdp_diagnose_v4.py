"""Diagnóstico v4: reproduce el escenario exacto del usuario y valida el fix.

1. Abre Chrome SIN la extensión y navega a la página de prueba
   (pestaña creada ANTES de la instalación, como le pasó al usuario).
2. Verifica que el content script NO está inyectado (estado inicial).
3. Carga la extensión (Extensions.loadUnpacked) → dispara onInstalled →
   el nuevo handler re-inyecta en las pestañas ya abiertas.
4. Verifica que el host aparece SIN recargar la página.
5. Configura el token en chrome.storage (como haría la página de opciones).
6. Selección real + clic real sobre el botón + captura (debe verse el toast).
7. Estado del puente: la cola debe consumirse y Ollama debe generar.
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

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

TOKEN = ""
for line in (ROOT / "local-bridge" / ".env").read_text(encoding="utf-8").splitlines():
    if line.startswith("BRIDGE_TOKEN="):
        TOKEN = line.split("=", 1)[1].strip()


def http_json(url: str):
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


class CDP:
    def __init__(self, ws_url: str) -> None:
        self.ws = websocket.create_connection(ws_url, timeout=25)
        self._id = 0
        self.events: list[dict] = []

    def call(self, method: str, **params):
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
        self.ws.settimeout(25)

    def evaluate(self, expression: str):
        result = self.call("Runtime.evaluate", expression=expression,
                           returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in result:
            detail = result["exceptionDetails"]
            text = detail.get("exception", {}).get("description") or detail.get("text")
            return {"__exception__": str(text)[:200]}
        return result.get("result", {}).get("value")

    def mouse(self, type_: str, x, y, **kw) -> None:
        self.call("Input.dispatchMouseEvent", type=type_, x=x, y=y, **kw)

    def screenshot(self, path: Path) -> None:
        data = self.call("Page.captureScreenshot", format="png")
        path.write_bytes(base64.b64decode(data["data"]))

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def main() -> int:
    report: dict = {}

    import shutil

    shutil.rmtree(PROFILE, ignore_errors=True)
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
          "Where-Object { $_.CommandLine -match 'cdp-profile' } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True)  # noqa: S603
    time.sleep(1.5)

    # ---- fase 1: navegador SIN extensión, pestaña ya abierta ---------------
    process = subprocess.Popen(
        [CHROME,
         f"--remote-debugging-port={DEBUG_PORT}",
         f"--user-data-dir={PROFILE}",
         "--enable-unsafe-extension-debugging",
         "--no-first-run", "--no-default-browser-check",
         "--window-size=1200,900", "--disable-gpu",
         PAGE_URL],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        try:
            http_json(f"http://127.0.0.1:{DEBUG_PORT}/json/version")
            break
        except Exception:
            time.sleep(0.3)
    time.sleep(2.5)  # terminar de cargar la página

    page = next(t for t in http_json(f"http://127.0.0.1:{DEBUG_PORT}/json")
                if t.get("type") == "page")
    cdp = CDP(page["webSocketDebuggerUrl"])
    cdp.call("Runtime.enable")
    cdp.call("Page.enable")
    for _ in range(10):
        state = cdp.evaluate("document.readyState")
        if state == "complete":
            break
        cdp.drain(0.4)

    report["before_install_injected"] = bool(
        cdp.evaluate("!!document.querySelector('div[data-extdator=\"host\"]')")
    )

    # ---- fase 2: instalar la extensión (onInstalled) -----------------------
    browser = CDP(http_json(f"http://127.0.0.1:{DEBUG_PORT}/json/version")["webSocketDebuggerUrl"])
    result = browser.call("Extensions.loadUnpacked", path=str(EXT_DIR))
    report["extension_id"] = result.get("id", "")
    browser.close()
    time.sleep(3.5)  # margen para onInstalled + re-inyección en la pestaña abierta

    report["after_install_injected_without_reload"] = bool(
        cdp.evaluate("!!document.querySelector('div[data-extdator=\"host\"]')")
    )

    # ---- fase 3: token en storage (simula la página de opciones) -----------
    targets = http_json(f"http://127.0.0.1:{DEBUG_PORT}/json")
    sw = next((t for t in targets if t.get("type") == "service_worker"
               and "akfoafndddmjfjolnmkjoabemocofcfo" in t.get("url", "")), None)
    report["sw_running"] = bool(sw)
    if sw:
        sw_cdp = CDP(sw["webSocketDebuggerUrl"])
        sw_cdp.call("Runtime.enable")
        sw_cdp.evaluate(
            "chrome.storage.local.set({backendUrl:'http://127.0.0.1:5000',"
            "token:'" + TOKEN + "'})"
        )
        sw_cdp.close()
        time.sleep(0.5)

    # ---- fase 4: selección + clic ------------------------------------------
    rect = json.loads(cdp.evaluate(
        "JSON.stringify(document.getElementById('parrafo').getBoundingClientRect())"
    ))
    x1, y1 = rect["left"] + 12, rect["top"] + 20
    x2, y2 = rect["left"] + 430, rect["top"] + 20
    cdp.mouse("mousePressed", x1, y1, button="left", buttons=1, clickCount=1)
    for step in range(1, 13):
        cdp.mouse("mouseMoved", x1 + (x2 - x1) * step / 12, y1, button="left", buttons=1)
    cdp.mouse("mouseReleased", x2, y2, button="left", buttons=0, clickCount=1)
    cdp.drain(0.9)
    report["selection_made"] = bool(cdp.evaluate("!!window.getSelection().toString()"))

    anchor = json.loads(cdp.evaluate(
        "JSON.stringify((() => {const s = window.getSelection();"
        "if (!s.rangeCount) return null;"
        "const r = s.getRangeAt(0).getClientRects()[0];"
        "return r ? {left: r.left, top: r.top} : null;})())"
    ))
    button_x, button_y = anchor["left"], anchor["top"] - 38
    if button_y < 6:
        button_y = anchor.get("bottom", anchor["top"] + 20) + 8

    cdp.screenshot(SHOT_DIR / "diag4_after_selection.png")

    # clic y espera generosa: SW frío + fetch + cola + Ollama
    cdp.mouse("mousePressed", button_x + 15, button_y + 15,
              button="left", buttons=1, clickCount=1)
    cdp.mouse("mouseReleased", button_x + 15, button_y + 15,
              button="left", buttons=0, clickCount=1)
    cdp.drain(2.0)
    cdp.screenshot(SHOT_DIR / "diag4_after_click.png")

    # ---- fase 5: verificar el puente (cola consumida + generación) ---------
    deadline = time.monotonic() + 90
    status = None
    while time.monotonic() < deadline:
        req = urllib.request.Request(
            "http://127.0.0.1:5000/api/status",
            headers={"Authorization": "Bearer " + TOKEN},
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            status = json.loads(response.read().decode("utf-8"))
        if (not status.get("generating")
                and (status.get("history_messages") or 0) >= 2):
            break
        time.sleep(3.0)
    report["bridge_status_final"] = status

    cdp.close()
    process.terminate()
    try:
        process.wait(timeout=5)
    except Exception:
        process.kill()

    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = (not report["before_install_injected"]
          and report["after_install_injected_without_reload"]
          and report["selection_made"]
          and (status or {}).get("history_messages", 0) >= 2)
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({"fatal": f"{type(exc).__name__}: {exc}"}))
        sys.exit(1)
