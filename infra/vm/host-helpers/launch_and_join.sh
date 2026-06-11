#!/usr/bin/env bash
# Host-side launch orchestrator. Runs ON the CachyOS VM host (10.0.0.16).
#
# Ties the whole hands-off flow together with NO manual step and NO eyeballed
# coords for the invite:
#   1. kick the guest launcher (ibrun) -> desktop..LaunchPad..PLAY..in-world
#   2. wait until launcher.log reports "in-world"
#   3. poll invite_accept.py (self-locating OCR, same logic as quest_accept.py)
#      until it clicks the located "Accept" word box, or the window expires.
#
# Vision is host-side on purpose: the guest has no tesseract. The guest only
# ever does the click (ibgclick task). Run: bash launch_and_join.sh
set -euo pipefail
VIRSH="sudo -n virsh -c qemu:///system"
DOM="iksar_buddy"
HERE="$(cd "$(dirname "$0")" && pwd)"
GEXEC="$HOME/ib-build/gexec.py"
INVITE="$HERE/invite_accept.py"

guest() { python3 "$GEXEC" "$1" 2>/dev/null; }
logtail() { guest 'Get-Content C:\ib\launcher.log -Tail 1' | sed -n 's/^OUT:$//;/@/p'; }

echo "[launch] starting guest launcher (ibrun)"
guest 'Start-ScheduledTask -TaskName ibrun' >/dev/null

echo "[launch] waiting for in-world (up to ~6 min)"
for _ in $(seq 1 120); do
  if logtail | grep -q "in-world"; then echo "[launch] in-world"; break; fi
  sleep 3
done

echo "[launch] polling self-locating invite accept (up to ~2 min)"
for _ in $(seq 1 40); do
  out="$(python3 "$INVITE")"
  echo "$out" | sed 's/^/  /'
  if echo "$out" | grep -q "CLICKED Accept"; then
    echo "[launch] group invite accepted via OCR"
    exit 0
  fi
  sleep 3
done
echo "[launch] no invite appeared within the window (left solo, no harm)"
