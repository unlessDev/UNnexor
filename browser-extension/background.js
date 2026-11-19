/* Extdator — background service worker.
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

chrome.runtime.onInstalled.addListener(() => {
  // Garantiza valores por defecto sin sobrescribir la configuración del usuario.
  chrome.storage.local.get(DEFAULT_CONFIG).then((cfg) => {
    chrome.storage.local.set(cfg);
  });
  // Los content scripts del manifest SOLO se inyectan en páginas cargadas a
  // partir de ahora. Las pestañas ya abiertas cuando se instala/recarga la
  // extensión quedarían sin botón ✦ — se re-inyectan aquí de una vez.
  injectIntoOpenTabs();
});

async function injectIntoOpenTabs() {
  try {
    const tabs = await chrome.tabs.query({});
    for (const tab of tabs) {
      if (!tab.id || !tab.url || !/^https?:/i.test(tab.url)) {
        continue; // chrome://, edge://, tienda, PDFs internos: sin permiso
      }
      try {
        await chrome.scripting.executeScript({
          target: { tabId: tab.id, allFrames: true },
          files: ["content.js"],
        });
      } catch (_) {
        // Sin host permission para esa pestaña o página especial: se ignora.
      }
    }
  } catch (_) {
    // chrome.tabs no disponible en este contexto: no es crítico.
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.type !== "extdator:send-selection") return false;
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

  const payload = {
    text,
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
        error: "El puente local no respondió (timeout). ¿Ejecutaste scripts\\start.ps1?",
      };
    }
    return {
      ok: false,
      error: "No se pudo conectar con el puente local en " + cfg.backendUrl +
        ". Inicia el servidor con scripts\\start.ps1.",
    };
  }

  let body = null;
  try {
    body = await resp.json();
  } catch (_) {
    /* respuesta sin JSON */
  }

  if (resp.ok) {
    return { ok: true, message: (body && body.message) || "Texto enviado a Ollama." };
  }

  switch (resp.status) {
    case 401:
      return {
        ok: false,
        error: "Token inválido. Copia BRIDGE_TOKEN de local-bridge\\.env en las opciones de la extensión.",
      };
    case 403:
      return { ok: false, error: "Origen no autorizado por el puente local." };
    case 413:
      return { ok: false, error: "El texto es demasiado extenso para el límite configurado." };
    case 429:
      return { ok: false, error: "Demasiadas solicitudes seguidas. Espera unos segundos." };
    default:
      return {
        ok: false,
        error: (body && body.error) || "Error HTTP " + resp.status + " del puente local.",
      };
  }
}
