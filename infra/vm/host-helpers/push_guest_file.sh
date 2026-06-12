#!/usr/bin/env bash
# Push a local file into the Windows guest via the qemu guest agent (base64 ->
# WriteAllBytes), avoiding shared folders. Usage: push_guest_file.sh <src> <windst>
#   push_guest_file.sh ~/ib-build/key_ev.ahk 'C:\ib\key_ev.ahk'
set -euo pipefail
SRC="$1"; DST="$2"
B64=$(base64 -w0 "$SRC")
python3 "$HOME/ib-build/gexec.py" "\$b=[Convert]::FromBase64String('$B64'); [IO.File]::WriteAllBytes('$DST',\$b); 'WROTE ' + (Get-Item '$DST').Length + ' bytes'"
