/* UNnexor — página de opciones (rediseño Fase 6).
 *
 * Asistente de 4 pasos: bienvenida → conectar (código de emparejamiento)
 * → diagnóstico (bridge/auth/envío) → preferencias.
 *
 * Seguridad:
 *   * El token se recibe por POST /api/pair/confirm SOLO a cambio del
 *     código efímero del panel; nunca se pide por otros caminos.
 *   * La configuración técnica manual vive colapsada (usuarios avanzados).
 *   * Las claves de PROVEEDORES nunca pasan por aquí: se administran en
 *     la aplicación de escritorio (keyring del sistema).
 */

"use strict";

const DEFAULT_CONFIG = { backendUrl: "http://127.0.0.1:5000", token: "" };
const DEFAULTS = { ...DEFAULT_CONFIG, showFab: true, wizardDone: false };

const $ = (id) => document.getElementById(id);

/* ------------------------------------------------------------------ */
/* Navegación del asistente                                            */
/* ------------------------------------------------------------------ */
let currentStep = 1;
let paired = false;
let diagPassed = false;

function gotoStep(n) {
  currentStep = n;
  document.querySelectorAll(".step").forEach((el) => {
    el.classList.toggle("active", Number(el.dataset.step) === n);
  });
  document.querySelectorAll(".step-pill").forEach((el) => {
    const idx = Number(el.dataset.pill);
    el.classList.toggle("active", idx === n);
    el.classList.toggle("done", idx < n && (idx < 2 ? paired : diagPassed));
  });
  if (n === 4) refreshFinalCard();
}

document.querySelectorAll("[data-goto]").forEach((btn) => {
  btn.addEventListener("click", () => gotoStep(Number(btn.dataset.goto)));
});

/* ------------------------------------------------------------------ */
/* Utilidades                                                          */
/* ------------------------------------------------------------------ */
async function loadConfig() {
  const cfg = await chrome.storage.local.get(DEFAULTS);
  return cfg;
}

async function savePartial(patch) {
  await chrome.storage.local.set(patch);
}

function normalizeUrl(raw) {
  return (raw || "").trim().replace(/\/+$/, "") || DEFAULT_CONFIG.backendUrl;
}

function isLoopbackUrl(url) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return false;
    const host = parsed.hostname.toLowerCase();
    return host === "127.0.0.1" || host === "localhost";
  } catch (_) {
    return false;
  }
}

function statusLine(id, tone, title, detail) {
  const line = $(id);
  line.className = "statusline " + tone;
  line.querySelector(".ico").textContent =
    tone === "ok" ? "✓" : tone === "err" ? "✕" : "⏳";
  line.querySelector("strong").textContent = title;
  line.querySelector("small").textContent = detail;
}

function statusBox(id, tone, html) {
  const box = $(id);
  box.innerHTML = `<div class="statusline ${tone}"><span class="ico">${
    tone === "ok" ? "✓" : tone === "err" ? "✕" : "⏳"
  }</span><div class="tx">${html}</div></div>`;
}

