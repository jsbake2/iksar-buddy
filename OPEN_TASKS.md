# iksar_buddy — Open Tasks

_Updated 2026-06-16 (owner notes folded in + icon feasibility crawled). Grouped by area._

---

## ✅ Done recently (context)
- In-guest reflex agent (crafter): fast counter loop, 12 Hz.
- Counter mechanic fixed: counters always press the icon's art (**1/2/3**); 4/5/6 are pump/filler only.
- Scribe → list capture from the writ pane (📖 Mark for scribe).
- List crafting + mana recovery between crafts.
- Healer launch bug fixed; healer in-guest sensing **ported + validated at 12 Hz**.
- Auto-shutdown per-bot ("⏻ Power off VM when list done"). **Tested, works.**
- Crafter VMs cleaned up (snapshot-overlay mistake reverted).

---

## 🔨 Crafter

### 1. Writs (timed quest crafting) — *path exists, never tested*
Flow: OCR the quest-journal panel → `{recipe: count}` → craft against the clock.
Journal-OCR region is a **placeholder calibration**; gated on the crafter hitting **L15**.

- **Movement: OUT of scope for now** (far-off goal). Owner gets the writs + parks the crafter at the station; we automate doing the craft list only.
- **Timed = barrel forward, ignore mana** — confirmed correct (already coded).
- **Open:** calibrate/verify journal OCR against a *real* writ window.
- **Need from owner:** a screenshot of a real writ in the journal once L15+.

### 2. Recipe search: handle many similar variants — *future enhancement*
Single long names are handled (owner fills the per-recipe Search field to disambiguate; top result wins).
**Future problem:** recipes with many near-identical variants, e.g.
`Turbo (Expert) / Turbo II (Expert) / … / Turbo (Journeyman) / Turbo II (Journeyman) / …`
The craft window's result area only surfaces ~2 rows for matching, which isn't enough to pick the right one.
**Plan:** widen the OCR result region / handle scrolling so several rows are visible to match against (and/or a tighter exact-match on the full name + tier). Revisit when it actually bites.

### 3. Solid-square reaction icons — *FEASIBLE (crawled the install); low priority since detection works now*
Findings from the EQ2 install (iksar_buddy2):
- **Active UI = DarqUI_v3** (custom skin) — and the EQ2 patcher only restores the **Default** UI, so edits to a **custom** skin (DarqUI, or a new skin) survive patching. ✅
- Ability/spell icons are **loose, standard `.dds`** in `UI/Default/images/icons/` (`icon_as*.dds` etc.), **256×256 DXT3 atlases** of 42×42 cells. Editable with normal tools (texconv / GIMP+DDS / Paint.NET / ImageMagick). ✅
- Icon→sheet+cell mapping lives in `eq2ui_IconStyles.xml` (`IconResource` + `IconRect`).
- DarqUI ships an editable **tradeskill window** (`eq2ui_tradeskills.xml`) + a `darqui_ts_reactions` component.

**Two viable paths:**
- **A — UI XML (cleaner, scoped):** edit the tradeskill window so the reaction slot/buttons render solid distinct colors. Doesn't touch other icons.
- **B — icon DDS:** paint the 3 reaction-art icon cells solid colors. Simple format, but changes that icon everywhere the ability shows (hotbar etc.).

**Verdict:** doable as a small project (main work = identifying the 3 reaction arts' icon cells, editing, testing in the custom skin). **Not urgent** — counter detection is reliable now after the 1/2/3 fix. Worth it later for bulletproofing.

---

## 🩹 Healer

### 4. In-guest acting test → make it the permanent path — *sensing done; acting staged*
- Flip `ibhealact` to acting at a real fight; confirm heals/wards land fast + chat-safety holds + keybinds land.
- If good: make the in-guest agent the **default** (replace the 1 Hz host agent) + autostart.
- **Need from owner:** a giant fight to test against.

### 5. Smarter healing — *policy work in `brain/policy.py`*
Owner-chosen priority: **earlier tiers → tank fast-lane → confirm heartbeats**; predictive + emergency-GCD-bypass = future.

Claude's take (tweak to the order):
- The **12 Hz in-guest switch (#4) is itself the biggest latency fix** — the tank gets sensed ~12× more often, which is most of the "reacts too late" problem. Do #4 first.
- **Earlier tiers** (emergency ~0.40 / critical ~0.65): yes, easy + high impact, tune live during the fight.
- **Emergency bypasses GCD**: I'd *promote this out of "future"* — it's a small change with big survivability payoff (emergency heal fires instantly mid-cast). Pairs naturally with earlier tiers.
- **Tank fast-lane**: with 12 Hz sensing already in, this may be **redundant** — let's see if #4 + earlier tiers + GCD-bypass already fix it before building a separate fast lane.
- **Confirm ward heartbeat** is firing: quick check during the test.
- **Predictive (HP velocity)**: agreed — future (needs tuning).
- **Need from owner:** OK to fold "emergency bypasses GCD" into the first pass? And threshold values, or tune live (I lean tune-live during the giants).

---

## Suggested sequencing
1. **Healer at the giants:** flip to in-guest acting (#4) → tune earlier tiers + emergency-GCD-bypass live (#5) → confirm wards → decide if tank fast-lane is still needed.
2. **Writs (#1):** when the crafter is L15 and you can show a real writ.
3. **#2 / #3:** opportunistic / future.
