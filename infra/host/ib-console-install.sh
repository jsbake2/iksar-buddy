#!/usr/bin/env bash
# ib console — client installer (run on each home-LAN computer that should open the
# dashboard's ⧉ console buttons). The ⧉ button navigates to an `ibconsole://` URL;
# this registers a local handler that SSH-tunnels to the server's SPICE and opens
# remote-viewer. Idempotent — safe to re-run. CachyOS/COSMIC (and any Arch + xdg).
#
#   ./ib-console-install.sh                 # uses defaults below
#   IB_SERVER_HOST=10.0.0.16 ./ib-console-install.sh
#
# Prereqs it sets up for you: virt-viewer (remote-viewer) + xdg-utils + openssh,
# a passwordless SSH alias to the server, the helper scripts, and the scheme
# handler. SPICE is a PASSIVE view (the bot keeps running while you watch/click).
set -euo pipefail

SERVER_HOST="${IB_SERVER_HOST:-10.0.0.16}"   # server LAN IP (the CachyOS host)
SERVER_USER="${IB_SERVER_USER:-jbaker}"
ALIAS="new-server"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
mkdir -p "$BIN" "$APPS"

say() { printf '\033[1;36m[ib-console]\033[0m %s\n' "$*"; }

# ---- 1) dependencies ------------------------------------------------------
say "1/5 dependencies (virt-viewer, xdg-utils, openssh)…"
if command -v pacman >/dev/null 2>&1; then
  need=()
  for p in virt-viewer xdg-utils openssh iproute2; do
    pacman -Qq "$p" >/dev/null 2>&1 || need+=("$p")
  done
  if [ "${#need[@]}" -gt 0 ]; then sudo pacman -S --needed --noconfirm "${need[@]}"; fi
else
  say "  non-Arch system — install 'virt-viewer' and 'xdg-utils' with your package manager."
fi

# ---- 2) SSH alias + key ---------------------------------------------------
say "2/5 SSH alias + key for $SERVER_USER@$SERVER_HOST…"
[ -f "$HOME/.ssh/id_ed25519" ] || ssh-keygen -t ed25519 -N "" -f "$HOME/.ssh/id_ed25519"
touch "$HOME/.ssh/config"; chmod 600 "$HOME/.ssh/config"
if ! grep -qiE "^[[:space:]]*Host([[:space:]].*)?\b$ALIAS\b" "$HOME/.ssh/config"; then
  cat >>"$HOME/.ssh/config" <<EOF

Host $ALIAS
    HostName $SERVER_HOST
    User $SERVER_USER
    IdentityFile ~/.ssh/id_ed25519
    StrictHostKeyChecking accept-new
EOF
  say "  added '$ALIAS' to ~/.ssh/config"
fi
if ssh -o BatchMode=yes -o ConnectTimeout=5 "$ALIAS" true 2>/dev/null; then
  say "  passwordless SSH already works."
else
  say "  authorizing THIS machine's key on the server (enter the server login password once)…"
  ssh-copy-id "$ALIAS" || say "  ssh-copy-id failed — add ~/.ssh/id_ed25519.pub to the server's authorized_keys manually."
fi

# ---- 3) helper scripts ----------------------------------------------------
say "3/5 helper scripts -> $BIN…"

cat >"$BIN/ib-console" <<'SH'
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
SH

cat >"$BIN/ib-console2" <<'SH'
#!/usr/bin/env bash
exec "$(dirname "$0")/ib-console" 5910
SH
cat >"$BIN/ib-console3" <<'SH'
#!/usr/bin/env bash
exec "$(dirname "$0")/ib-console" 5920
SH

cat >"$BIN/ib-console-handler" <<'SH'
#!/usr/bin/env bash
# Launched by the ibconsole:// scheme (dashboard ⧉ buttons). URL carries the VM's
# SPICE port, e.g. ibconsole://open?port=5910. Default 5900 (healer).
url="$1"
port=$(printf '%s' "$url" | grep -oP 'port=\K[0-9]+')
exec "$HOME/.local/bin/ib-console" "${port:-5900}" >>/tmp/ib-console.log 2>&1
SH

chmod +x "$BIN/ib-console" "$BIN/ib-console2" "$BIN/ib-console3" "$BIN/ib-console-handler"

# ---- 4) register the ibconsole:// scheme ----------------------------------
say "4/5 register ibconsole:// handler…"
cat >"$APPS/ib-console.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ib console
Exec=$BIN/ib-console-handler %u
Terminal=false
NoDisplay=true
MimeType=x-scheme-handler/ibconsole;
EOF
update-desktop-database "$APPS" 2>/dev/null || true
xdg-mime default ib-console.desktop x-scheme-handler/ibconsole 2>/dev/null || true

# ---- 5) done --------------------------------------------------------------
say "5/5 done."
say "PATH check: ensure $BIN is on PATH (COSMIC/most shells include it by default)."
say "Test now:  xdg-open 'ibconsole://open'   ← a healer console window should appear."
say "In the dashboard, the ⧉ console buttons will now work from this machine."
