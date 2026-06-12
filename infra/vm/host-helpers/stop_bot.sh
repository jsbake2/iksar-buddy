#!/usr/bin/env bash
# Stop Bot: press the configured CAMP key for a clean EQ2 logout, wait out the
# camp countdown, then gracefully shut down the VM (force off if it hangs).
# Streams progress to stdout (brain relays to the dashboard). Run on the host.
#   stop_bot.sh "<camp_key>" [camp_wait_seconds]
# Pass "" or "none" as the camp key to skip camping and just shut down.
set -uo pipefail
VIRSH="sudo -n virsh -c qemu:///system"
DOM=iksar_buddy
G="$HOME/ib-build/gexec.py"
CAMP_KEY="${1:-}"
CAMP_WAIT="${2:-25}"

if [ -z "$CAMP_KEY" ] || [ "$CAMP_KEY" = "none" ]; then
  echo "no camp key configured; shutting down directly"
else
  echo "camping (key: $CAMP_KEY)"
  python3 "$G" "Set-Content C:\\ib\\keys.txt '$CAMP_KEY' -NoNewline; Start-ScheduledTask -TaskName ibkey" >/dev/null 2>&1
  echo "waiting ${CAMP_WAIT}s for the camp countdown"
  sleep "$CAMP_WAIT"
fi

echo "shutting down VM"
$VIRSH shutdown "$DOM" >/dev/null 2>&1
for i in $(seq 1 20); do
  st=$($VIRSH domstate "$DOM" 2>/dev/null)
  if [ "$st" = "shut off" ]; then echo "VM off"; exit 0; fi
  sleep 3
done
echo "graceful shutdown timed out; forcing off"
$VIRSH destroy "$DOM" >/dev/null 2>&1 && echo "VM off (forced)"
