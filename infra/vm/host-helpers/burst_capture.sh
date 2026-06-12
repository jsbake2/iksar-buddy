#!/usr/bin/env bash
# Burst full-res screenshots of the VM to disk so a one-shot UI (e.g. the mender
# armor-repair dialog) is captured no matter when it appears in the window.
# Args: <label> <seconds> <interval>. Run on the host.
set -uo pipefail
LABEL="${1:-cap}"; DUR="${2:-60}"; IVAL="${3:-1.2}"
OUT="$HOME/ib-data/captures/$LABEL"; mkdir -p "$OUT"
VIRSH="sudo -n virsh -c qemu:///system"
END=$(( $(date +%s) + DUR )); i=0
while [ "$(date +%s)" -lt "$END" ]; do
  i=$((i+1)); n=$(printf "%03d" "$i"); ts=$(date +%H%M%S)
  ppm="/tmp/burst_$$.ppm"
  if $VIRSH screenshot iksar_buddy "$ppm" >/dev/null 2>&1; then
    magick "$ppm" "$OUT/${LABEL}_${ts}_${n}.png" 2>/dev/null && echo "saved ${LABEL}_${ts}_${n}.png"
  fi
  sleep "$IVAL"
done
rm -f "/tmp/burst_$$.ppm"
echo "DONE: $(ls "$OUT" | wc -l) frames in $OUT"
