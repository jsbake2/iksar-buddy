#!/usr/bin/env bash
# Ensure EQ2 chat logging is ON for Jenskin -- the combat-detection signal. EQ2's
# /log is a TOGGLE: turning it ON writes a "...is now *ON*" line; turning it OFF
# writes nothing. So toggle once and look for a FRESH *ON* confirmation line:
#   - new *ON* line appeared  -> it was off, now ON (done)
#   - no new *ON* line         -> we just turned it off (it was on) -> toggle again
# Keying off the confirmation line (not file size) makes this correct even if
# combat is writing to the log. Net result: logging always ends ON. Run on host.
set -uo pipefail
G="$HOME/ib-build/gexec.py"
GT="$HOME/ib-build/gtype.py"
LOG="C:\\Users\\Public\\Daybreak Game Company\\Installed Games\\EverQuest II\\logs\\Wuoshi\\eq2log_Jenskin.txt"

last_on() {   # the most recent "...is now *ON*" line in the log (empty if none)
  python3 "$G" "if (Test-Path '$LOG') { (Select-String -Path '$LOG' -Pattern 'is now \*ON\*' | Select-Object -Last 1).Line }" \
    2>/dev/null | grep 'is now' || true
}

OLD=$(last_on)
python3 "$GT" '/log' enter >/dev/null 2>&1
sleep 1.5
NEW=$(last_on)
if [ -n "$NEW" ] && [ "$NEW" != "$OLD" ]; then
  echo "logging ON (was off)"
else
  python3 "$GT" '/log' enter >/dev/null 2>&1   # was on -> we turned it off -> re-enable
  sleep 1.5
  echo "logging ON (re-enabled)"
fi
