# iksar_buddy (`ib`)

Healer-automation companion for a privately-hosted EQ2Emu server. Sense (pixels /
OCR / logs) → decide (state machine + Defiler policy) → act (guarded keypress).
See `CLAUDE.md` (operating rules) and `PROJECT.md` (spec). Opsec: nothing
runtime-visible reads `eq2`/`bot` — it's `ib` / `iksar_buddy` (`PROJECT.md` §7.5).

## Layout
```
brain/      host-side: transport server, state machine, Defiler policy, dashboard
agent/      guest-side: capture, OCR, inject(+chat-safety guard), launcher, client
shared/     wire protocol (length-prefixed JSON) shared by both
config/      owner-owned YAML: ability map, thresholds, calibration
bin/ibremote  connect to guest VM(s) over RDP/SPICE (multi-VM aware)
infra/vm/    VM build artifacts (unattend, libvirt domain, host bootstrap helpers)
```

## Run (dev)
```
pip install -e .            # brain deps (fastapi/uvicorn/pyyaml/setproctitle)
python -m brain             # transport :8765, dashboard http://localhost:8080
# on the guest:
pip install -e .[agent]     # mss/pytesseract/pillow
python -m agent --brain 192.168.122.1
```
Dashboard: live HP/ward bars, state, sensor health, **CHAT-FOCUS alarm**,
aborted-injection counter, manual controls (force combat/ooc/follow/rez,
pause/resume/estop), multiple themes.

## Remote into the VM(s)
```
bin/ibremote          # RDP; one VM -> connect, many -> menu (future crafting fleet)
bin/ibremote -c       # SPICE console (works before RDP is set up)
bin/ibremote -l       # list running VMs + IPs
```

## Status (2026-06-11)
Guest VM up (Win10, RDP, internet). EQ2 client installed; LaunchPad login
automated. Brain/agent scaffold + dashboard in place (vertical slice). Sensors
and keybinds are stubs/placeholders until the client resolution is locked and the
Defiler is leveled. The bot does **not** run automated gameplay on live servers —
only the owner's private Woushi emu (`CLAUDE.md` legitimacy boundary).

> Runtime invariant: keystrokes must NEVER land in the chat bar. The injector is
> fail-closed — no proof of game-world focus, no keypress (`PROJECT.md` §6.2).
