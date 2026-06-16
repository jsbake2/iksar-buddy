# iksar_buddy — Open Tasks

_Snapshot 2026-06-16. Grouped by area. Each item notes status + what's needed from the owner to flesh it out._

---

## ✅ Done recently (context)
- In-guest reflex agent (crafter): fast counter loop, 12 Hz.
- Counter mechanic fixed: counters always press the icon's art (**1/2/3**); 4/5/6 are pump/filler only. (Was wrongly mode-switching counters → armor choked.)
- Scribe → list capture from the writ pane (📖 Mark for scribe).
- List crafting + mana recovery between crafts.
- Healer launch bug fixed (deploy-drift `select_only` import → 500).
- Healer in-guest sensing **ported + validated at 12 Hz** (identical to host read).
- Auto-shutdown: per-bot "⏻ Power off VM when list done" (camp → power off). **Tested, works.**
- Crafter VMs cleaned up (snapshot-overlay mistake reverted; disks intact).

---

## 🔨 Crafter

### 1. Writs (timed quest crafting) — *path exists, never tested*
Flow: OCR the quest-journal panel → `{recipe: count}` → craft the list against the clock.
The journal-OCR region is a **placeholder calibration only**; writs were gated on the crafter hitting **L20**.

**Open:**
- Calibrate/verify journal OCR against a *real* writ window.
- Confirm the flow: parked at the station with just the craft list, or does the bot need to **walk to/from the writ-giver**? (FORGE.md mentions movement — is that in scope, or do you position manually?)
- Timed completion behavior (writs barrel forward, ignore mana — already coded; verify it's right for timed).

**Need from owner:** a screenshot of a real writ in the journal (when L20+), and whether movement is in scope.

### 2. Long recipe-name search reliability — *verify, maybe a non-issue*
While (wrongly) chasing the armor failures, saw "not in filtered list" on some 20+ char names. Root cause was counters (now fixed), but EQ2's search field caps at ~18 chars, so very long names get truncated. Want to confirm long names still select reliably now.

**Need from owner:** nothing yet — I'll watch for it on the next long-name list.

### 3. Solid-square reaction icons — *future / optional*
Replace EQ2's reaction-art icons with bold solid-color squares on the crafter client → counter detection becomes a trivial color match (vs template matching). Nice-to-have bulletproofing, not urgent.

---

## 🩹 Healer

### 4. In-guest acting test → make it the permanent path — *sensing done; acting staged*
Sensing is ported and validated (12 Hz). The acting agent (`ibhealact`) + instant revert are staged.

**Open:**
- Flip to acting at a real fight; confirm heals/wards land fast under damage.
- Confirm the **chat-safety invariant holds** in-guest (no stray keys) and keybinds land.
- If good: make the in-guest agent the **default** (replace the 1 Hz host agent) + autostart.

**Need from owner:** a giant fight (or anything that deals damage) to test against.

### 5. Smarter healing — *your ask from 2026-06-12; policy work in `brain/policy.py`*
Concern: the tank dies because the healer reacts too late (latency + thresholds + GCD). Ideas you'd listed:
- **Predictive** — react to HP *velocity* (big drop since last tick → pre-emptive emergency heal), not just absolute thresholds.
- **Earlier tiers** — bump emergency ~0.40 / critical ~0.65 (currently lower).
- **Tank fast-lane** — sense/react to the tank with no GCD when critical.
- **Emergency bypasses GCD entirely** — fire the instant tank < threshold, even mid-cast.
- **Confirm the ward heartbeat** is actually firing (proactive mitigation).
- (Faster *sensing* is largely handled by the 12 Hz in-guest lift.)

**Need from owner:** which of these matter most + preferred threshold values (or "tune live"). Best tuned *during* the combat test.

---

## Suggested sequencing
1. **#4 + #5 together at the giants** — test in-guest acting and tune the smarter-healing thresholds live in one session.
2. **#1 writs** — when the crafter is L20 and you can show a real writ.
3. **#2 / #3** — opportunistic / future.

_Edit freely — fill in details under any item and hand it back._