/* ------------------------------------------------------------------ */
/* Paso 2: emparejamiento por código                                   */
/* ------------------------------------------------------------------ */
$("pairBtn").addEventListener("click", async () => {
  const code = $("pairCode").value.trim();
  const cfg = await loadConfig();
  const url = normalizeUrl(cfg.backendUrl);
  if (!/^\d{6}$/.test(code)) {
    statusBox("pairStatus", "err",
      "<strong>Código incompleto.</strong><small>Introduce los 6 dígitos que muestra el panel.</small>");
    return;
  }
  $("pairBtn").disabled = true;
  statusBox("pairStatus", "wait",
    "<strong>Conectando…</strong><small>Validando el código con la aplicación UNnexor.</small>");
  try {
    const resp = await fetch(url + "/api/pair/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
      signal: AbortSignal.timeout(8000),
    });
    const body = await resp.json().catch(() => ({}));
    if (resp.ok && body.token) {
      await savePartial({
        token: body.token,
        backendUrl: url,
        wizardDone: true,
      });
      paired = true;
      statusBox("pairStatus", "ok",
        "<strong>¡Conectado!</strong><small>La extensión ya está vinculada con la aplicación.</small>");
      setTimeout(() => gotoStep(3), 900);
    } else if (resp.status === 403) {
      statusBox("pairStatus", "err",
        "<strong>Código inválido o caducado.</strong><small>Pide uno nuevo en el panel (⚙ → Emparejar extensión). Cada código vale 10 minutos y un solo uso.</small>");
    } else if (resp.status === 429) {
      statusBox("pairStatus", "err",
        "<strong>Demasiados intentos.</strong><small>Espera un minuto y vuelve a probar.</small>");
    } else {
      statusBox("pairStatus", "err",
        `<strong>No se pudo conectar.</strong><small>${body.detail || "Error HTTP " + resp.status}.</small>`);
    }
  } catch (err) {
    statusBox("pairStatus", "err",
      "<strong>No se alcanza la aplicación UNnexor.</strong><small>Ábrela desde el menú Inicio y vuelve a intentarlo. Si acabas de instalarla, espera unos segundos.</small>");
  } finally {
    $("pairBtn").disabled = false;
  }
});

/* Configuración manual avanzada ------------------------------------ */
$("eyeBtn").addEventListener("click", () => {
  const input = $("token");
  input.type = input.type === "password" ? "text" : "password";
});

$("saveManual").addEventListener("click", async () => {
  const backendUrl = normalizeUrl($("backendUrl").value);
  if (!isLoopbackUrl(backendUrl)) {
    statusBox("manualStatus", "err",
      "<strong>Dirección no válida.</strong><small>La aplicación debe estar en este equipo: usa 127.0.0.1 o localhost.</small>");
    return;
  }
  const token = $("token").value.trim();
  await savePartial({ backendUrl, token, wizardDone: true });
  paired = Boolean(token);
  statusBox("manualStatus", "ok",
    "<strong>Configuración manual guardada.</strong><small>Continúa con el diagnóstico para verificarla.</small>");
});

/* ------------------------------------------------------------------ */
/* Paso 3: diagnóstico                                                 */
/* ------------------------------------------------------------------ */
$("diagBtn").addEventListener("click", runDiagnostic);

