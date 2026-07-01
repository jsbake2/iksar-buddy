# Session Log — 2026-06-30 (Dirge/Joar: account3 + account-aware char select)

## Ask
Owner adding a 3rd toon: **Joar (Dirge)** on a NEW EQ2 account (`matte123`). Wanted it
"straightened out" while he manually powerlevels. Dirge in-game behavior is owner-SME,
TBD — for now just: it logs in, and follow/jump/basic manual controls exist.

## Done
- **Roster + profile.** `Joar: { account: account3, adventure: dirge }` in
  `config/characters.yaml`; `config/profiles/joar.yaml` = a SUPPORT (non-healer)
  profile — `maintenance_role: none`, all heal/ward/cure roles blank (policy skips
  unmapped roles), manual `follow`/`stop_follow`/`jump`/buffs/`camp` wired. The
  decision loop queues NOTHING for Joar even on a dying tank (tested).
- **Account model moved to the BRAIN** (was mistakenly on forge and reverted there):
  `brain/charswitch.py` owns roster (`account_of`) + creds. `creds_for_character`
  reads brain-owned `~/ib-data/accounts.yaml` (keyed by account label), falling back
  to the legacy forge `accounts.yaml` (account-label or VM-dom key) so the existing
  account1/account2 healer login is unchanged. Paths read at call time (env-overridable).
- **Account-aware character select (the real integration):** `healer_change(target,
  current)` — SAME account -> `/camp <name>`; DIFFERENT account (Joar/account3 while an
  account2 healer is in world) -> `/camp desktop` to log OUT, wait for client exit, then
  `boot_and_login` to log back IN with the target account's creds. Wired into
  `/api/profile/{name}/swap` (was same-account `/camp` only); response now flags
  `cross_account`. Swap button confirm text updated to describe the relog.
- **Creds** stashed in gitignored `~/ib-data/accounts.yaml` (workstation). Perms 600.
- **Tests:** `tests/test_charswitch.py` — account_of, cred resolution (temp env), and the
  Dirge-queues-nothing invariant. 62 passed / 1 pre-existing forge failure (unrelated).
- **VM:** started `iksar_buddy` (GPU/4070 box) on 10.0.0.16 for manual powerleveling.
  Server stack otherwise all off; nothing disrupted.

## Not done / notes
- **`healer_change` is UNTESTED live** — the cross-account `/camp desktop` -> relog path
  is coded + unit-reasoned but has never run against the client. Validate before trusting.
- **Live server needs the creds too:** add the `account3` block to
  `10.0.0.16:~/ib-data/accounts.yaml` before dashboard auto-login/relog of Joar will work
  there. Not touched (owner using the box).
- **Dirge has no dedicated VM** — a 3rd account can't share a running healer's login;
  the relog path repurposes the healer VM. A separate box/profile pairing (tank follow
  target, group slots) is owner-SME work, deferred.
- Dashboard healing grid still renders a meaningless "none" maintenance column for Joar —
  left as-is; the Dirge won't use the heal grid and behavior is TBD.
- Pre-existing failing test `test_forge.py::test_scribe_prefix_strip_and_suffix` predates
  this work (stale vs the 06-26 search-tier change).

## Next
1. Live-test `healer_change` cross-account relog (pick Joar from the dashboard).
2. Put `account3` creds in the server's `~/ib-data/accounts.yaml`.
3. Owner defines the Dirge kit (buff keys, follow target, group layout).
