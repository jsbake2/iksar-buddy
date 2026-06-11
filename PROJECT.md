# PROJECT.md — EQ2 Healer Bot

Technical spec. `CLAUDE.md` covers operating rules; this covers what we're building.

---

## 1. Summary

Automate a **Defiler healer** as the second box behind a human **Shadowknight** tank on a privately-hosted EQ2Emu server. The bot senses the EQ2 client (pixels primary, OCR + logs secondary), runs a decision loop, and injects keypresses bound to the healer's hotbar. Server is never touched.

## 2. Topology

```
CachyOS server (host)                         Windows 10 VM (guest, on same host via virt-manager/KVM)
┌────────────────────────────┐   TCP socket  ┌─────────────────────────────────────┐
│ BRAIN                       │◄─────────────►│ AGENT                               │
│  - state machine            │  state events │  - pixel capture (dxcam/mss)        │
│  - decision loop (heal/cure │ ◄──────────── │  - filtered-chat OCR (tesseract)    │
│    /rez/follow/buff)        │               │  - combat-log tailer                │
│  - policy + class config    │  keypress cmds│  - keypress injector (SendInput)    │
│  - web dashboard            │ ────────────► │  - chat-safety guard (fail-closed)  │
│  - manual override controls │               │  - launcher (boot→client→in-group)  │
└────────────────────────────┘               └─────────────────────────────────────┘
```

- **Brain** holds all logic, config, and the UI. Runs on the CachyOS host (or any box; host is default).
- **Agent** is dumb: sense and press. No decisions. Lives in the VM, autostarts on Windows login.
- Split exists so iteration happens brain-side where the owner works; the VM snapshot rarely changes.

## 3. Infrastructure

- **Host:** CachyOS, virt-manager/KVM. Also runs unrelated GPU/AI workloads on a 4070 and other homelab services — **must not be disrupted** (see `CLAUDE.md`).
- **GPU:** none passed through. 4070 stays with host AI work. EQ2 runs on **software/WARP rendering** in the guest — fine, the game is from 2004 and the bot doesn't care if it's ugly. Bonus: software render is deterministic, no driver updates shifting colors/AA under the pixel sensor.
- **Guest:** Windows 10 Pro, Q35 + OVMF, virtio disk/net, QXL/virtio video. ~6 pinned vCPUs (CPU is also the renderer; pin to one CCX on Ryzen), 8 GB RAM. Windows runs unactivated (cosmetic limits only; irrelevant headless). Install with network off → local account, no MS account/OneDrive noise.
- **Client config (load-bearing for sensors):** fixed windowed resolution, **locked UI scale**, particle/effects minimized, visual effects off, never sleep/lock. **Snapshot the VM once calibrated** so a Windows update can't nudge the UI and silently break pixel coords.

## 4. Sensor hierarchy

Ordered by trust. This ordering is a hard design input from the owner's prior experience — respect it.

1. **Pixels — PRIMARY.** Ground truth for *state*: per-member HP%, own power, cast-bar (don't cast while casting), ward/buff-icon presence. Sample a horizontal scanline per HP bar → fill ratio. 10–15 Hz is plenty. **Logs are NOT trusted for combat events — they drop/buffer — so pixels carry the loop.**
2. **Manual override hotkeys — override layer.** Host-side hotkeys that force state transitions when auto-detection desyncs: `force combat`, `force OOC`, `force follow`. Override suppresses auto-detection until the next clean transition.
3. **Combat logs — coarse only.** Reliable enough for combat **start/end** signal into the state machine; nothing finer. One input among several, never the sole trigger for anything critical.
4. **Filtered-chat OCR — safety net / event catch.** A dedicated chat window, filtered to only relevant channels, **large font, black background, high-contrast text** — a purpose-built OCR target with near-zero noise. Tesseract is highly reliable on this. Polls 2–4 Hz. Catches: follow-drop lines, group-invite dialog, rez offers, recovery signals.
5. **Process memory — DEFERRED.** Perfect data but offset-fragile across client builds. Out of scope for v1. Revisit only if a concrete gap demands it.

## 5. The bot subject: SK + Defiler

**Defiler is the chosen healer and it's the right call for automation.** Rationale (don't second-guess it):

- **Wards absorb damage before it lands.** The core loop is *maintenance* (keep ward up, refresh on fade/depletion), not *reaction*. This is latency-tolerant — exactly right for a sense→decide→press pipeline that's inherently 150–400 ms behind a human. A late ward refresh costs some absorb; a late reactive heal costs a death.
- Defiler debuffs/slow lower incoming damage — a second proactive mitigation layer the bot doesn't have to time.
- Evil-aligned, same-city as the SK. (Warden was ruled out: Qeynos-only.)
- Pre-pull is fully scriptable: ward tank → group ward → debuff on incoming → idle.

