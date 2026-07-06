// ui-core.js — the helpers every ib dashboard used to re-declare (REFACTOR P5.4).
// Served from web_common/static via the shared StaticFiles fallthrough:
//   brain/forge (mounted at "/"):  <script src="ui-core.js">
//   harvest    (mounted "/static"): <script src="/static/ui-core.js">
// Load BEFORE the page's own script. Exposes a single `ibUI` global — no modules,
// same zero-build style as toast.js.
"use strict";
const ibUI = (() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
  const fmt = (v, s = "") => (v === null || v === undefined ? "—" : v + s);
  const pct = (v) => Math.round((v ?? 0) * 100);
  const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

  // fire-and-forget POST (button actions). Never throws.
  const post = (url) => fetch(url, { method: "POST" }).catch(() => {});
  // JSON POST; resolves to the parsed response, or null on any failure.
  const postJSON = (url, body) =>
    fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body ?? {}) })
      .then((r) => r.json()).catch(() => null);

  // Theme persistence: restore from localStorage, keep the <select> + storage in
  // sync. Each app keeps its own storage key ("ib-theme", "ibf-theme", "ibh-theme")
  // so themes stay per-app until themes.css itself is unified.
  function theme(sel, key, dflt) {
    const saved = localStorage.getItem(key);
    if (saved) document.documentElement.dataset.theme = saved;
    if (sel) {
      sel.value = saved || dflt || sel.value;
      sel.onchange = () => {
        document.documentElement.dataset.theme = sel.value;
        localStorage.setItem(key, sel.value);
      };
    }
  }

  // WebSocket with auto-reconnect; parses each frame as JSON and swallows bad
  // frames (the pre-P5.4 copies in app.js/focus.js/group.js were identical).
  function wsReconnect(onMsg, { path = "/ws", delay = 1500, onClose } = {}) {
    (function connect() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}${path}`);
      ws.onmessage = (e) => { try { onMsg(JSON.parse(e.data)); } catch (_) {} };
      ws.onclose = () => { if (onClose) onClose(); setTimeout(connect, delay); };
      ws.onerror = () => ws.close();
    })();
  }

  return { $, esc, fmt, pct, cap, post, postJSON, theme, wsReconnect };
})();
