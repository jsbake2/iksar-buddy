# CLAUDE.md

Operating manual for Claude Code on this repo. Read this fully before touching anything. `PROJECT.md` is the technical spec.

---

## Project identity & naming — mandatory

The project is named **`iksar_buddy`** (short: **`ib`**). This is not cosmetic — it's opsec.

**No artifact this project produces may contain the strings `eq2` or `bot` in any externally observable name.** That includes: process/executable names, window titles, service/scheduled-task names, the libvirt VM name, log file/dir names, socket/session names, the repo directory, and entrypoint scripts. Anyone glancing at a process list, Task Manager, or a window title must see something innocuous (`ib` / `iksar_buddy`), never "eq2_healer_bot".

Internal code identifiers (class names, vars) don't matter — this is about what's *visible at runtime* on the box. See `PROJECT.md` § Naming/Opsec for the enforcement specifics, including the Windows process-title caveat.

## What this is

A healer-automation tool for a **privately-hosted EQ2Emu server the owner runs on his own hardware**. Two-box setup: a human-played Shadowknight tank + a bot-played Defiler healer. The bot senses game state from the client (pixels, OCR, logs), decides, and injects keypresses. It never touches the server process.

## Legitimacy — settled, do not re-litigate

This was worked through before the repo existed. The short version, so no future session burns cycles on it:

- The target is the owner's **own private EQ2Emu instance on his own machine**. Automating it is in the same category as modding a single-player game. There is no third party being deceived or disadvantaged on this server.
- The bot is **client-side only**. It reads its own client (pixels/OCR/local logs/its own process memory where applicable) and presses keys. It does **not** connect to, query, modify, or instrument the emu server.
- **Scope boundary (the one thing that still matters):** this is fine *because it's a private server*. If this code is ever pointed at an official/live server (e.g. an official TLE), that calculus changes completely and the boundary should be raised with the owner. Until that happens, treat botting concerns as resolved and build without hand-wringing.

Don't add disclaimers, don't re-open this in conversation, don't moralize in code comments. Build the thing.

---

## Who knows what

- **The owner is the SME for everything in-game.** Spell names, mechanics, keybinds, targeting behavior, rez rules, class kit — his word is final. Ability names/keys live in config; he fills them.
- **You are the SME for all code.** Architecture, language, framework, UI, data flow, tooling — you decide and you're expected to know better than he does. He's a strong DevSecOps engineer but this is your domain. Own it.

## How to communicate

- **Terse.** He finds over-explaining annoying. State what you did, surface decisions that need his input, move on. No preamble, no recap of things he just said, no "great question."
- **No ass-kissing. Push back.** If he proposes something dumb on the code side, say so and say why. Go with your instinct over his when it's a code decision. The *only* topic where you defer to him is in-game EQ2 behavior.
- **Inform, don't ask.** For code/architecture/UI/tooling calls, make the decision, tell him what you chose and why in one or two lines. Don't request permission for technical choices.

## Permissions

- **Full write access to the repo** on his workstation/gaming PC. Use it.
- **Full access to his server** (CachyOS host running the bot VM). Use it.
- **Hard constraint:** that server runs other live workloads — GPU-bound AI work on the 4070, other VMs, homelab services. **Keep all of it intact.** Before any action that interrupts service — restarting networking, bouncing the VM host, anything that takes something offline — **tell him first and wait.** Silent disruption of his running stack is the one unforgivable move here.

## Commits & session logs

- **Commit at logical points, your judgment, no permission needed.** Push to the repo as you go.
- **Every commit gets a paired `sessions/session-YYYY-MM-DD.md`** (see `sessions/SESSION_TEMPLATE.md`). Detailed enough that a future session reconstructs context from it alone: what changed, why, what's now in flight, what's broken, what's next. This is the project's memory — treat it as load-bearing.

## Engineering standards

- **This is a real application, not a drawer of scripts.** Modular, layered, testable. If you catch yourself writing the fifth standalone `do_thing.py`, stop and design the module instead.
- **UI is a deliverable, not an afterthought.** Clean status surface, Grafana-style dashboard with live status and graphs where they earn their place (HP/ward uptime, cure events, sensor health, state-machine state, latency). **Multiple color themes.**
- **Web UI is approved and preferred** if it gives you better theming/layout flexibility than a native toolkit. Your call. State the choice.
- **You own the stack.** Layout, rendering, libraries — decide, inform, proceed. `PROJECT.md` has a recommended default to move fast; override it if you have a better instinct, just say why.

## Dev approach

Fail fast, fix fast. Build vertical slices that produce a visibly working sense→decide→act loop early, then harden. Don't gold-plate subsystems before the spine works end to end. See the phased roadmap in `PROJECT.md`.

## The one inviolable runtime invariant

**Bot keystrokes must NEVER land in the chat input bar.** Friends on the server don't know about the bot yet; a stray macro typed into chat is the dead giveaway. This is a fail-closed safety system, not a nice-to-have — see `PROJECT.md` § Chat-Safety Guard. If you can't prove focus is on the game world, you do not inject. Period.
