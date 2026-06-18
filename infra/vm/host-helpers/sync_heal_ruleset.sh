#!/usr/bin/env bash
# Regenerate the in-guest heal ruleset from the BRAIN CONFIG (single source of
# truth: calibration.yaml geometry + the active profile's ability_map keys) and
# install it on the healer guest as C:\ib\agent\heal.json. This is the fix for the
# keys drifting -- the in-guest healer never carries a hand-maintained keymap again.
#
# Does NOT start the heal loop; it only installs the file. Re-run after any keybind
# or calibration change. Usage: sync_heal_ruleset.sh
set -euo pipefail
PY=/home/jbaker/ib-app/.venv/bin/python
GEN=/home/jbaker/ib-app/guest_agent/heal_ruleset.py
OUT=/home/jbaker/ib-build/heal.json
CFG=${IB_CONFIG_DIR:-/home/jbaker/ib-data/config}

"$PY" "$GEN" --config-dir "$CFG" > "$OUT"
echo "built $OUT from $CFG ($(wc -c < "$OUT") bytes)"

B64=$(base64 -w0 "$OUT")
"$PY" /home/jbaker/ib-build/gexec.py \
  "\$b=[Convert]::FromBase64String('$B64'); [IO.File]::WriteAllBytes('C:\\ib\\agent\\heal.json',\$b); 'WROTE '+(Get-Item 'C:\\ib\\agent\\heal.json').Length+' bytes'"
