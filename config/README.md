# config/ — the owner-tunable surface

Every knob the owner should ever need to touch lives HERE, not in code. The
`.py` constants that mirror these values are **fallback defaults only** — edit
the YAML, not the code. `IB_CONFIG_DIR` relocates the whole directory (the live
deployment points it at `~/ib-data`-adjacent config where applicable).

**Hot reload:** only the brain hot-reloads (mtime watch on thresholds /
calibration / profiles). Everything else (host_agent, host_sensor, harvest,
charswitch) reads at startup — restart the process after an edit. The in-guest
agent gets thresholds+calibration+names pushed over the wire in the brain's
CONFIG message on connect and on every profile switch.

## Files

| File | What it owns | Consumers |
|---|---|---|
| `thresholds.yaml` | decision thresholds, pacing, detection tunables | brain (hot), host_agent (startup), in-guest agent (CONFIG push), heal_ruleset |
| `calibration.yaml` | screen geometry, sensor regions, bar colors, `healer_dom`, log path | host_sensor, host_agent, in-guest agent (CONFIG push), heal_ruleset, dashboard, charswitch |
| `harvest.yaml` | harvest controller knobs (dom, login form coords, move keys, ingest) | harvest/__main__.py (startup), sense_push (argv on deploy) |
| `ability_map.yaml` / `profiles/*.yaml` + `active_profile` | keybinds, names, class kit per character | brain Config (hot; pushed to agent) |
| `characters.yaml` | character → account roster | brain/charswitch |
| `secrets.yaml` (gitignored) | login credentials | agent/launcher |
| `forge/` | crafting configs — NOTE: the LIVE forge reads `~/ib-data/forge` (`IB_FORGE_DIR`), not this dir | forge |

## thresholds.yaml

| Key | Default | Consumer | Effect |
|---|---|---|---|
| `hp_standard` | 0.90 | brain policy, heal_ruleset | below ⇒ standard heal (mana-gated); also the reflex's std pixel x |
| `hp_critical` | 0.75 | brain policy, heal_ruleset | below ⇒ critical heal, ignores mana |
| `hp_emergency` | 0.50 | brain policy | below ⇒ emergency heal, cancels cast |
| `mana_floor` | 0.30 | brain policy, heal_ruleset | own power below ⇒ skip standard heals (reflex fallback was 0.24 before P1.4) |
| `group_heal_count` / `group_critical_count` | 2 / 3 | brain policy | hurt/critical member count ⇒ group heal |
| `gcd_s` | 0.6 | brain server | min gap between ANY two commands |
| `cooldown_default_s` | 1.5 | brain server | per-(action,target) repeat gap when not in `cooldowns_s` |
| `cooldowns_s.<role>` | see file | brain server | per-action land+sensor-lag estimate |
| `prepull_debounce_s` | 3.0 | brain server | min gap between pre_pull fires |
| `rez_window_s` | 240 | host_agent, guest agent | post-revive: lit detriments = rez sickness, no cure |
| `combat_hp_drop` | 0.02 | host_agent, guest agent | HP drop/cycle that reads as "took a hit" |
| `combat_decay_s` | 5.0 | host_agent, guest agent | combat → OOC after this long without a hit |
| `chat_hysteresis_s` | 3.0 | host_agent, guest agent | chat-input activity latches "busy" this long |
| `combat_log_poll_s` | 1.0 | host_agent | fallback combat-log poll period |
| `decision_hz` | 12 | agent | sense loop rate |
| `ward_heartbeat_s` | 8.4 | brain heartbeats | maintained-ward recast period (in combat) |
| `assist_heartbeat_s` | 4 | brain heartbeats | pet+assist re-send period (in combat) |
| `debuff_cycle_s` / `debuff_power_floor` | 10 / 0.50 | brain heartbeats | debuff period; only above the power floor |
| `mana_heal_floor` / `mana_heal_recast_s` | 0.0 / 6 | brain heartbeats | Dirge mana feed (0 = off); per-target recast gap |
| `pbuff_N_interval_s` / `pbuff_N_target` | 0 / 0 | brain heartbeats | periodic buff N period (0 = off) + group-slot target (0/1 = self) |
| `rez_*` / `cure_priority` | see file | brain policy | rez order + cure resolve order |

## calibration.yaml

| Key | Consumer | Effect |
|---|---|---|
| `healer_dom` | host_agent, host_sensor, dashboard, charswitch | THE healer VM libvirt domain |
| `eq2_log_template` | host_agent | combat-log path; `{char}` = active profile's names[0] |
| `sensor.*` | host_sensor (startup), guest agent (CONFIG push) | load-bearing bar/detriment/chat geometry + pixel thresholds — edit after any UI move |
| `bar_colors.*` | heal_ruleset | HP/power fill colors the in-guest reflex checks |
| `group_bars` / `power_bar` | heal_ruleset | reflex pixel locations (derived geometry doc for `sensor.*`) |
| `fingerprints`, `char_slot`, `chat_*`, `cast_bar`, `ward_icon` | launcher/guards | screen fingerprints (several still placeholder) |

## harvest.yaml

| Key | Default | Effect |
|---|---|---|
| `dom` / `spice_port` | iksar_buddy / 5900 | VM the harvester drives + viewer port |
| `active_char` | Furyflatulence | character until a login sets it |
| `user_click` / `username_ocr` | see file | login-form focus click + OCR verify region (1920×1080) |
| `eq2_log_template` | see file | harvest event stream log path |
| `move_keys` | WASD/QE/Space | movement key map |
| `ingest_url` / `ingest_hz` | :18082/api/ingest / 8.0 | where/how fast the in-guest sensor pushes (takes effect on sensor redeploy) |
