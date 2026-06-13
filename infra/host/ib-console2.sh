#!/usr/bin/env bash
# Native SPICE console for the iksar_buddy2 VM (the crafting clone). Opens an SSH
# tunnel to the server's SPICE (127.0.0.1:5910) on local 5951, then launches
# remote-viewer. Touches NOTHING on the server/VM -- passive SPICE client over
# SSH. Sibling of ib-console (which is the healer on 5900). Run: ib-console2
set -e
pgrep -f "5951:127.0.0.1:5910" >/dev/null 2>&1 || ssh -fN -L 5951:127.0.0.1:5910 new-server
sleep 1
exec remote-viewer "spice://127.0.0.1:5951" --title "ib console 2 (craft)"
