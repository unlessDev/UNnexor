"""Depuración mínima del flujo CDP: targets, navegación y evaluate."""
import json
import time
import urllib.request

import websocket


def http_json(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read().decode())


targets = http_json("http://127.0.0.1:9333/json")
print("--- targets ---")
for t in targets:
    print(t.get("type"), "|", t.get("url", "")[:70], "|", t.get("title", "")[:40])

pages = [t for t in targets if t.get("type") == "page"]
if not pages:
    print("NO HAY PAGE TARGETS")
    raise SystemExit(1)

page = pages[0]
print("--- conectando a", page["webSocketDebuggerUrl"][:80])
ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=15)
mid = 0


def call(method, **params):
    global mid
    mid += 1
    ws.send(json.dumps({"id": mid, "method": method, "params": params}))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        m = json.loads(ws.recv())
        if m.get("id") == mid:
            return m
        # guardar eventos aparte
    return {"error": "timeout"}


r = call("Runtime.enable")
print("Runtime.enable ->", "error" in r and r.get("error"))
r = call("Page.enable")
r = call("Page.navigate", url="http://127.0.0.1:8901/testpage.html")
print("Page.navigate ->", json.dumps(r.get("result", r))[:200])

for i in range(15):
    time.sleep(0.6)
    r = call("Runtime.evaluate", expression="document.readyState+'|'+location.href",
             returnByValue=True)
    value = (r.get("result", {}).get("result", {}) or {}).get("value")
    print(f"poll {i}: {value!r}")
    if value and str(value).startswith("complete"):
        break

r = call("Runtime.evaluate",
         expression="!!document.getElementById('parrafo')", returnByValue=True)
print("parrafo existe ->", json.dumps(r.get("result", r))[:300])

r = call("Runtime.evaluate",
         expression="JSON.stringify(document.getElementById('parrafo').getBoundingClientRect())",
         returnByValue=True)
print("rect ->", json.dumps(r.get("result", r))[:300])
ws.close()
