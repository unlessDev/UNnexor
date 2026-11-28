/* UNnexor — background service worker.
 *
 * Única responsabilidad: recibir la selección del content script y hacer la
 * petición HTTP POST al puente local (FastAPI). Toda la lógica de UI vive en
 * el content script; aquí no hay DOM.
 */

const DEFAULT_CONFIG = {
  backendUrl: "http://127.0.0.1:5000",
  token: "",
};
const REQUEST_TIMEOUT_MS = 15000;

chrome.runtime.onInstalled.addListener(async (details) => {
  // Garantiza valores por defecto sin sobrescribir la configuración del usuario.
  const cfg = await chrome.storage.local.get(DEFAULT_CONFIG);
  await chrome.storage.local.set(cfg);
  // Primer arranque sin vinculación: abrimos el asistente de configuración.
  // No se re-inyecta en pestañas existentes para no solicitar acceso a
  // todos los sitios; las páginas nuevas ya reciben el content script.
  if (details.reason === "install" && !cfg.token) {
    chrome.runtime.openOptionsPage();
  }
});

// El icono de la barra abre la configuración (no hay popup).
chrome.action.onClicked.addListener(() => {
  chrome.runtime.openOptionsPage();
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.type !== "unnexor:send-selection") return false;
  handleSend(msg, sender)
    .then(sendResponse)
    .catch((err) => {
      sendResponse({
        ok: false,
        error: "Error inesperado: " + (err && err.message ? err.message : String(err)),
      });
    });
  return true; // canal abierto hasta que llegue la respuesta asíncrona
});

async function loadConfig() {
  const cfg = await chrome.storage.local.get(DEFAULT_CONFIG);
  return {
    backendUrl: (cfg.backendUrl || DEFAULT_CONFIG.backendUrl).replace(/\/+$/, ""),
    token: cfg.token || "",
  };
}

async function handleSend(msg, sender) {
  const cfg = await loadConfig();
  const text = typeof msg.text === "string" ? msg.text : "";
  if (!text.trim()) {
    return { ok: false, error: "La selección está vacía." };
  }

  // «Consultar…» adjunta una pregunta; «Enviar directamente» no. La pregunta
  // DEBE viajar al puente: sin ella el modelo recibe solo la selección y la
  // consulta se comporta como un envío directo (error corregido en 2.1.0).
  const question = typeof msg.question === "string" ? msg.question.trim() : "";
  const mode = question ? "ask" : "direct";
  const payload = {
    text,
    question,
    mode,
    source_url: (sender && sender.tab && sender.tab.url) || msg.sourceUrl || "",
    timestamp: new Date().toISOString(),
  };

  const headers = { "Content-Type": "application/json" };
  if (cfg.token) headers["Authorization"] = "Bearer " + cfg.token;

  let resp;
  try {
    resp = await fetch(cfg.backendUrl + "/api/selection", {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch (err) {
    const name = err && err.name;
    if (name === "TimeoutError" || name === "AbortError") {
      return {
        ok: false,
        error: "La aplicación UNnexor tardó demasiado en responder. Comprueba que siga abierta.",
      };
    }
    return {
      ok: false,
      error: "No se pudo conectar con la aplicación UNnexor. Ábrela y vuelve a intentarlo.",
    };
  }

  let body = null;
  try {
    body = await resp.json();
  } catch (_) {
    /* respuesta sin JSON */
  }

  if (resp.ok) {
    if (mode === "ask" && !(body && body.question_received)) {
      // Un puente anterior aceptaría el texto e ignoraría la pregunta.
      return {
        ok: false,
        error: "La aplicación UNnexor instalada no admite consultas. Actualízala a la versión 2.1 o posterior.",
      };
    }
    return { ok: true, message: (body && body.message) || "Texto enviado a la aplicación." };
  }

  switch (resp.status) {
    case 401:
      return {
        ok: false,
        error: "La vinculación no es válida. Abre UNnexor → ⚙ → Emparejar extensión y vuelve a conectar.",
      };
    case 403:
      return {
        ok: false,
        error: "La aplicación rechazó la conexión. Vuelve a emparejar la extensión desde Opciones.",
      };
    case 413:
      return { ok: false, error: "El texto es demasiado extenso. Selecciona un fragmento más corto." };
    case 429:
      return { ok: false, error: "Demasiadas solicitudes seguidas. Espera unos segundos." };
    default:
      return {
        ok: false,
        error: (body && (body.detail || body.error)) ||
          "La aplicación devolvió un error (" + resp.status + ").",
      };
  }
}
