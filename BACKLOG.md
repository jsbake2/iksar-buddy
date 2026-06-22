# Backlog — deferred items

Things intentionally parked, not lost. See `sessions/` for context.

## Harvest bot

### Monster / NPC list (deferred 2026-06-22)
Removed from the dashboard for now. Harvest nodes are detected via the game's static
nearby-harvestable array (`module+0x177bf00`), but there is **no parallel static array for
monsters** — they live in a **heap spawn-manager**. Cracking it:
- Use the same **target-diff** technique that found the node array (`re_tools/target_diff.py`
  + `target_examine.py`), but **target a MOB** instead of firing gather: snapshot module-RW
  pointers → target nearest NPC → snapshot → diff → the static ptr that flips to the mob
  object (and/or the nearby-NPC array). This needs a keypress, so do it **with the owner
  watching**, not headless.
- Once the NPC object/list is known: enumerate nearby monsters with level + position +
  aggro flag → wire to a "nearest monster (aggressive? · level · distance)" panel and the
  opsec "players nearby" proximity hold (~30 m).
- Node NAMES are ID-linked (no string in the node object) → node type comes from the harvest
  log verb, not memory; a memory name path would need the ID→string table.

### Other parked
- Compass N/E/S/W zero-direction calibration (heading value is solid; letter mapping provisional).
- Smooth-nav polish (no 90° snaps), route POI editor, `?`-collectible nodes.
