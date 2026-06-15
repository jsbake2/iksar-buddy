# Forge calibration — findings from your marked-up screenshots

Method worked well: each marked image = a clean original with a bold opaque fill
over the target. I diffed marked-vs-original to recover the exact region, and the
fill color + filename told me which element. Coords below are **guest pixels @
1920×1080**. Overlay PNGs in this folder show MY boxes on the clean frames so you can
sanity-check that I put them in the right place.

## Coordinate table (what I extracted)

| # | Element | Region / point | Source frame | Overlay |
|---|---|---|---|---|
| 2 | **Search input** | box x279–419 y169–192 → click **(349,180)** | EQ2_000002 | A |
| 8 | **Filtered recipe list** | x220–533 y259–675; first result row top ≈ **(377,272)** | EQ2_000002 | A |
| 7 | **Mouse safe / focus click** | strip x757–918 y134–154 → click **(837,144)** (craft-window title bar) | EQ2_000004 | B |
| 1 | **Begin** (start) | RIGHT button, click **(784,707)** | EQ2_000005 | C |
| 6 | **Create** (save as var) | LEFT button, click **(492,707)** | EQ2_000005 | C |
| 3 | **Counter icon** (active reaction) | box x579–617 y622–660 → center **(598,641)** | mid-craft | D |
| 4 | **Craft arts 1/2/3** (refs) | y≈710, centers **1=(640) 2=(683) 3=(726)**; 4/5/6 = 769/812/855 | mid-craft | D |
| - | **Durability/mode bar** | top bar; sample along y≈262. green `(34,205,46)`=present, maroon `(53,16,25)`=depleted | EQ2_000008 | D |
| 9 | **Quest journal OCR** | x1562–1909 y239–606 | EQ2_000006 | E |

## My understanding of the reaction mechanic (confirm I've got it)

- During a craft the bottom bar becomes **[book] [1][2][3][4][5][6] [stop]**.
  **1/2/3 = durability arts, 4/5/6 = progress arts.** There are really **3 reaction
  types**, each with a durability twin (1/2/3) and a progress twin (4/5/6).
- At craft start I **capture icons 1/2/3 into memory** as the 3 match references
  (works for any class/recipe, no saved template library).
- A reaction fires → its icon shows in the **counter slot (598,641)** next to its name
  (e.g. "Orange Hot Metal"). I template-match that icon against refs 1/2/3 to get the
  **type** (1, 2 or 3).
- Which key I actually press depends on **durability level**: durability **low → press
  the durability twin (1/2/3)**, durability **high → press the progress twin (4/5/6)**
  of that same type. (Pairing assumed **1↔4, 2↔5, 3↔6** — see Q4.)
- **Idle (no counter showing):** cycle the active set with .3 s pauses — `1,2,3` if
  durability is low, `4,5,6` if high — and **break instantly** when a counter appears.
- **Power gate:** reuse the healer's self power bar (`config/calibration.yaml`
  `power_bar: x0:19 x1:128 y:46 fill:blue`, read by `agent/host_sensor.py`). Pause when
  **< 50%**, **except during a writ** (writs barrel forward to completion).
- **Begin vs Create:** rely on **Begin** (first craft); on subsequent crafts the same
  spot becomes **Retry/Redo**. Keep **Create** stashed in a var as a fallback.

## Confirmed (2026-06-14)

1. ✅ **Begin/Create flip** — owner confirmed: **Create LEFT (492,707), Begin RIGHT
   (784,707)**. Filename labels were transposed.
2. ✅ **Durability threshold = 80%.** Durability **< 80% → durability arts (1/2/3)**;
   **≥ 80% → progress arts (4/5/6)**. Top green bar = durability (confirmed).
   - Bar geometry (from EQ2_000008): track **x581→882, y≈262**, ~301 px wide, gold
     quality ticks at x641 & x821. 88% fill edge at x846. The **80% point lands ≈x822 —
     right on a gold tick**, so the exact sample pixel gets nudged a few px off the tick
     and finalized at live calibration (green `(34,205,46)`=≥thresh, maroon `(53,16,25)`=below).

3. ✅ **Counter→key pairing is positional: 1↔4, 2↔5, 3↔6.** Counter matches type at
   position N → press **N** in durability mode (dur <80%), **N+3** in progress mode (dur ≥80%).

## Verified myself

4. **Power bar on crafter HUD** ✅ — same spot as the healer. Crafter HUD top-left:
   HP (green `(2,232,0)`) at **y36–39**, power (blue `(117,117,234)`) at **y44–47**,
   track **x19–128**. Matches healer `power_bar: {x0:19, x1:128, y:46, fill:blue}` — reuse
   `agent/host_sensor.py` fill logic directly. Pause <50% except during writs.

## Everything needed for the single-craft slice is now captured. No open questions.

## Noted for later (not blocking)
- Precision recipe search + chopping long names to fit the input field (dino regex pain).
- Level-up recipe-log capture → regex → savable crafting list (after you hit crafter L20).
- Game-log capture for authoritative "you created…" completion + dedup.
- Test recipe: **leather bags** (you'll give the exact name).
