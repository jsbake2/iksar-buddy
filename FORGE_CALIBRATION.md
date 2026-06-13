# Forge calibration checklist

Everything Forge needs captured from the live game to actually craft. All coords
are **guest pixels at 1920×1080**. Captured via the dashboard **⚙ set up tradeskills**
window (per VM) and saved into `craft.yaml` (calibration) — the keys (1–6 + camp)
live separately in the keymap. Both crafter VMs are identical clones, so calibrate
once and it serves both.

Legend: **pixel** = one point + its color · **region** = a box {x,y,w,h} · **click** =
a point to click · **template** = a cropped PNG for image-matching.

## A. Crafting loop (the core)
1. **3 reaction button positions** — `region ×3` (`reaction.button_regions`). The FIXED
   spots where the three reaction buttons render. We do NOT save a per-class template
   library — at **every craft start** Forge grabs these three buttons FRESH into memory
   and uses them as the match references. So any class works, even a random quest craft
   for an undefined class. (Counter #1/#2/#3; keys 1–3 durability / 4–6 progress reuse
   the same three buttons.)
2. **Active-reaction watch area** — `region` (`reaction.region`). Where the active
   reaction shows up; matched against the 3 live references → counter # → press the key
   for (counter#, mode).
3. **Active-reaction cue** — how a button signals it's the one to press right now
   (glow / highlight / pulse / a marker) — so the watch-area match is reliable.
   ← needs your eyes when we calibrate.
4. **Durability vs Progress mode** — `pixel`. One spot that reads one color in
   durability mode and another in progress mode (decides which key-set answers a counter).
5. **Power / mana gate** — `pixel`. Reads the "enough power" color; below it Forge pauses
   (and uses the power ability if set) before continuing.
6. **Begin button** — `pixel` + `click`. Appears between crafts to start the next item.
7. **Retry button** — `pixel` + `click`. Appears to repeat the current recipe.
8. **Craft-window focus point** — `click`. A safe spot inside the craft window clicked
   before pressing art keys so they land in the craft window (not chat / the hotbar).

## B. Writs / recipe selection
9.  **Search: clear** — `click`. The X that clears the recipe search box.
10. **Search: box** — `click`. Focuses the recipe search field (then Forge types the name).
11. **Search: first result** — `click`. Selects the first filtered recipe.
12. **Journal OCR region** — `region`. The writ's "I need to make …" required-items text
    (OCR'd → recipe list).

## C. Login / safety (mostly done)
13. **Char-select list region** — `region` (OCR the names). ✅ calibrated.
14. **Char-select row click-x** — `value` (=100; click the row-LEFT/portrait, not the name). ✅
15. **Char-select Play button** — `click` (≈1715,890). ✅
16. **Chat input region** — `region`. The chat bar — used by the chat-safety guard (never
    inject while it's active) and to type `/camp`.

## What's NOT captured here (lives elsewhere)
- The 6 art keys + camp command → **keymap** (`⌨ keymap` window).
- Which character/class is on each VM → **crafter roster** (`⌨ characters` window).
- VM names / SPICE ports / accounts → `stations.yaml` + the interlock.
