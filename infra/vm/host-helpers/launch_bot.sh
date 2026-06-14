#!/usr/bin/env bash
# Launch Bot (phase 1): start the VM, wait for boot + auto-login, run the in-game
# launcher (ibrun). The launcher now STOPS at char-select -- the HOST (brain) then
# OCR-picks the ACTIVE PROFILE's character, so Launch respects the selected profile
# instead of a hardcoded list slot. Streams progress to stdout (the brain relays
# each line to the dashboard event stream). Run on the host. Run: launch_bot.sh
#
# Exit codes (the brain branches on these):
#   0 = reached char-select   -> brain runs the host char pick next
#   2 = client already running -> already in-world, skip the pick
#   1 = failed before char-select
set -uo pipefail
VIRSH="sudo -n virsh -c qemu:///system"
DOM=iksar_buddy
G="$HOME/ib-build/gexec.py"

echo "starting VM"
if $VIRSH list --state-running --name | grep -qx "$DOM"; then
  echo "VM already running"
else
  $VIRSH start "$DOM" >/dev/null 2>&1 && echo "VM started"
fi

echo "waiting for guest agent"
for i in $(seq 1 75); do
  $VIRSH qemu-agent-command "$DOM" '{"execute":"guest-ping"}' >/dev/null 2>&1 && { echo "guest up"; break; }
  sleep 4
done
sleep 12   # let auto-login + desktop settle

# Idempotent: if the EQ2 client is ALREADY running, don't re-run the launcher
# (that would pop LaunchPad over a live session). Tell the brain to skip the pick.
if python3 "$G" "if (Get-Process EverQuest2 -ErrorAction SilentlyContinue) {'RUNNING'}" 2>/dev/null | grep -q RUNNING; then
  echo "client already running; skipping launcher"
  exit 2
fi

# Clear the launcher log FIRST so we wait for THIS run's char-select, not a stale
# "char-select ready" from a previous launch.
python3 "$G" 'Set-Content C:\ib\launcher.log ""' >/dev/null 2>&1
echo "launching client (ibrun)"
python3 "$G" 'Start-ScheduledTask -TaskName ibrun' >/dev/null 2>&1

echo "waiting for char-select (up to ~6 min)"
for i in $(seq 1 120); do
  line=$(python3 "$G" 'Get-Content C:\ib\launcher.log -Tail 1' 2>/dev/null | grep '@' | tail -1)
  if echo "$line" | grep -qi "char-select ready"; then echo "char-select ready"; exit 0; fi
  if echo "$line" | grep -qi "in-world"; then echo "in-world (legacy launcher)"; exit 2; fi
  sleep 3
done
echo "FAILED: never reached char-select"
exit 1
