#!/usr/bin/env bash
# Connect to the iksar_buddy Windows guest over RDP, from this workstation.
#
# The guest lives on the host's libvirt NAT (192.168.122.x), not reachable
# directly from the LAN — so we tunnel RDP over SSH to the host. No changes to
# the host's networking are needed (CLAUDE.md: don't touch the live stack).
#
# The guest IP is auto-discovered from libvirt each run (qemu-agent, then DHCP
# lease), so this keeps working even if the lease ever changes.
#
# Requires an RDP client: install freerdp  ->  sudo pacman -S freerdp
# (or remmina for a GUI front-end).
set -euo pipefail

HOST=10.0.0.16
SSH_USER=jbaker
GUEST=iksar_buddy
LOCAL_PORT=13389
RUSER=iksar
RPASS='IksarBuddy1!'   # throwaway local-account pw (also set in autounattend.xml)

echo "discovering ${GUEST} IP via libvirt on ${HOST}..."
GIP=$(ssh "${SSH_USER}@${HOST}" \
  "sudo -n virsh -c qemu:///system domifaddr ${GUEST} --source agent 2>/dev/null \
   | awk '/ipv4/{print \$4}' | cut -d/ -f1 | head -1")
if [ -z "${GIP:-}" ]; then
  GIP=$(ssh "${SSH_USER}@${HOST}" \
    "sudo -n virsh -c qemu:///system net-dhcp-leases default 2>/dev/null \
     | awk '/${GUEST}|IB/{print \$5}' | cut -d/ -f1 | head -1")
fi
[ -n "${GIP:-}" ] || { echo "ERROR: could not find guest IP (is the VM running?)"; exit 1; }
echo "guest IP: ${GIP}"

echo "opening SSH tunnel  127.0.0.1:${LOCAL_PORT} -> ${GIP}:3389 ..."
ssh -f -N -o ExitOnForwardFailure=yes -L "${LOCAL_PORT}:${GIP}:3389" "${SSH_USER}@${HOST}"
TUN_PID=$(pgrep -f "L ${LOCAL_PORT}:${GIP}:3389" | head -1 || true)
cleanup() { [ -n "${TUN_PID:-}" ] && kill "${TUN_PID}" 2>/dev/null || true; }
trap cleanup EXIT

# freerdp v3 uses /cert:ignore ; v2 uses /cert-ignore — try v3, fall back.
RDP_BIN=$(command -v xfreerdp3 || command -v xfreerdp || true)
[ -n "${RDP_BIN}" ] || { echo "no xfreerdp found — install: sudo pacman -S freerdp"; exit 1; }
echo "launching ${RDP_BIN} ..."
"${RDP_BIN}" /v:127.0.0.1:${LOCAL_PORT} /u:"${RUSER}" /p:"${RPASS}" \
  /cert:ignore /dynamic-resolution +clipboard /sound 2>/dev/null \
  || "${RDP_BIN}" /v:127.0.0.1:${LOCAL_PORT} /u:"${RUSER}" /p:"${RPASS}" \
       /cert-ignore /dynamic-resolution +clipboard
