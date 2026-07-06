# ib — architecture (one page)

Who runs where, what talks to what over which transport, and where the two
safety gates sit. Per-app behavior lives in `PROJECT.md` / `FORGE.md` /
`HARVEST.md`; this is the process/transport map those docs assume.

```
CachyOS host (10.0.0.16)                      Windows guest VM(s)
────────────────────────                      ─────────────────────────────
brain        :8765 transport, :8080/:18080 web   iksar_buddy (healer VM)
 └ host_agent  (healer sense+inject glue)     crafter VMs (forge fleet)
forge        :18081 web                       harvest VM
harvest      :18082 web
                                              in-guest processes (all pythonw/AHK,
                                              interactive session, `ib*` task names):
                                                ibagent  → ib_agent.py (+ heal/craft_reflex)
                                                ibkeyd   → keyd.ahk key daemon
                                                ibhud    → hud_overlay.py (harvest HUD)
                                                harvest_agent.py (spawned per command)
```

## Transports (every host⇄guest pair)

| pair | transport | notes |
|---|---|---|
| host_agent → brain | length-prefixed JSON over TCP :8765 (`shared/protocol.py`) | STATE_EVENT ~2 Hz + HEARTBEAT; receives COMMAND/CONFIG |
| brain/forge/harvest → guest | `shared/guest.py` `Guest(dom)`: virsh + qemu-guest-agent | guest-exec (PowerShell), guest-file read/write, screenshot, VM lifecycle. Synchronous — callers run it in an executor |
| ib_agent → forge/harvest | **outbound-only HTTP** from the guest | poll `GET /api/agent/{bot}/command`, push `POST /api/agent/{bot}/telemetry` (forge :18081); harvest sense_push → `POST :18082/api/ingest`. No inbound port on any guest |
| host_agent key inject | guest-file write of `C:\ib\keycmd.txt` → `ibkeyd` AHK daemon (~<0.1 s) | one-shot `ibkey` scheduled task is the fallback (~0.5 s); daemon self-heals via heartbeat file |
| combat log | in-guest tail thread (ib_agent) mirrors freshest `eq2log_*.txt` → `C:\ib\combat_tail.txt`; host_agent polls the mirror | falls back to a PS `Get-Content -Tail` when the mirror is stale/absent |
| harvest nav/loops | guest-file JSON handshake: host writes `C:\ib\nav_target.json`, agent writes `nav_status.json`; `C:\ib\STOP` aborts | harvest_agent reads client memory (pymem) for pos/nodes |
| dashboards → browser | FastAPI + WebSocket `/ws` snapshots; statics via `web_common` StaticFiles fallthrough | SPICE console via spice-html5 (`/spice/console.html`), LAN-only |

## Sensing paths (healer)

1. **In-guest reflex (primary, ~12 Hz):** ib_agent + heal_reflex grab the group
   window with mss, single-grab-per-tick pixel reads, decide from the pushed
   ruleset (`heal.json`, built by `guest_agent/heal_ruleset.py` FROM
   `config/thresholds.yaml`), press keys locally. Reaction in tens of ms.
2. **Host poll (fallback + orchestration, ~2 Hz):** host_agent drives
   `agent/host_sensor.py` over virsh screenshots, feeds the brain's state
   machine, fires keymap heartbeats through the guarded inject path.

## The two safety gates

- **Chat-safety guard (THE invariant):** no injection without proof that focus
  is on the game world. `agent/chat_guard.py` (host path) and the reflex loops'
  chat-clear pixel check (guest path) both fail closed; aborted injections are
  counted on the dashboard. Blind typing is only ever allowed into
  atomically-focused, OCR-verified UI fields (login, recipe search).
- **Account interlock (`shared/account_lock.py`):** one live client per game
  account across all apps — forge/harvest/brain must take the lock before
  launching or switching characters, so two VMs can't log the same account in.

## Config flow

`config/*.yaml` (owner-owned; see `config/README.md` for the knob table) →
`shared/tunables.py` one-shot load at startup for every app; the brain
additionally hot-reloads and serves `/api/tunables`. Guests get config pushed,
never read the repo: `heal.json` ruleset, `agent.json` (host URL/bot id),
`offsets.py` (memory offsets — ONE canonical copy in `guest_agent/`).
