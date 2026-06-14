#!/usr/bin/env bash
# Native SPICE console for an ib VM. SSH-tunnel to the server's SPICE, then
# remote-viewer. Passive client — touches nothing on the server/VM.
#   ib-console [remote_spice_port]   5900 healer(default) / 5910 craft1 / 5920 craft2
SERVER="${IB_CONSOLE_SERVER:-new-server}"
remote="${1:-5900}"
local_port=$(( 5950 + (remote - 5900) / 10 ))     # 5900->5950, 5910->5951, 5920->5952
log="/tmp/ib-console.log"
ts() { date '+%F %T'; }
echo "$(ts) req remote=$remote local=$local_port server=$SERVER" >>"$log"
# Reuse an existing tunnel BY PORT (robust); else create one. ExitOnForwardFailure
# so a half-open ssh never lingers; BatchMode so it fails fast (no hang) when the
# key isn't set up. Do NOT 'set -e' before remote-viewer — a benign reuse must not
# abort the launch (that was the old "button does nothing" bug).
if ! ss -ltn 2>/dev/null | grep -q "127.0.0.1:$local_port "; then
  if ! ssh -fN -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
        -L "$local_port:127.0.0.1:$remote" "$SERVER" 2>>"$log"; then
    echo "$(ts) TUNNEL FAILED to $SERVER (ssh key/LAN? VM off?)" >>"$log"
    command -v notify-send >/dev/null 2>&1 && \
      notify-send "ib console" "SSH tunnel to $SERVER failed — check key/LAN, or the VM is off."
    exit 1
  fi
  sleep 1
fi
exec remote-viewer "spice://127.0.0.1:$local_port" --title "ib console :$remote" >>"$log" 2>&1
