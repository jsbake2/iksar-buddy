// Shared toast + OS-notification helper (forge & brain use identical copies).
// Backend sets snapshot.notice = {ts, title, detail, level, sys}; the client diffs
// the ts and pops a stacking toast + (sys) a browser Notification. level: info|good|warn|error.
const ibNotify = (() => {
  let host = null, lastTs = 0, primed = false, permAsked = false;
  function ensureHost() {
    if (host) return host;
    host = document.createElement("div");
    host.id = "ibToasts";
    document.body.appendChild(host);
    return host;
  }
  function askPerm() {
    if (permAsked || !("Notification" in window)) return;
    permAsked = true;
    if (Notification.permission === "default") { try { Notification.requestPermission(); } catch (_) {} }
  }
  function osNotify(title, body) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    try { new Notification(title, { body: body || "", tag: "ib-" + Math.round(Date.now() / 1000) }); } catch (_) {}
  }
  function toast(title, detail, level) {
    const h = ensureHost();
    const el = document.createElement("div");
    el.className = "ib-toast " + (level || "warn");
    el.innerHTML = `<div class="ib-toast-t"></div><div class="ib-toast-d"></div><span class="ib-toast-x">✕</span>`;
    el.querySelector(".ib-toast-t").textContent = title;
    const d = el.querySelector(".ib-toast-d");
    if (detail) d.textContent = detail; else d.remove();
    const kill = () => { el.classList.add("out"); setTimeout(() => el.remove(), 250); };
    el.querySelector(".ib-toast-x").onclick = (e) => { e.stopPropagation(); kill(); };
    el.onclick = kill;
    h.appendChild(el);
    requestAnimationFrame(() => el.classList.add("in"));
    setTimeout(kill, level === "error" ? 11000 : 6000);
  }
  return {
    ask: askPerm,
    // manual toast (local UI feedback, no OS notification)
    show(title, detail, level) { toast(title, detail, level); },
    // wire a header button to the shared ntfy phone-push on/off state (/api/push)
    async phone(btn) {
      if (!btn) return;
      const paint = (s) => {
        btn.dataset.on = s.enabled ? "1" : "0";
        btn.disabled = !s.configured;
        btn.textContent = (s.configured && s.enabled) ? "🔔 phone" : "🔕 phone";
        btn.title = !s.configured ? "phone push not set up (see ~/ib-data/push.yaml)"
          : s.enabled ? "phone alerts ON — click to mute (silences all ib apps)"
                      : "phone alerts OFF — click to enable";
      };
      try { paint(await (await fetch("/api/push")).json()); } catch (_) {}
      btn.onclick = async () => {
        const cur = btn.dataset.on === "1";
        try {
          paint(await (await fetch("/api/push", { method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled: !cur }) })).json());
        } catch (_) {}
      };
      // reflect out-of-band changes (another app's toggle) every 10s
      setInterval(async () => { try { paint(await (await fetch("/api/push")).json()); } catch (_) {} }, 10000);
    },
    // drive from a websocket snapshot: fires once per new notice.ts
    fromSnapshot(snap) {
      const n = snap && snap.notice;
      if (!primed) { primed = true; lastTs = (n && n.ts) || 0; return; }  // skip stale notice on (re)connect
      if (!n || !n.ts || n.ts <= lastTs) return;
      lastTs = n.ts;
      toast(n.title, n.detail, n.level);
      if (n.sys) osNotify(n.title, n.detail);
    },
  };
})();
// ask for OS-notification permission on the first user gesture (browsers require it)
document.addEventListener("click", () => ibNotify.ask(), { once: true });
