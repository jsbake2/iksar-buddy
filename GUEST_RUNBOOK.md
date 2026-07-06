# Guest runbook — what lives in `C:\ib\` and how it gets there

The first thing you need when a VM revert/reboot "breaks the bot". Nothing in
the guest is hand-maintained: **every file below is pushed from the host**, and
most of it self-deploys. If a guest looks wrong, redeploy — never edit in-VM.

## Delivery mechanisms (all via qemu-guest-agent, `shared/guest.py`)

| mechanism | what it ships | when it runs |
|---|---|---|
| **self-deploy** (host_agent) | `keyd.ahk` + `ibkeyd` task | automatically, when the daemon heartbeat (`C:\ib\keydaemon.hb`) stops advancing |
| **deploy_agent** (harvest `__main__`) | everything under `C:\ib\agent\` for harvest | on harvest launch (C:\ib is assumed reverted on boot) |
| `infra/vm/host-helpers/sync_heal_ruleset.sh` | `C:\ib\agent\heal.json` (built from config/thresholds + calibration + active profile) | manually, after any keybind/calibration change |
| `infra/vm/host-helpers/push_guest_file.sh` | any single file | manual utility (base64 → WriteAllBytes) |
| forge deploy (`forge/guest.py` → shared Guest) | `ib_agent.py`, `craft_reflex.py`, `agent.json` | on forge worker start |

## Inventory

### Scheduled tasks (all InteractiveToken — session 0 can't BitBlt/inject)
| task | runs | purpose |
|---|---|---|
| `ibkeyd` | `C:\ib\keyd.ahk` (AHK daemon) | fast key inject: watches `keycmd.txt`, ~<0.1 s per press; writes `keydaemon.hb` heartbeat |
| `ibkey` | one-shot AHK | fallback inject path (~0.5 s) when the daemon is down |
| `ibagent` | `C:\ib\py\python.exe` → `agent\ib_agent.py` (pythonw) | in-guest reflex agent: heal/craft reflex loops, combat-log tail, outbound HTTP telemetry |
| `ibhud` | `agent\hud_overlay.py` | harvest on-screen status HUD (reads `hud.json`) |

### Files
| path | what | written by |
|---|---|---|
| `C:\ib\py\` | embedded Python (pythonw.exe — no console window) | VM image / one-time bootstrap |
| `C:\ib\keyd.ahk`, `ibkeyd.xml` | key daemon script + task XML | host_agent self-deploy (canonical source: `infra/vm/ahk/key_daemon.ahk`) |
| `C:\ib\keycmd.txt` / `keydaemon.hb` | inject command file / daemon heartbeat | host_agent / daemon |
| `C:\ib\keys.txt`, `click.txt`, `key_ev.ahk`, `launcher.ahk` | one-shot key/click/login helpers | pushed per use |
| `C:\ib\agent\*.py` | `ib_agent`, `heal_reflex`, `craft_reflex`, `harvest_agent` + its six modules (`agentio`, `win_input`, `eq2mem`, `nav`, `harvest_loops`, `diag`), `hud_overlay`, `offsets`, `nav_graph`, `sense_push`, `memory_read` | forge/harvest deploys (repo `guest_agent/` is the source) |
| `C:\ib\agent\agent.json` | ib_agent config: `{host, bot, poll_hz}` | forge deploy (see `guest_agent/agent.example.json`) |
| `C:\ib\agent\heal.json` | healer reflex ruleset (pixel locs + keys) | `sync_heal_ruleset.sh` — NEVER hand-edit; regenerate from config |
| `C:\ib\sense.json`, `hud.json` | sense config / HUD state | host push / agent |
| `C:\ib\graph.json`, `route.json`, `nav_target.json`, `nav_status.json`, `STOP` | harvest nav graph, route, host↔agent handshake, abort flag | harvest host ↔ harvest_agent |
| `C:\ib\combat_tail.txt` | last 250 raw combat-log lines (atomic mirror) | ib_agent tail thread (P2.3) |
| `C:\ib\crash.log`, `gdbg.log`, `ahk.log`, `launcher.log` | faulthandler / debug / AHK / login logs | the respective guest processes |
| `Documents\EverQuest II\logs\<server>\eq2log_*.txt` | the game's chat/combat log — `/log` defaults OFF each relog; every bot must verify it's on at startup | EQ2 client |

## What survives a revert

Assume **nothing under `C:\ib\` survives** except what's baked into the image
(`C:\ib\py\`). The system is built for that: host_agent re-deploys the key
daemon on heartbeat loss, harvest re-runs `deploy_agent()` on every launch,
forge re-pushes ib_agent on worker start. The only manual step after a revert
is `sync_heal_ruleset.sh` if the healer reflex is in use (and re-checking that
EQ2 `/log` is enabled — the bots verify but can't enable the client setting
from outside a logged-in session).

## Diagnosing "the bot won't start" after a deploy

1. `C:\ib\crash.log` — faulthandler traceback from harvest_agent (e.g.
   ImportError = a module didn't push).
2. `C:\ib\gdbg.log` — harvest agent debug trail.
3. `ibkeyd` dead? host_agent logs the self-heal; check `keydaemon.hb` mtime.
4. Telemetry silent? ib_agent pushes outbound HTTP — check `agent.json` host
   URL and that the dashboard port is reachable FROM the guest.
