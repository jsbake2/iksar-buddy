# Session Log — 2026-06-30 (Dirge: fullscreen-VM login fix + buff UI; Forge OCR triage)

## 1. Login flow FIXED — the healer VM needs the guest-agent typer, not AHK
Root cause of the earlier "password blank at the login form": the healer VM `iksar_buddy`
is the SAME fullscreen GPU box harvest uses, and **AHK Send does not land on its fullscreen
login form** (it typed username+world but not password). Harvest already solved this — it
passes `form_typer=self._agent_type_login` to `LoginDriver` (the in-guest ibharv agent types
via keybd_event). The healer's `charswitch` was using the default AHK typer.

Fix (`brain/charswitch.py`): `_agent_login()` builds the LoginDriver reusing harvest's
`Harvest._agent_type_login` as the form_typer — literally the same code path as harvest/forge.
ALL keyboard-to-client actions now go through the guest agent for this VM:
- `healer_login` → boot_and_login with the agent typer.
- `healer_switch` (same account) → agent types `/camp <char>` (not AHK camp_to).
- `healer_change` cross-account → agent types `/camp desktop`, wait client exit, relogin.
**Validated live: Joar logged into world first try** via the agent typer.

## 2. Deployed live to 10.0.0.16 + verified
- Server config dir is `~/ib-data/config` (IB_CONFIG_DIR), NOT ~/ib-app/config. There was
  NO characters.yaml there — created it (full roster incl. Croalst=acct2/Paraphon=acct1,
  inferred from the profile pairs + crafters.yaml). Joar profile + `~/ib-data/accounts.yaml`
  (account3=matte123, gitignored, mode 600) deployed. Backed up the 3 brain files I overwrote
  (`*.pre-joar.bak`).
- Live-validated cred resolution: `creds_for_character('Joar') -> matte123`; existing toons
  still resolve to `robskin2004` via the forge dom fallback (unchanged).
- Restarted ib-brain twice (new charswitch + new app.py/UI). active_profile set to `joar`.
- Cleanly camped Joar + powered the VM OFF for the owner's from-scratch UI launch test.

## 3. Dirge UI — buffs replace heals (owner spec)
When a profile has `maintenance_role: none` the dashboard is now KIND=`dirge` and renders a
support layout instead of the heal grid (healer profiles unchanged):
- Per-member grid = INDIVIDUAL buff buttons (target that member's F-key + cast).
- New sections: Tank buffs (group pos 2 = F2), Self buffs (F1), Debuffs (current target),
  Group buffs (no target), Damage combos (combat). Healer sections (Heals/Buffs/Combat) +
  the ward-recast tuner hide for a Dirge.
- Roles are PREFIX-grouped in the profile (`ibuff_/tbuff_/sbuff_/dbuff_/gbuff_/combo_`),
  owner fills keys via the ⌨ keymap page; unmapped buttons render greyed/italic with a hint.
- Backend: `_profile_state` exposes `kind`+`actions`; `act_member`/`act_group` gained a safe
  generic fallthrough (`_profile_role`) so any profile-defined role works without a whitelist.
Files: brain/web/app.py, brain/web/static/{app.js,index.html,themes.css}, config/profiles/joar.yaml.

## 4. Forge OCR triage (INVESTIGATED ONLY — forge NOT restarted, owner using it)
Subagent traced all three reported bugs (read-only). NOT applied — proposed fixes only:
- **II↔III / IV dropped:** `recipes.py:70-76` `_fix_roman` maps an I-glyph run by COUNT
  (`_ROMAN={2:II,3:III,4:IV}`) — the `4:IV` entry is bogus (IV has a V) and OCR stroke-count
  is unreliable, so III→IV / IV→II flips happen. `_TIER_RE` (`recipes.py:72`) lookahead
  rejects a trailing `)`/`.` so "Silent Threat IV)" drops the IV. Preprocessing
  (`sensors.py` `-blur 0x0.5` before a hard threshold) erases thin `I` strokes.
- **Single-word "Recipe II" → "Recipe":** NOT `_decompose` (correct). The picker
  (`sensors.py match_recipe_row`) matches the tier as a SUBSTRING/soft coverage token, and
  `"ii"` ⊂ `"iii"`; for a 1-word base the tier is only 50% of coverage and a base-only row
  clears `sole_result_floor` (0.34), so the II is discarded.
- **Proposed:** cap `_ROMAN` at 3 + drop `4:IV`; widen `_TIER_RE` lookahead to `[\s().]`;
  make the tier a MANDATORY exact-token gate in the picker (reject rows whose tier != target);
  add a blur-free high-scale tier OCR pass. Full report + file:line in the session thread.

## State / Next
- Owner to: fill Dirge keybinds (keymap), refresh dashboard to see the buff UI, run the
  from-scratch Launch of Joar from the UI (VM is OFF).
- `healer_change` cross-account relog path: coded + same-code as harvest, but the /camp-desktop
  transition itself is still UNTESTED live.
- Forge OCR fixes: await owner go-ahead to apply (forge in use; needs a restart to deploy).
