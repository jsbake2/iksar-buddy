#!/usr/bin/env bash
# Native SPICE console for the iksar_buddy3 VM (crafter #2). SSH tunnel local 5952
# -> server 5920, then remote-viewer. Sibling of ib-console (healer 5900) and
# ib-console2 (crafter #1, 5910). Passive client, touches nothing on the server.
set -e
pgrep -f "5952:127.0.0.1:5920" >/dev/null 2>&1 || ssh -fN -L 5952:127.0.0.1:5920 new-server
sleep 1
exec remote-viewer "spice://127.0.0.1:5952" --title "ib craft VM2"
