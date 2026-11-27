/* UNnexor — content script.
 *
 * Responsabilidades (solo estas):
 *   1. Detectar selecciones de texto válidas en la página.
 *   2. Mostrar/posicionar/ocultar el botón contextual con la X de UNnexor
 *      (aislado con Shadow DOM).
 *   3. Ofrecer dos acciones al pulsarlo:
 *        A) Enviar directamente: la selección va tal cual al modelo
 *           (comportamiento histórico).
 *        B) Consultar: un panel pequeño para escribir una pregunta sobre la
 *           selección; se envían ambas.
 *   4. Enviar al background service worker y mostrar un toast con el
 *      resultado.
 *
 * No toca el CSS del sitio: todo el estilo vive dentro del Shadow DOM.
 * Los identificadores internos (``__extdatorInjected``, ``extdator:*``) se
 * conservan por compatibilidad entre versiones de la extensión y del puente.
 */
(() => {
  "use strict";

  // Protección contra doble inyección (p. ej. recargas de la extensión).
  if (window.__extdatorInjected) return;
  window.__extdatorInjected = "content";

  const BUTTON_W = 34;
  const BUTTON_H = 34;
  const GAP = 8;
  const MARGIN = 6;
  const UPDATE_DELAY_MS = 120; // debounce de selectionchange
  // Debe coincidir con MAX_TEXT_LENGTH del backend (.env).
  const MAX_TEXT_LENGTH = 32000;
  // Debe coincidir con MAX_QUESTION_LENGTH del backend (server.py).
  const MAX_QUESTION_LENGTH = 4000;

  let sendInProgress = false;
  let selectionRange = null; // { text, range } de la selección activa
  let pending = null; // { text, rect } capturado al pulsar el botón
  let mode = "idle"; // idle | menu | ask
  let updateTimer = 0;
  let scrollRaf = 0;
  let toastTimer = 0;
  let showFabEnabled = true; // preferencia del usuario (options → paso 4)

  try {
    chrome.storage.local.get({ showFab: true }).then((cfg) => {
      showFabEnabled = cfg.showFab !== false;
      if (!showFabEnabled) hideAll();
    });
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area === "local" && changes.showFab) {
        showFabEnabled = changes.showFab.newValue !== false;
        if (!showFabEnabled) hideAll();
      }
    });
  } catch (_) {
    /* storage no disponible en este contexto: se usa el valor por defecto */
  }

  // La X de UNnexor (misma geometría que branding/unnexor-x.svg). El bloque
  // lo regenera scripts/make_brand_assets.py: no editar a mano.
  /* BEGIN UNNEXOR_X_SVG (generado) */
  const UNNEXOR_X_SVG =
    "<svg viewBox=\"0 0 581 356\" aria-hidden=\"true\" focusable=\"false\"><defs><linearGradient id=\"unx-slash\" gradientUnits=\"userSpaceOnUse\" x1=\"575\" y1=\"2\" x2=\"-0\" y2=\"357\"><stop offset=\"0\" stop-color=\"#00F4FF\"/><stop offset=\"0.18\" stop-color=\"#00D6FF\"/><stop offset=\"0.4\" stop-color=\"#1C4CF6\"/><stop offset=\"0.58\" stop-color=\"#5A12F2\"/><stop offset=\"0.72\" stop-color=\"#A000FA\"/><stop offset=\"0.88\" stop-color=\"#F205FC\"/><stop offset=\"1\" stop-color=\"#FF2CE8\"/></linearGradient><linearGradient id=\"unx-inner\" gradientUnits=\"userSpaceOnUse\" x1=\"425\" y1=\"7\" x2=\"185\" y2=\"287\"><stop offset=\"0\" stop-color=\"#00E0FF\"/><stop offset=\"0.3\" stop-color=\"#00B8FA\"/><stop offset=\"0.58\" stop-color=\"#0A3FC8\"/><stop offset=\"0.82\" stop-color=\"#0A0A60\" stop-opacity=\"0.6\"/><stop offset=\"1\" stop-color=\"#05001A\" stop-opacity=\"0.15\"/></linearGradient><linearGradient id=\"unx-floor\" gradientUnits=\"userSpaceOnUse\" x1=\"65\" y1=\"297\" x2=\"225\" y2=\"317\"><stop offset=\"0\" stop-color=\"#7A00E6\"/><stop offset=\"0.55\" stop-color=\"#4A00B8\"/><stop offset=\"1\" stop-color=\"#2A0078\"/></linearGradient><linearGradient id=\"unx-upper\" gradientUnits=\"userSpaceOnUse\" x1=\"25\" y1=\"17\" x2=\"255\" y2=\"129\"><stop offset=\"0\" stop-color=\"#00CFFF\"/><stop offset=\"0.4\" stop-color=\"#1F45FF\"/><stop offset=\"0.75\" stop-color=\"#5A0BFF\"/><stop offset=\"1\" stop-color=\"#8C00FF\"/></linearGradient><linearGradient id=\"unx-lower\" gradientUnits=\"userSpaceOnUse\" x1=\"325\" y1=\"247\" x2=\"575\" y2=\"337\"><stop offset=\"0\" stop-color=\"#1028D0\"/><stop offset=\"0.35\" stop-color=\"#0A6BF5\"/><stop offset=\"0.7\" stop-color=\"#00C8FD\"/><stop offset=\"1\" stop-color=\"#00F6FF\"/></linearGradient></defs><path data-part=\"lower-right\" fill=\"url(#unx-lower)\" d=\"M401,356 581,356 399.2,174.2 365.7,205.8 477,317 423,317 338,232 306.6,261.6Z\"/><path data-part=\"upper-left\" fill=\"url(#unx-upper)\" d=\"M6,0 190,0 295.3,105.3 263.9,134.9 175,46 76,46 140.5,65.6 236.1,161.1 200.6,194.6Z\"/><path data-part=\"slash-floor\" fill=\"url(#unx-floor)\" d=\"M107.2,295 205,295 180.1,318.5 82.3,318.5Z\"/><path data-part=\"slash-inner\" fill=\"url(#unx-inner)\" d=\"M419.9,0 485.7,0 173,295 107.2,295Z\"/><path data-part=\"slash\" fill=\"url(#unx-slash)\" d=\"M484.2,0 572.8,0 195.5,356 0,356 180.1,186.1 200.7,206.7 83.9,317 180.2,317 465.3,48 433.3,48Z\"/></svg>";
  /* END UNNEXOR_X_SVG */

  /* ------------------------------------------------------------------ */
  /* Shadow DOM: host + botón + menú + panel de consulta + toast         */
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
      border-radius: 10px;
      border: 1px solid rgba(0, 229, 255, 0.35);
      background: linear-gradient(135deg, #161a2e 0%, #07080f 100%);
      cursor: pointer;
      padding: 0; margin: 0;
      box-shadow: 0 6px 18px rgba(0, 0, 0, 0.45),
                  inset 0 0 0 1px rgba(242, 5, 252, 0.10);
      opacity: 0;
      transform: translateY(5px) scale(0.9);
      transition: opacity .13s ease, transform .13s ease,
                  border-color .15s ease, box-shadow .15s ease;
    }
    .btn.visible { display: inline-flex; }
    .btn.shown { opacity: 1; transform: translateY(0) scale(1); }
    .btn:hover, .btn.open {
      border-color: rgba(0, 229, 255, 0.8);
      box-shadow: 0 6px 20px rgba(0, 0, 0, 0.5), 0 0 0 3px rgba(58, 43, 255, 0.25);
    }
    .btn:active { transform: translateY(0) scale(0.94); }
    .btn.sending { opacity: .55; pointer-events: none; }
    .btn svg { width: 22px; height: 14px; display: block; }

    .menu, .ask {
      position: fixed;
      z-index: 2147483647;
      display: none;
      font: 13px/1.4 "Segoe UI", Roboto, system-ui, sans-serif;
      color: #e8eaf2;
      background: #11131d;
      border: 1px solid rgba(255, 255, 255, .12);
      border-radius: 12px;
      box-shadow: 0 12px 32px rgba(0, 0, 0, .55);
    }
    .menu.visible, .ask.visible { display: block; }
    .menu { padding: 5px; min-width: 214px; }
    .item {
      all: unset;
      box-sizing: border-box;
      display: flex; align-items: flex-start; gap: 10px;
      width: 100%;
      padding: 8px 10px;
      border-radius: 8px;
      cursor: pointer;
      color: #e8eaf2;
    }
    .item:hover, .item:focus-visible { background: #1d2133; outline: none; }
    .item .ico {
      flex: none; width: 22px; height: 22px; border-radius: 6px;
      display: inline-flex; align-items: center; justify-content: center;
      font-size: 13px;
      background: linear-gradient(135deg, rgba(0,229,255,.18), rgba(242,5,252,.18));
    }
    .item strong { display: block; font-weight: 600; font-size: 13px; }
    .item small { display: block; color: #9aa0b5; font-size: 11.5px; }

    .ask { width: min(380px, calc(100vw - 16px)); padding: 12px; }
    .ask header {
      display: flex; align-items: center; gap: 8px;
      font-weight: 600; margin-bottom: 8px;
    }
    .ask header svg { width: 20px; height: 13px; }
    .ask header .sp { flex: 1; }
    .ask .x {
      all: unset; cursor: pointer; color: #9aa0b5; padding: 2px 6px;
      border-radius: 6px; font-size: 14px;
    }
    .ask .x:hover { background: #1d2133; color: #fff; }
    .quote {
      max-height: 72px; overflow: auto;
      padding: 6px 8px; margin-bottom: 8px;
      border-left: 3px solid #3a2bff;
      background: #0b0d16; border-radius: 6px;
      color: #b9bdd0; font-size: 12px; white-space: pre-wrap; word-break: break-word;
    }
    .meta { color: #7d839a; font-size: 11px; margin: -4px 0 8px; }
    textarea {
      width: 100%; min-height: 64px; max-height: 180px; resize: vertical;
      padding: 8px 10px;
      font: inherit; color: #e8eaf2;
      background: #0b0d16;
      border: 1px solid rgba(255,255,255,.14); border-radius: 8px;
      outline: none;
    }
    textarea:focus { border-color: rgba(0,229,255,.7); }
    .row { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
    .row .hint { flex: 1; color: #7d839a; font-size: 11px; }
    .b {
      all: unset; cursor: pointer;
      padding: 6px 12px; border-radius: 8px; font-weight: 600; font-size: 12.5px;
    }
    .b.cancel { color: #c4c8d8; background: #1b1e2b; }
    .b.cancel:hover { background: #262a3b; }
    .b.go {
      color: #fff;
      background: linear-gradient(135deg, #00b8f0 0%, #3a2bff 55%, #c010f0 100%);
    }
    .b.go:hover { filter: brightness(1.1); }
    .b[disabled] { opacity: .5; pointer-events: none; }
    .status { margin-top: 8px; font-size: 12px; color: #9aa0b5; min-height: 16px; }
    .status.error { color: #ff8a8a; }

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
      background: #11131d;
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
  btn.title = "UNnexor: enviar o consultar la selección";
  btn.setAttribute("aria-label", "UNnexor: enviar o consultar la selección");
  btn.setAttribute("aria-haspopup", "menu");
  btn.innerHTML = UNNEXOR_X_SVG;

  const menu = document.createElement("div");
  menu.className = "menu";
  menu.setAttribute("role", "menu");
  menu.innerHTML = `
    <button class="item" type="button" role="menuitem" data-action="send">
      <span class="ico">➤</span>
      <span><strong>Enviar directamente</strong>
      <small>El texto seleccionado va tal cual al modelo.</small></span>
    </button>
    <button class="item" type="button" role="menuitem" data-action="ask">
      <span class="ico">?</span>
      <span><strong>Consultar…</strong>
      <small>Escribe una pregunta sobre el texto.</small></span>
    </button>`;

  const ask = document.createElement("div");
  ask.className = "ask";
  ask.setAttribute("role", "dialog");
  ask.setAttribute("aria-label", "Consultar a UNnexor sobre la selección");
  ask.innerHTML = `
    <header>${UNNEXOR_X_SVG}<span>Consultar sobre la selección</span>
      <span class="sp"></span>
      <button class="x" type="button" data-action="cancel" title="Cancelar (Esc)">✕</button>
    </header>
    <div class="quote"></div>
    <div class="meta"></div>
    <textarea maxlength="${MAX_QUESTION_LENGTH}"
      placeholder="Ej.: Explícame este concepto con un ejemplo práctico y sencillo."></textarea>
    <div class="row">
      <span class="hint">Ctrl+Enter para enviar · Esc para cancelar</span>
      <button class="b cancel" type="button" data-action="cancel">Cancelar</button>
      <button class="b go" type="button" data-action="submit">Consultar</button>
    </div>
    <div class="status" aria-live="polite"></div>`;
  const quoteEl = ask.querySelector(".quote");
  const metaEl = ask.querySelector(".meta");
  const questionEl = ask.querySelector("textarea");
  const statusEl = ask.querySelector(".status");
  const submitEl = ask.querySelector('[data-action="submit"]');

  const toast = document.createElement("div");
  toast.className = "toast";

  shadow.append(style, btn, menu, ask, toast);
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

  function firstRect(range) {
    let rects;
    try {
      rects = range.getClientRects();
    } catch (_) {
      return null;
    }
    if (!rects || rects.length === 0) return null;
    // El primer rect de tamaño no nulo corresponde al inicio de la selección.
    for (const r of rects) {
      if (r.width > 0 || r.height > 0) return r;
    }
    return rects[0] || null;
  }

  function readSelection() {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return null;
    const text = sel.toString();
    if (!text || text.trim().length === 0) return null;
    const range = sel.getRangeAt(0);
    if (!firstRect(range)) return null;
    return { text, range };
  }

  function placeButton() {
    if (!selectionRange) { hideButton(); return; }
    const anchor = firstRect(selectionRange.range);
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

  // Coloca un panel flotante junto al botón sin salirse de la ventana.
  function placeNear(el) {
    const b = btn.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    let left = b.left;
    let top = b.bottom + 6;
    if (top + h > vh - MARGIN) top = b.top - h - 6; // sin sitio abajo → arriba
    left = Math.min(Math.max(MARGIN, left), vw - w - MARGIN);
    top = Math.min(Math.max(MARGIN, top), vh - h - MARGIN);
    el.style.left = `${Math.round(left)}px`;
    el.style.top = `${Math.round(top)}px`;
  }

  function hideButton() {
    btn.classList.remove("visible", "shown", "sending", "open");
    btn.style.left = "-9999px";
    btn.style.top = "-9999px";
    selectionRange = null;
  }

  function closeMenu() {
    menu.classList.remove("visible");
    btn.classList.remove("open");
    if (mode === "menu") mode = "idle";
  }

  function closeAsk() {
    ask.classList.remove("visible");
    statusEl.textContent = "";
    statusEl.className = "status";
    if (mode === "ask") mode = "idle";
  }

  function hideAll() {
    closeMenu();
    closeAsk();
    pending = null;
    mode = "idle";
    hideButton();
  }

  function scheduleUpdate(delay) {
    // Con el panel de consulta abierto la selección de la página se pierde
    // (el foco pasa al cuadro de texto): no se recalcula nada.
    if (mode !== "idle") return;
    clearTimeout(updateTimer);
    updateTimer = setTimeout(() => {
      if (mode !== "idle") return;
      selectionRange = readSelection();
      if (selectionRange && showFabEnabled) placeButton(); else hideButton();
    }, delay === undefined ? UPDATE_DELAY_MS : delay);
  }

  document.addEventListener("selectionchange", () => scheduleUpdate(), true);
  window.addEventListener("mouseup", () => scheduleUpdate(0), true);
  window.addEventListener("keyup", (e) => {
    if (e.key === "Escape" && mode === "idle") hideButton();
  }, true);

  // Reposicionar durante el scroll (throttle con rAF, listener pasivo).
  window.addEventListener("scroll", () => {
    if (scrollRaf) return;
    if (!selectionRange && mode === "idle") return;
    scrollRaf = requestAnimationFrame(() => {
      scrollRaf = 0;
      if (mode === "menu") closeMenu();
      if (selectionRange && mode === "idle") placeButton();
    });
  }, { passive: true, capture: true });

  // Al empezar una nueva interacción fuera del UI, ocultarlo de inmediato
  // para que nunca interfiera con la siguiente selección.
  //
  // IMPORTANTE: el shadow root es CLOSED, así que un clic dentro del shadow se
  // ve desde fuera con el target retargetizado al host. Se comprueba el
  // target retargetizado Y, por si el root cambiara a open, el path completo.
  document.addEventListener("mousedown", (e) => {
    if (e.target === host || e.composedPath().includes(btn)) return;
    if (mode === "ask") return; // el panel solo se cierra de forma explícita
    hideAll();
  }, true);

  // Conservar la selección: impedir que el mousedown sobre el botón o el
  // menú la borre.
  for (const el of [btn, menu]) {
    el.addEventListener("mousedown", (e) => {
      e.preventDefault();
      e.stopPropagation();
    });
  }

  // Las teclas pulsadas en el panel no deben activar atajos de la página.
  for (const type of ["keydown", "keyup", "keypress"]) {
    ask.addEventListener(type, (e) => e.stopPropagation());
  }

  /* ------------------------------------------------------------------ */
  /* Acciones                                                            */
  /* ------------------------------------------------------------------ */

  function snapshotSelection() {
    const state = readSelection();
    if (!state) return null;
    const text = state.text; // copia exacta: acentos, símbolos y saltos intactos
    if (text.length > MAX_TEXT_LENGTH) {
      showToast(`El texto supera el límite de ${MAX_TEXT_LENGTH} caracteres.`, "error");
      hideAll();
      return null;
    }
    return { text };
  }

  btn.addEventListener("click", () => {
    if (sendInProgress) return;
    if (mode === "menu") { closeMenu(); return; }
    const snap = snapshotSelection();
    if (!snap) { hideAll(); return; }
    pending = snap;
    mode = "menu";
    btn.classList.add("open");
    menu.classList.add("visible");
    placeNear(menu);
    const first = menu.querySelector(".item");
    if (first) first.focus({ preventScroll: true });
  });

  menu.addEventListener("click", (e) => {
    const item = e.target.closest && e.target.closest("[data-action]");
    if (!item || !pending) return;
    const action = item.dataset.action;
    closeMenu();
    if (action === "send") {
      send(pending.text, "");
    } else if (action === "ask") {
      openAsk();
    }
  });

  menu.addEventListener("keydown", (e) => {
    const items = Array.from(menu.querySelectorAll(".item"));
    const index = items.indexOf(shadow.activeElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = (index + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length;
      items[next].focus();
    } else if (e.key === "Escape") {
      e.preventDefault();
      closeMenu();
    }
  });

  function openAsk() {
    if (!pending) return;
    mode = "ask";
    const text = pending.text;
    quoteEl.textContent = text.length > 600 ? text.slice(0, 600) + "…" : text;
    metaEl.textContent = `${text.length.toLocaleString()} caracteres seleccionados`;
    questionEl.value = "";
    statusEl.textContent = "";
    statusEl.className = "status";
    submitEl.disabled = false;
    ask.classList.add("visible");
    placeNear(ask);
    setTimeout(() => questionEl.focus({ preventScroll: true }), 0);
  }

  function cancelAsk() {
    closeAsk();
    pending = null;
    hideButton();
  }

  ask.addEventListener("click", (e) => {
    const el = e.target.closest && e.target.closest("[data-action]");
    if (!el) return;
    if (el.dataset.action === "cancel") cancelAsk();
    else if (el.dataset.action === "submit") submitAsk();
  });

  questionEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      submitAsk();
    } else if (e.key === "Escape") {
      e.preventDefault();
      cancelAsk();
    }
  });

  async function submitAsk() {
    if (!pending || sendInProgress) return;
    const question = questionEl.value.trim();
    if (!question) {
      statusEl.textContent = "Escribe una pregunta o usa «Enviar directamente».";
      statusEl.className = "status error";
      questionEl.focus();
      return;
    }
    statusEl.textContent = "Enviando consulta a UNnexor…";
    statusEl.className = "status";
    submitEl.disabled = true;
    const result = await send(pending.text, question, true);
    if (result && result.ok) {
      cancelAsk();
    } else {
      // El panel queda abierto con la pregunta para reintentar.
      submitEl.disabled = false;
      statusEl.textContent = (result && result.error) || "No se pudo enviar la consulta.";
      statusEl.className = "status error";
    }
  }

  async function send(text, question, fromPanel) {
    if (sendInProgress) return null;
    sendInProgress = true;
    btn.classList.add("sending");
    let result;
    try {
      const response = await chrome.runtime.sendMessage({
        type: "extdator:send-selection",
        text,
        question,
        sourceUrl: location.href,
      });
      result = response || { ok: false, error: "Sin respuesta de la extensión." };
    } catch (err) {
      result = {
        ok: false,
        error: "Error interno de la extensión: " +
          (err && err.message ? err.message : String(err)),
      };
    } finally {
      sendInProgress = false;
      btn.classList.remove("sending");
    }
    if (result.ok) {
      showToast(result.message || "Texto enviado a UNnexor.", "success");
    } else if (!fromPanel) {
      showToast(result.error || "No se pudo enviar la selección.", "error");
    }
    if (!fromPanel) {
      pending = null;
      mode = "idle";
      scheduleUpdate(0);
    }
    return result;
  }
})();
