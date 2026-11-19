"""Diagnóstico v3: ataca las patas invisibles del flujo.

1. Carga la extensión (Extensions.loadUnpacked) y navega a la página.
2. Lista TODOS los targets (¿existe el service worker de la extensión?).
3. Localiza el contexto de mundo aislado del content script y evalúa ahí
   directamente ``chrome.runtime.sendMessage`` → respuesta del SW.
4. Si el SW está activo, le adjunta un debugger y evalúa un fetch de prueba
   a /api/selection para medir la pata SW→puente.
5. Recoge consola/errores del SW y del contexto aislado.
"""

from __future__ import annotations

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
    def __init__(self, ws_url: str, collect: bool = True) -> None:
        self.ws = websocket.create_connection(ws_url, timeout=25)
        self._id = 0
        self.events: list[dict] = []
        self.collect = collect

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
            if self.collect and "method" in message:
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

    def evaluate(self, expression: str, context_id: int | None = None):
        params = {"expression": expression, "returnByValue": True, "awaitPromise": True}
        if context_id:
            params["contextId"] = context_id
        result = self.call("Runtime.evaluate", **params)
        inner = result.get("result", {})
        if "exceptionDetails" in result:
            detail = result["exceptionDetails"]
            text = detail.get("exception", {}).get("description") or detail.get("text")
            return {"__exception__": str(text)[:300]}
        return inner.get("value")

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def console_summary(events: list[dict], limit: int = 12) -> list[str]:
    out = []
    for event in events:
        if event.get("method") == "Runtime.consoleAPICalled":
            params = event["params"]
            if params.get("type") in ("error", "warning", "log"):
                text = " ".join(
                    str(a.get("value", a.get("description", "")))
                    for a in params.get("args", [])
                )
                out.append(f"[{params['type']}] {text[:200]}")
        elif event.get("method") == "Runtime.exceptionThrown":
            detail = event["params"].get("exceptionDetails", {})
            text = detail.get("exception", {}).get("description") or detail.get("text", "")
            out.append("[exception] " + str(text)[:300])
    return out[:limit]


