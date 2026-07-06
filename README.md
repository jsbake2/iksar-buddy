# iksar_buddy (`ib`)

Client-side automation companion for a privately-hosted EQ2Emu server:
sense (pixels / OCR / logs / client memory) → decide → act (guarded keypress).
Three apps share one codebase. See `CLAUDE.md` (operating rules), `PROJECT.md`
(healer spec), `FORGE.md` (crafting), `HARVEST.md` (harvesting),
`ARCHITECTURE.md` (process/transport map). Opsec: nothing runtime-visible reads
`eq2`/`bot` — it's `ib` / `iksar_buddy` (`PROJECT.md` §7.5).

## The three apps

| app | what it does | run | dashboard |
|---|---|---|---|
| **brain** (healer) | plays the Defiler/Fury/Dirge: state machine + policy + keymap heartbeats | `python -m brain` | `:8080` (live: `:18080`) |
| **forge** (crafting) | multi-VM crafting fleet: writs, recipe lists, counter reflexes | `python -m forge` | `:18081` |
| **harvest** | memory-read node harvesting: grid tours, nav, rare/tell alerts | `python -m harvest` | `:18082` |

## Layout

```
brain/        healer host app: transport server (:8765), state machine, policy,
              per-spell heartbeats, telemetry, web dashboard
agent/        healer host↔guest glue: host_agent (guest-exec sensing/inject,
              chat guard), host_sensor, launcher, + legacy in-VM agent client
forge/        crafting host app: controller, per-VM workers, writ flow, sensors
harvest/      harvest host app: controller/web, nav graphs, RE tools
guest_agent/  files PUSHED INTO the Windows guests: ib_agent (reflex loop),
              heal/craft_reflex, harvest_agent (+ its six modules), offsets
shared/       cross-app plumbing: Guest (virsh/qemu-ga I/O core), LoginDriver,
              wire protocol, tunables loader, account interlock, push alerts
web_common/   the ONE copy of shared dashboard statics (spice-html5, toast.js,
              ui-core.js) — apps mount it as a StaticFiles fallthrough
config/       owner-owned YAML: keymaps, thresholds, calibration, profiles,
              accounts (gitignored secrets). See config/README.md for every knob
tests/        pytest suite (fast, no game required): pytest -q
bin/          host helpers (ibremote: RDP/SPICE into the VMs)
infra/        VM build artifacts, host bootstrap, AHK daemons, guest helpers
tools/        offline tooling (EQ2U recipe scraper + browser)
sessions/     per-commit session logs — the project's memory, read them
```

## Run (dev)

```
pip install -e .              # host deps (fastapi/uvicorn/pyyaml/setproctitle)
python -m brain               # healer: transport :8765, dashboard :8080
python -m forge               # crafting fleet dashboard :18081
python -m harvest             # harvest dashboard :18082
pytest -q                     # test suite
```

Guest-side files under `guest_agent/` are not installed — each app's deploy
pushes them into the VM over qemu-guest-agent (see `GUEST_RUNBOOK.md` for what
lands where in `C:\ib\` and how to rebuild a guest after a revert).

## Remote into the VM(s)

```
bin/ibremote          # RDP; one VM -> connect, many -> menu
bin/ibremote -c       # SPICE console (works before RDP is set up)
bin/ibremote -l       # list running VMs + IPs
```

Every dashboard also embeds a web SPICE console (spice-html5, LAN-only).

## Live deployment

Host `10.0.0.16` runs brain (+ healer VM), forge fleet, harvest. Code lives in
`~/ib-app`, runtime config/data in `~/ib-data` (`IB_FORGE_DIR` et al.). That box
runs other workloads — never restart services on it without the owner's go-ahead
(`CLAUDE.md` § Permissions).

> Runtime invariant: keystrokes must NEVER land in the chat bar. Injection is
> fail-closed — no proof of game-world focus, no keypress (`PROJECT.md` §6.2).
> Scope: private EQ2Emu server only (`CLAUDE.md` § Legitimacy).
