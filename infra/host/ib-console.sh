#!/usr/bin/env bash
# Native SPICE console for the iksar_buddy VM. Opens an SSH tunnel to the host's
# SPICE (127.0.0.1:5900) on local 5950, then launches remote-viewer. Touches
# NOTHING on the host/bot -- passive SPICE client over SSH. Run: ib-console
set -e
pgrep -f "5950:127.0.0.1:5900" >/dev/null 2>&1 || ssh -fN -L 5950:127.0.0.1:5900 new-server
sleep 1
exec remote-viewer "spice://127.0.0.1:5950" --title "ib console"
