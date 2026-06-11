# STRETCH_GOALS.md — Crafting / Writ Bot

> **PRIORITY: SECONDARY.** The healer bot (`PROJECT.md`) is the primary deliverable. Do **not** pull focus to crafting until the healer is fully functional, or at minimum stable enough to run while we test/tune it. Crafting work happens in the gaps and during healer fine-tuning, not before the healer spine breathes. If you're ever choosing between healer progress and crafting progress, healer wins.

---

## Goals, in order

1. **Stretch:** clean up and finish the existing crafting code (daily writs + bulk crafting). Owner reports it was ~90% — worked, but had pain points and bugs. This is a **triage-and-finish** job on handed-off code, not a greenfield build.
2. **Double-stretch:** add **movement** so the bot can travel to the writ giver, accept a new writ, complete it, and turn it in. Owner doesn't care how hard this is or how long it takes — it's the last thing on the list.

Both go here, separate from the healer spec, on purpose.

## What's being handed off

A crafting bot set of code (separate from the healer code), covering **daily writs** and **bulk crafting**. Treat it as a starting point to fold into this project's modular structure — not as gospel. Expect bugs and rough edges.

## Architecture fit — reuse the spine

The crafting bot is **not a separate application.** It's a second **brain mode / policy** running on the same agent spine the healer uses:

- Same **transport**, **pixel capture**, **OCR**, **keypress injection**, and — critically — the same **chat-safety guard** (`PROJECT.md` § 6.2). Crafting keystrokes leaking into chat is the same dead giveaway; the fail-closed invariant applies unchanged.
- Crafting gets its **own state machine and decision loop**, swapped in when the bot is in "craft mode" instead of "heal mode." Don't fork the project; extend it.
- When you triage the handoff code, the goal is to **lift its useful logic into this structure**, not bolt the old scripts on the side.

## EQ2 crafting mechanics (context — owner is SME, confirm specifics)

The core of crafting automation is the **reaction-event loop**, and it's pixel-driven, which fits our primary sensor cleanly:

- Crafting a recipe runs an interactive process: a progress bar fills, and **events fire** that you counter with **reaction arts** (the crafting arts on the hotbar). Hitting the right counter art when an event flashes protects durability/progress and drives toward a **pristine** result.
- **Event detection = the crafting heartbeat**, analogous to ward maintenance in the healer. Detect the event icon/flash via pixel → fire the matching counter art. This is almost certainly where the handed-off code's pain points live; make it the priority to harden.
- Before each craft: verify **fuel + raw materials** present; bail gracefully (don't burn a half-craft) if short.
- **Bulk crafting** = loop the single-craft routine over a queue of N.
- **Writs** = accept work order from the tradeskill writ giver, make the required item set, turn in. Requirements can be read from the journal/writ text via **OCR**.

> Exact recipe names, reaction-art keybinds, writ-giver identity/location, and quality targets are **owner-supplied config**, same SME split as the healer. Spec references them by role (counter-art-for-event-type, fuel-check, writ-requirement-parse).

## Movement (double-stretch)

Hard part, but **the problem is constrained**: tradeskill areas are compact, indoor, flat, with the writ giver and crafting stations close together. This is short-hop navigation, not open-world pathfinding — far more tractable.

Approach (no server access — pixel/OCR/inject only, consistent with everything else):

- **Waypoint-arrow steering.** Set an in-game waypoint to the target, read the compass/waypoint arrow direction via pixel, hold autorun, and correct heading until the arrow centers and distance closes.
- **`/loc` OCR for position.** EQ2 prints current x/y/z to chat on `/loc`; OCR it from the filtered chat window to get a real coordinate fix, compute heading to a known target loc, steer. (Keep this inside the chat-safety discipline — reading the filtered window, not typing into the live bar; fire `/loc` via a bound macro, not typed text.)
- Known target locs (writ giver, each station, turn-in spot) are **owner-supplied config** per area.
- Scope to **one crafting area at a time.** No zoning, no long-distance travel in v1 of movement.

## Phasing (only after healer is stable)

- **S0 — Triage:** get the handoff code building inside this project's structure. Document in a session log what works, what's broken, what the pain points actually are. No new features yet.
- **S1 — Solid single craft:** reliable craft-to-pristine on one recipe. Fix the event-detection/counter-art reliability first — that's the likely root of the reported bugs.
- **S2 — Writ loop:** accept writ → OCR requirements → craft the set → turn in.
- **S3 — Bulk:** queue N, loop, with material/fuel guarding and graceful stop-on-empty.
- **DS — Movement:** waypoint-steer between writ giver, station, and turn-in within one compact area.

Same fail-fast rule: get S1 working as a thin slice before chasing writs and bulk. Movement is genuinely last and explicitly optional.

## Reminder

Nothing here justifies slowing the healer. If healer tuning reveals work, that work preempts everything in this file.