def main() -> int:
    report: dict = {}

    import shutil

    shutil.rmtree(PROFILE, ignore_errors=True)
    # SOLO procesos de prueba (perfil .cdp-profile): nunca el navegador del usuario.
    ps_script = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -match 'cdp-profile' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps_script],
                   capture_output=True)  # noqa: S603
    time.sleep(1.5)

    process = subprocess.Popen(
        [CHROME,
         f"--remote-debugging-port={DEBUG_PORT}",
         f"--user-data-dir={PROFILE}",
         "--enable-unsafe-extension-debugging",
         "--no-first-run", "--no-default-browser-check",
         "--window-size=1200,900", "--disable-gpu",
         "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        try:
            http_json(f"http://127.0.0.1:{DEBUG_PORT}/json/version")
            break
        except Exception:
            time.sleep(0.3)

    browser = CDP(http_json(f"http://127.0.0.1:{DEBUG_PORT}/json/version")["webSocketDebuggerUrl"])
    load_result = browser.call("Extensions.loadUnpacked", path=str(EXT_DIR))
    report["extension_id"] = load_result.get("id", "")
    browser.close()

    page = next(t for t in http_json(f"http://127.0.0.1:{DEBUG_PORT}/json")
                if t.get("type") == "page")
    cdp = CDP(page["webSocketDebuggerUrl"])
    cdp.call("Runtime.enable")
    cdp.call("Page.enable")
    cdp.call("Page.navigate", url=PAGE_URL)
    for _ in range(20):
        state = cdp.evaluate("document.readyState")
        if state == "complete":
            break
        cdp.drain(0.4)
    cdp.drain(1.0)

    # ---- targets visibles ahora (¿SW de la extensión corriendo?) ----------
    targets = http_json(f"http://127.0.0.1:{DEBUG_PORT}/json")
    report["targets_after_page"] = [
        {"type": t.get("type"), "url": t.get("url", "")[:90]} for t in targets
    ]

    # ---- contextos de ejecución: buscar el mundo aislado del content ------
    isolated = []
    for event in cdp.events:
        if event.get("method") == "Runtime.executionContextCreated":
            ctx = event["params"].get("context", {})
            aux = ctx.get("auxData", {}) or {}
            isolated.append({
                "id": ctx.get("id"),
                "name": ctx.get("name", "")[:60],
                "origin": ctx.get("origin", "")[:60],
                "type": aux.get("type", "?"),
            })
    report["execution_contexts"] = isolated
    ext_ctx = next((c for c in isolated
                    if c["origin"].startswith("chrome-extension://")
                    and c["type"] != "default"), None)
    report["isolated_context_found"] = bool(ext_ctx)

    # ---- selección con ratón de confianza ---------------------------------
    rect = json.loads(cdp.evaluate(
        "JSON.stringify(document.getElementById('parrafo').getBoundingClientRect())"
    ))
    x1, y1 = rect["left"] + 12, rect["top"] + 20
    x2, y2 = rect["left"] + 430, rect["top"] + 20
    cdp.call("Input.dispatchMouseEvent", type="mousePressed",
             x=x1, y=y1, button="left", buttons=1, clickCount=1)
    for step in range(1, 13):
        cdp.call("Input.dispatchMouseEvent", type="mouseMoved",
                 x=x1 + (x2 - x1) * step / 12, y=y1, button="left", buttons=1)
    cdp.call("Input.dispatchMouseEvent", type="mouseReleased",
             x=x2, y=y2, button="left", buttons=0, clickCount=1)
    cdp.drain(0.9)
    report["selection"] = bool(cdp.evaluate("!!window.getSelection().toString()"))

    # ---- TEST A: sendMessage desde el mundo aislado (pata content→SW) -----
    if ext_ctx:
        result = cdp.evaluate(
            "new Promise(resolve => {"
            "  const t0 = Date.now();"
            "  try {"
            "    chrome.runtime.sendMessage("
            "      {type:'extdator:send-selection', text:'diagnostico v3 TEST A',"
            "       sourceUrl: location.href},"
            "      resp => {"
            "        const err = chrome.runtime.lastError;"
            "        resolve({ok: !err, lastError: err ? err.message : null,"
            "                 response: resp, ms: Date.now() - t0});"
            "      });"
            "  } catch (e) { resolve({ok:false, threw: String(e)}); }"
            "})",
            context_id=ext_ctx["id"],
        )
        report["TEST_A_sendMessage_from_content"] = result
    else:
        report["TEST_A_sendMessage_from_content"] = "sin contexto aislado"

    cdp.drain(6.0)
    report["console_after_testA"] = console_summary(cdp.events)

    # ---- TEST B: fetch directo desde el SW (pata SW→puente) ---------------
    targets = http_json(f"http://127.0.0.1:{DEBUG_PORT}/json")
    sw = next((t for t in targets if t.get("type") == "service_worker"
               and "background.js" in t.get("url", "")), None)
    report["sw_target_now"] = ({"url": sw["url"][:90]} if sw else "NO PRESENTE")
    if sw:
        sw_cdp = CDP(sw["webSocketDebuggerUrl"])
        sw_cdp.call("Runtime.enable")
        result = sw_cdp.evaluate(
            "fetch('http://127.0.0.1:5000/api/selection', {"
            "  method:'POST',"
            "  headers:{'Content-Type':'application/json',"
            "           'Authorization':'Bearer " + TOKEN + "'},"
            "  body: JSON.stringify({text:'diagnostico v3 TEST B',"
            "                        source_url:'sw-direct-test'})"
            "}).then(r => r.text().then(body => ({status: r.status, body})))"
            ".catch(e => ({fetch_error: String(e)}))"
        )
        report["TEST_B_fetch_from_sw"] = result
        sw_cdp.close()

    # ---- estado del puente ------------------------------------------------
    time.sleep(2.0)
    req = urllib.request.Request(
        "http://127.0.0.1:5000/api/status",
        headers={"Authorization": "Bearer " + TOKEN},
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        report["bridge_status"] = json.loads(response.read().decode("utf-8"))

    cdp.close()
    process.terminate()
    try:
        process.wait(timeout=5)
    except Exception:
        process.kill()

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