async function runDiagnostic() {
  diagPassed = false;
  const cfg = await loadConfig();
  const url = normalizeUrl(cfg.backendUrl);
  const headers = { "Content-Type": "application/json" };
  if (cfg.token) headers["Authorization"] = "Bearer " + cfg.token;

  // 1) Puente activo (health no requiere token).
  statusLine("stBridge", "wait", "Puente local", "comprobando…");
  statusLine("stAuth", "wait", "Autenticación", "sin comprobar");
  statusLine("stSend", "wait", "Envío de prueba", "sin comprobar");
  $("diagExtra").innerHTML = "";

  let bridgeOk = false;
  let ollamaDetail = "";
  try {
    const resp = await fetch(url + "/health", {
      signal: AbortSignal.timeout(6000),
    });
    const body = await resp.json().catch(() => ({}));
    if (resp.ok && (body.app === "unnexor-bridge" || body.app === "extdator-bridge")) {
      bridgeOk = true;
      const o = body.ollama || {};
      ollamaDetail = o.reachable
        ? "aplicación activa · proveedor local conectado"
        : "aplicación activa · aviso: Ollama no responde (puede que uses un proveedor remoto)";
      statusLine("stBridge", "ok", "Aplicación UNnexor", ollamaDetail);
    } else {
      statusLine("stBridge", "err", "Aplicación UNnexor",
        "el puerto responde pero no es UNnexor. ¿Otra aplicación usa el " +
        "puerto 5000?");
    }
  } catch (err) {
    statusLine("stBridge", "err", "Aplicación UNnexor",
      "sin conexión en " + url + " — abre la aplicación UNnexor y reintenta");
  }

  // 2) Autenticación (GET /api/status con token).
  let authOk = false;
  if (bridgeOk) {
    try {
      const resp = await fetch(url + "/api/status", { headers });
      if (resp.ok) {
        authOk = true;
        statusLine("stAuth", "ok", "Autenticación",
          cfg.token ? "token válido" : "token válido (sin token configurado)");
      } else if (resp.status === 401) {
        statusLine("stAuth", "err", "Autenticación",
          "vinculación no válida — vuelve al paso 2 y usa el emparejamiento");
      } else if (resp.status === 403) {
        statusLine("stAuth", "err", "Autenticación",
          "la aplicación rechazó el origen — vuelve a emparejar la extensión");
      } else {
        statusLine("stAuth", "err", "Autenticación", "error HTTP " + resp.status);
      }
    } catch (err) {
      statusLine("stAuth", "err", "Autenticación", "fallo de red inesperado");
    }
  } else {
    statusLine("stAuth", "err", "Autenticación", "omitido: la aplicación no responde");
  }

  // 3) Envío de prueba de punta a punta (POST /api/selection).
  let sendOk = false;
  if (authOk) {
    try {
      const resp = await fetch(url + "/api/selection", {
        method: "POST",
        headers,
        body: JSON.stringify({
          text: "Prueba de diagnóstico de UNnexor: si ves este mensaje, la conexión funciona.",
          source_url: "diagnostic://options",
          timestamp: new Date().toISOString(),
        }),
        signal: AbortSignal.timeout(8000),
      });
      if (resp.status === 202) {
        sendOk = true;
        statusLine("stSend", "ok", "Envío de prueba",
          "aceptado — verás el texto de prueba en el panel flotante");
      } else if (resp.status === 429) {
        statusLine("stSend", "err", "Envío de prueba",
          "límite de solicitudes activo: espera unos segundos y reintenta");
      } else {
        const body = await resp.json().catch(() => ({}));
        statusLine("stSend", "err", "Envío de prueba",
          body.detail || "error HTTP " + resp.status);
      }
    } catch (err) {
      statusLine("stSend", "err", "Envío de prueba", "fallo de red inesperado");
    }
  } else {
    statusLine("stSend", "err", "Envío de prueba",
      "omitido: la autenticación falló");
  }

  diagPassed = bridgeOk && authOk && sendOk;
  if (diagPassed) {
    $("diagExtra").innerHTML =
      '<div class="statusline ok"><span class="ico">✓</span><div class="tx">' +
      "<strong>Todo correcto.</strong><small>Puedes continuar a preferencias.</small></div></div>";
  }
}

/* ------------------------------------------------------------------ */
/* Paso 4: preferencias                                                */
/* ------------------------------------------------------------------ */
async function refreshFinalCard() {
  const cfg = await loadConfig();
  $("showFab").checked = cfg.showFab !== false;
  $("finalCard").style.display = cfg.token ? "block" : "none";
}

$("showFab").addEventListener("change", async (e) => {
  await savePartial({ showFab: e.target.checked });
});

$("finishBtn").addEventListener("click", async () => {
  const cfg = await loadConfig();
  await savePartial({ wizardDone: true });
  window.close();
});

/* ------------------------------------------------------------------ */
/* Arranque                                                            */
/* ------------------------------------------------------------------ */
(async function init() {
  const cfg = await loadConfig();
  $("backendUrl").value = cfg.backendUrl || DEFAULT_CONFIG.backendUrl;
  $("token").value = cfg.token || "";
  paired = Boolean(cfg.token);
  // Reanudar el asistente donde se quedó (o en preferencias si ya está listo).
  if (!cfg.wizardDone) {
    gotoStep(1);
  } else if (cfg.token) {
    gotoStep(4);
  } else {
    gotoStep(2);
  }
})();