Future third box, if added: **Dirge** (evil; biggest force-multiplier for SK+Defiler — hate, melee buffs, battle rez).

### Decision loop (Defiler), priority order

1. **Cure pending** → cure by detrimental type (noxious/elemental/trauma/arcane).
2. **Tank ward absent/depleted** → recast. *This is the heartbeat.*
3. **Group ward down + AE incoming** → group ward.
4. **Tank below emergency threshold through wards** (rare path) → direct heal.
5. **OOC + prepull flag** → re-debuff next target, restore wards, drink/regen.

Items 1–3 are state-maintenance checks, not reactions. That's the whole reason Defiler works here.

> Ability names and keybinds are **owner-supplied config**, not hardcoded. The spec references abilities by role (ward, group-ward, cure-by-type, direct-heal, debuff, rez); the owner maps each to a hotbar key.

## 6. Core subsystems

### 6.1 State machine
States: `OOC`, `IN_COMBAT`, `WIPE_RECOVERY`, `REZ_LOOP`. Transitions from: log signal, manual override, optional pixel heuristic (own combat-stance indicator / target HP bar present). Manual override always wins and latches until a clean transition.

### 6.2 Chat-Safety Guard — **fail-closed, highest priority**
Stray keystrokes in the chat input bar = the bot's identity leaking to friends. Non-negotiable invariant: **never inject unless focus is provably on the game world.**

- **Pre-injection focus check:** sample the chat-input-bar region for the "input active" fingerprint (open field / cursor). If active → abort the injection, send `ESC`, re-verify, only then proceed.
- **Continuous watchdog:** every loop, sample chat-input state. If it's open and the bot didn't open it → `ESC` it and log an alarm.
- **Prefer bound social macros over typed commands.** Anything like follow/accept should fire as a hotkey mapped to an in-game macro, **not** by typing `/`-commands — keep text entry out of the pipeline entirely. This shrinks the leak surface to ~zero.
- **Keyspace hygiene:** bot keybinds must avoid any key EQ2 treats as a chat/reply trigger.
- **Fail-closed:** if focus state is unknown or ambiguous, **do not inject.**
- **Dashboard:** prominent CHAT-FOCUS alarm + counter of aborted injections.

### 6.3 Autofollow
Drive primarily off the **state-machine transition**, with OCR as the recovery net:

- `→ IN_COMBAT`: drop follow (stand and cast).
- `→ OOC`: re-engage follow on the tank.
- OCR catches "you have stopped following" (follow breaks on zone, knockback, LoS) → re-assert. Manual `force follow` override always available.

### 6.4 Rez loop
Trigger on partial or full wipe.

- **Bot is dead (full/partial wipe incl. healer):** the bot can't self-rez from dead; a surviving/returning player will rez it. **Auto-accept the incoming rez/revive prompt** (fingerprint + OCR confirm). Configurable policy: wait N seconds for an in-zone rez before self-reviving at stone (avoid revive sickness / avoid zone-out). 
- **Bot alive, others dead:** systematically rez the group. **Tank (SK) first, always**, then by priority list (generalizes to tank → other healer → support → DPS when group grows). Target dead member (group F-key/group-window targeting works on dead members), cast rez, wait for cast completion, confirm up, advance to next.
- Detect death via pixel (empty HP bar + death indicator on group window) corroborated by OCR/log death lines.

### 6.5 Launcher automation
One owner action (`virsh start`) → bot in group. **Fingerprint-gated steps**, never blind sleeps:

```
host:  virsh start <vm>  →  wait for agent socket heartbeat (agent = Windows startup task)
agent: launch EQ2 client →  wait for login-screen fingerprint
       inject creds → ENTER → wait for char-select fingerprint
       select char slot (fixed coords) → ENTER → wait for in-world fingerprint
host:  notify "bot ready"
owner: send group invite
agent: watch for invite-dialog fingerprint → fire bound accept macro
```

Each step waits for the expected screen's pixel signature before acting. Windows auto-login + agent autostart make `virsh start` the only manual trigger.

## 7. Dashboard / UI

