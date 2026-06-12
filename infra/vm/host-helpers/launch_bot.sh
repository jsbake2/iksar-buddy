#!/usr/bin/env bash
# Launch Bot: start the VM, wait for boot + auto-login, run the in-game launcher
# (ibrun: LaunchPad -> PLAY -> char-select -> in-world), then poll the OCR invite
# accept until the group is joined. Streams progress to stdout (the brain relays
# each line to the dashboard event stream). Run on the host. Run: launch_bot.sh
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
# (that would pop LaunchPad over a live session). Just go to the invite watch.
if python3 "$G" "if (Get-Process EverQuest2 -ErrorAction SilentlyContinue) {'RUNNING'}" 2>/dev/null | grep -q RUNNING; then
  echo "client already running; skipping launcher"
else
  echo "launching client (ibrun)"
  python3 "$G" 'Start-ScheduledTask -TaskName ibrun' >/dev/null 2>&1
fi

echo "waiting for in-world (up to ~6 min)"
for i in $(seq 1 120); do
  line=$(python3 "$G" 'Get-Content C:\ib\launcher.log -Tail 1' 2>/dev/null | grep '@' | tail -1)
  if echo "$line" | grep -qi "in-world"; then echo "in-world"; break; fi
  sleep 3
done
# No invite watching here -- the dashboard "accept invite" button runs it on
# demand (instant), so we don't poll for minutes.
echo "in-world; use the 'accept invite' button when the invite arrives"
