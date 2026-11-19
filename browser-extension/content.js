/* Extdator — content script.
 *
 * Responsabilidades (solo estas):
 *   1. Detectar selecciones de texto válidas en la página.
 *   2. Mostrar/posicionar/ocultar un botón contextual (aislado con Shadow DOM).
 *   3. Enviar la selección exacta al background service worker.
 *   4. Mostrar un toast discreto con el resultado.
 *
 * No toca el CSS del sitio: todo el estilo vive dentro del Shadow DOM.
 */
(() => {
  "use strict";

  // Protección contra doble inyección (p. ej. recargas de la extensión).
  if (window.__extdatorInjected) return;
  window.__extdatorInjected = "content";

  const BUTTON_W = 30;
  const BUTTON_H = 30;
  const GAP = 8;
  const MARGIN = 6;
  const UPDATE_DELAY_MS = 120; // debounce de selectionchange
  // Debe coincidir con MAX_TEXT_LENGTH del backend (.env).
  const MAX_TEXT_LENGTH = 32000;

  let sendInProgress = false;
  let selectionRange = null; // { text, range } de la selección activa
  let updateTimer = 0;
  let scrollRaf = 0;
  let toastTimer = 0;
  let showFabEnabled = true; // preferencia del usuario (options → paso 4)

  try {
    chrome.storage.local.get({ showFab: true }).then((cfg) => {
      showFabEnabled = cfg.showFab !== false;
      if (!showFabEnabled) hideButton();
    });
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area === "local" && changes.showFab) {
        showFabEnabled = changes.showFab.newValue !== false;
        if (!showFabEnabled) hideButton();
      }
    });
  } catch (_) {
    /* storage no disponible en este contexto: se usa el valor por defecto */
  }

  const SPARKLE_SVG =
    '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
    '<path d="M12 2C13.2 7.6 16.4 10.8 22 12C16.4 13.2 13.2 16.4 12 22' +
    'C10.8 16.4 7.6 13.2 2 12C7.6 10.8 10.8 7.6 12 2Z"/></svg>';

  /* ------------------------------------------------------------------ */
  /* Shadow DOM: host + botón + toast                                    */
  /* ------------------------------------------------------------------ */

  const host = document.createElement("div");
  host.dataset.extdator = "host";
  const shadow = host.attachShadow({ mode: "closed" });

  const style = document.createElement("style");
  style.textContent = `
    :host { all: initial; }
    * { box-sizing: border-box; }
    .btn {
      position: fixed;
      z-index: 2147483647;
      width: ${BUTTON_W}px; height: ${BUTTON_H}px;
      display: none;
      align-items: center; justify-content: center;
      border-radius: 9px;
      border: 1px solid rgba(139, 124, 255, 0.35);
      background: linear-gradient(135deg, #191c26 0%, #12141c 100%);
      color: #c9c2ff;
      cursor: pointer;
      padding: 0; margin: 0;
      box-shadow: 0 6px 18px rgba(0, 0, 0, 0.45);
      opacity: 0;
      transform: translateY(5px) scale(0.9);
      transition: opacity .13s ease, transform .13s ease,
                  background .15s ease, border-color .15s ease;
    }
    .btn.visible { display: inline-flex; }
    .btn.shown { opacity: 1; transform: translateY(0) scale(1); }
    .btn:hover { background: #262b3d; border-color: rgba(139,124,255,.8); color: #fff; }
    .btn:active { transform: translateY(0) scale(0.92); }
    .btn.sending { opacity: .5; pointer-events: none; }
    .btn svg { width: 15px; height: 15px; display: block; }
    .toast {
      position: fixed;
      top: 16px; left: 50%;
      transform: translateX(-50%) translateY(-8px);
      z-index: 2147483647;
      max-width: min(420px, 80vw);
      padding: 8px 14px;
      border-radius: 8px;
      font: 13px/1.45 "Segoe UI", Roboto, sans-serif;
      color: #e8eaf2;
      background: #191c26;
      border: 1px solid rgba(255, 255, 255, .12);
      box-shadow: 0 6px 18px rgba(0, 0, 0, .45);
      opacity: 0;
      pointer-events: none;
      transition: opacity .18s ease, transform .18s ease;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
    .toast.success { border-color: rgba(61, 220, 151, .5); }
    .toast.error   { border-color: rgba(255, 107, 107, .6); }
  `;

  const btn = document.createElement("button");
  btn.className = "btn";
  btn.type = "button";
  btn.title = "Enviar a Ollama (Extdator)";
  btn.setAttribute("aria-label", "Enviar selección a Ollama");
  btn.innerHTML = SPARKLE_SVG;

  const toast = document.createElement("div");
  toast.className = "toast";

  shadow.append(style, btn, toast);
  (document.documentElement || document.body).appendChild(host);

  function showToast(message, tone) {
    if (!message) return;
    toast.textContent = message;
    toast.className = `toast ${tone || ""} show`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toast.className = `toast ${tone || ""}`;
    }, 3200);
  }

  /* ------------------------------------------------------------------ */
  /* Detección y posicionamiento del botón                               */
  /* ------------------------------------------------------------------ */

  function readSelection() {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return null;
    const text = sel.toString();
    if (!text || text.trim().length === 0) return null;
    const range = sel.getRangeAt(0);
    let rects;
    try {
      rects = range.getClientRects();
    } catch (_) {
      return null;
    }
    if (!rects || rects.length === 0) return null;
    // El primer rect de tamaño no nulo corresponde al inicio de la selección.
    let anchor = null;
    for (const r of rects) {
      if (r.width > 0 || r.height > 0) { anchor = r; break; }
    }
    if (!anchor) anchor = rects[0];
    if (!anchor) return null;
    return { text, range };
  }

  function placeButton() {
    if (!selectionRange) { hideButton(); return; }
    let rects;
    try {
      rects = selectionRange.range.getClientRects();
    } catch (_) {
      hideButton();
      return;
    }
    if (!rects || rects.length === 0) { hideButton(); return; }
    let anchor = null;
    for (const r of rects) {
      if (r.width > 0 || r.height > 0) { anchor = r; break; }
    }
    if (!anchor) { hideButton(); return; }

    const vw = window.innerWidth;
    const vh = window.innerHeight;
    // Selección fuera del viewport → ocultar (evita botones "fantasma").
    if (anchor.bottom < -4 || anchor.top > vh + 4) { hideButton(); return; }

    let left = anchor.left;
    let top = anchor.top - BUTTON_H - GAP;
    if (top < MARGIN) top = anchor.bottom + GAP; // sin sitio arriba → debajo
    left = Math.min(Math.max(MARGIN, left), vw - BUTTON_W - MARGIN);
    top = Math.min(Math.max(MARGIN, top), vh - BUTTON_H - MARGIN);

    btn.style.left = `${Math.round(left)}px`;
    btn.style.top = `${Math.round(top)}px`;

    if (!btn.classList.contains("visible")) {
      btn.classList.add("visible");
      requestAnimationFrame(() => btn.classList.add("shown"));
    }
  }

  function hideButton() {
    btn.classList.remove("visible", "shown", "sending");
    btn.style.left = "-9999px";
    btn.style.top = "-9999px";
    selectionRange = null;
  }

  function scheduleUpdate(delay) {
    clearTimeout(updateTimer);
    updateTimer = setTimeout(() => {
      selectionRange = readSelection();
      if (selectionRange && showFabEnabled) placeButton(); else hideButton();
    }, delay === undefined ? UPDATE_DELAY_MS : delay);
  }

  document.addEventListener("selectionchange", () => scheduleUpdate(), true);
  window.addEventListener("mouseup", () => scheduleUpdate(0), true);
  window.addEventListener("keyup", (e) => {
    if (e.key === "Escape") hideButton();
  }, true);

  // Reposicionar durante el scroll (throttle con rAF, listener pasivo).
  window.addEventListener("scroll", () => {
    if (!selectionRange || scrollRaf) return;
    scrollRaf = requestAnimationFrame(() => {
      scrollRaf = 0;
      if (selectionRange) placeButton();
    });
  }, { passive: true, capture: true });

  // Al empezar una nueva interacción fuera del botón, ocultarlo de inmediato
  // para que nunca interfiera con la siguiente selección.
  //
  // IMPORTANTE: el shadow root es CLOSED, así que un clic dentro del shadow se
  // ve desde fuera con el target retargetizado al host (los nodos internos,
  // incluido btn, NO aparecen en composedPath() para listeners externos).
  // Comparar con `e.composedPath().includes(btn)` sería siempre false y
  // ocultaría el botón en pleno clic (bug verificado por CDP: el click nunca
  // llegaba al botón). Se comprueba el target retargetizado Y, por si el root
  // cambiara a open algún día, también el path completo.
  document.addEventListener("mousedown", (e) => {
    if (e.target === host || e.composedPath().includes(btn)) return;
    hideButton();
  }, true);

  // Conservar la selección: impedir que el mousedown sobre el botón la borre.
  btn.addEventListener("mousedown", (e) => {
    e.preventDefault();
    e.stopPropagation();
  });

  /* ------------------------------------------------------------------ */
  /* Envío                                                               */
  /* ------------------------------------------------------------------ */

  btn.addEventListener("click", async () => {
    if (sendInProgress) return; // sin envíos duplicados por el mismo clic
    const state = readSelection();
    if (!state) { hideButton(); return; }
    const text = state.text; // copia exacta: acentos, símbolos y saltos intactos
    if (text.length > MAX_TEXT_LENGTH) {
      showToast(`El texto supera el límite de ${MAX_TEXT_LENGTH} caracteres.`, "error");
      hideButton();
      return;
    }
    sendInProgress = true;
    btn.classList.add("sending");
    try {
      const response = await chrome.runtime.sendMessage({
        type: "extdator:send-selection",
        text,
        sourceUrl: location.href,
      });
      if (response && response.ok) {
        showToast(response.message || "Texto enviado a Ollama.", "success");
      } else {
        showToast((response && response.error) || "No se pudo enviar la selección.", "error");
      }
    } catch (err) {
      showToast("Error interno de la extensión: " +
        (err && err.message ? err.message : String(err)), "error");
    } finally {
      sendInProgress = false;
      scheduleUpdate(0);
    }
  });
})();