- **Grafana-style**, web UI (recommended — best theming flexibility). Live websocket telemetry from the brain.
- Surfaces: per-member HP/ward bars, own power/cast state, current state-machine state, cure/heal/rez event stream, sensor health (pixel poll rate, OCR confidence, log freshness, agent latency), CHAT-FOCUS alarm + aborted-injection counter.
- Graphs where they earn it: HP-over-time, ward uptime %, heal/cure throughput, sense→act latency.
- **Manual controls:** force combat on/off, force follow, force rez-loop, pause/resume bot, emergency stop.
- **Multiple color themes.**

## 7.5 Naming / Opsec

Project name is **`iksar_buddy`** / **`ib`**. **No runtime-visible artifact may contain `eq2` or `bot`.** This is an opsec requirement equal in seriousness to the chat-safety guard — same reason (friends don't know yet), different surface.

Covers every externally observable name:
- Process / executable names and **window titles** (brain, agent, dashboard, launcher).
- systemd units / Windows scheduled tasks / startup entries.
- **libvirt VM name** (anywhere this spec said "the bot VM" → rename to `iksar_buddy` or similar innocuous).
- Log files/dirs, socket names, tmux/screen sessions, the repo directory, entrypoint script filenames.

Internal identifiers (class/var names) are exempt — this is about what shows up in a process list, Task Manager, or a window title bar.

**Windows process-title caveat (you're the SME, handle it right):** `setproctitle` does **not** change what Task Manager shows for a Python interpreter — it'll still read `python.exe`/`pythonw.exe`. To actually hide the agent's name on Windows, either package the entrypoint with PyInstaller as `ib.exe`, or run from a renamed interpreter copy. Set the **window title** explicitly regardless (any capture/console window). On the Linux brain side `setproctitle` is sufficient. Don't naively call `setproctitle` and assume the Windows side is covered — it isn't.

## 8. Configuration

Owner-owned, hot-reloadable where practical:
- Ability → keybind map (per role: ward, group-ward, cures by type, direct-heal, debuff, rez, follow-macro, accept-macro).
- Thresholds (emergency %, routine %, ward-refresh timing, rez-wait seconds).
- Screen geometry: resolution, UI scale, per-element sample regions/fingerprints (calibration profile, versioned alongside the VM snapshot).
- Priority lists (rez order, cure order).

## 9. Proposed repo layout (yours to finalize)

```
iksar_buddy/          # repo dir — note: no "eq2"/"bot" anywhere (see § 7.5)
├── CLAUDE.md
├── PROJECT.md
├── sessions/
│   ├── SESSION_TEMPLATE.md
│   └── session-YYYY-MM-DD.md
├── brain/        # host-side: state machine, decision loop, policy, transport server, web dashboard
├── agent/        # VM-side: capture, ocr, logtail, inject(+chat guard), launcher, transport client
├── shared/       # protocol/schema shared by both
└── config/       # ability maps, thresholds, calibration profiles, themes
```

## 10. Recommended stack (default — override with reason if you have a better instinct)

- **Brain:** Python (decision loop + socket server) with **FastAPI + websocket** serving the dashboard. Matches the owner's Python background and gives clean live telemetry.
- **Dashboard frontend:** lightweight SPA (your pick — Svelte/React/vanilla) over websocket, themeable.
- **Agent:** Python on Windows — `dxcam` for DXGI capture (fall back to `mss` if WARP gives it trouble), `pytesseract` for the filtered chat window, `pywin32`/`SendInput` for injection.
- **Transport:** length-prefixed JSON or msgpack over TCP. **Owner has existing socket code coming** — reuse its framing if sound, otherwise propose this. Mark as stub until that lands.

## 11. Phased roadmap (fail fast)

0. **Scaffold:** repo, modular skeleton, transport handshake, agent heartbeat, dashboard shell.
1. **Sense-only:** pixel capture of group HP + own power/cast → live on dashboard. No acting.
2. **Act manually:** keypress injection **+ chat-safety guard first**, driven by dashboard "cast X on slot N" buttons.
3. **Spine:** state machine (combat on/off + manual override) + Defiler ward-maintenance loop. End-to-end sense→decide→press working.
4. **Cures + follow:** cure-by-type handling; filtered-chat OCR for follow-drop + autofollow.
5. **Rez loop.**
6. **Launcher automation:** boot → in-group, fingerprint-gated.
7. **Polish:** themes, graphs, config UI, hardening.

Get 1→3 working as a thin vertical slice before widening. Don't gold-plate cures/rez/launcher before the spine breathes.

## 12. Open items

- Owner's existing socket code → finalizes the transport protocol.
- Final locked resolution + UI scale → every pixel coord and fingerprint derives from this; fix it before sensor code.
- Defiler ability → keybind map (owner).
