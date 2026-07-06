"""EQ2 client-memory offsets — the ONE copy (REFACTOR P0.4).

Every in-guest reader (harvest_agent, sense_push, sense_daemon, memory_read /
harvest_read, spawns_live) imports these instead of carrying its own constants;
before this module existed, sense_daemon shipped with STALE pre-recalibration
offsets and read garbage position. A client patch now means: recalibrate
(memory_read.py --recalibrate X Y Z, see sessions/2026-06-23), edit THIS file,
redeploy — not a five-file hunt.

Deploy note: this file is pushed to C:\\ib\\agent\\offsets.py by every deploy
path (harvest deploy_agent / deploy_reader / start_sensor). In-guest scripts
import it as a sibling (`import offsets`); repo-side code imports
`guest_agent.offsets`. Both spellings hit this file.

History:
  2026-06-23  POS/HDG shifted +0x10 after a client update (0x1822b68 ->
              0x1822b78). ZONE_PTR unchanged. 0-360 heading mirror at 0x1822af8.
"""

PROC = "EverQuest2.exe"

# --- player (module-static, [EverQuest2.exe + off]) --------------------------
POS_OFF = 0x1822b78      # float32 X,Y,Z
HDG_OFF = 0x1822b84      # POS + 0xC = heading in degrees
ZONE_PTR = 0x1826998     # -> zone-name string ("The Thundering Steppes")

# --- nearby-harvestable array (module-static) ---------------------------------
# The game's LIVE gather-skill list: pointers to harvest-node objects (vtable in
# the 0x149x-0x14ex family); world position at node+0x60. Real nodes only.
NODE_LO = 0x177bf00
NODE_HI = 0x177c100
NODE_VT_MIN = 0x1490000  # node-vtable window (module-relative) for pointer vetting
NODE_VT_MAX = 0x14f0000
NODE_POS = 0x60          # node object -> world position (float32 X,Y,Z)

# --- heap scans (whole-heap vtable sweeps; slow path) --------------------------
ACTOR_VT = 0x1782848     # mobs/NPCs/players; world pos at obj+0x20
ACTOR_POS = 0x20
# harvest-node vtable family (per-type), (vtable_off, pos_off) — via despawn diffs
NODE_CLASSES = [(0x14eb850, 0x60), (0x14a3238, 0x40), (0x14a32d8, 0x40),
                (0x1493c58, 0x40), (0x149b2f8, 0x40)]
