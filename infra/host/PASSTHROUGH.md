# Host setup — RTX 4070 passthrough + live stack (CachyOS, 10.0.0.16)

How the iksar_buddy VM gets a real GPU and how the brain/agent run. Done 2026-06-11.

## Why
Software-rendering EQ2 through virtio-gpu pegged the CPU (qemu ~748%, host load
~10) and was too slow even for autofollow. Passing the idle RTX 4070 to the VM
fixed it: qemu dropped to ~110%, host load to ~3, 4070 at ~37%.

## GPU split
- **iGPU (UHD 770, 8086:a780)** → host display (COSMIC desktop on i915). Monitor
  must be on the **motherboard/onboard port**, not the 4070. Set BIOS primary
  display to Auto/IGFX.
- **RTX 4070 (10de:2786 + audio 10de:22bc)** → dedicated to the VM via vfio.
  (Local AI on the 4070 was dropped in favor of the Claude API, so no juggling.)

## Host changes (all persistent)
1. **IOMMU** — GRUB `GRUB_CMDLINE_LINUX_DEFAULT` += `intel_iommu=on iommu=pt`,
   then `grub-mkconfig -o /boot/grub/grub.cfg`. Backup: `/etc/default/grub.ib-bak`.
2. **vfio binds the 4070 at boot** — `/etc/modprobe.d/vfio.conf` (see
   modprobe-vfio.conf) + `MODULES=(vfio_pci vfio vfio_iommu_type1)` in
   `/etc/mkinitcpio.conf` (backup `.ib-bak`), then `mkinitcpio -P`. Verify after
   reboot: `lspci -nnks 01:00.0` shows `vfio-pci`; iGPU shows `i915`.
3. **VM XML** — two `<hostdev managed='yes'>` for 01:00.0 + 01:00.1 (in
   infra/vm/iksar_buddy.xml). `managed` works because vfio already owns them.
4. **ufw** (was the cause of two SSH/network scares):
   - `ufw allow from 10.0.0.0/24 to any port 22 proto tcp`  (SSH from LAN)
   - `ufw allow in on virbr0`  (DHCP/DNS for the guest — without this the guest
     gets an APIPA 169.254 address and has no network)
   - `ufw allow from 10.0.0.0/24 to any port 18080 proto tcp`  (dashboard)
   All persist in /etc/ufw and ufw is enabled on boot.
5. **systemd** — ib-brain.service + ib-agent.service (this dir), enabled. VM
   autostarts: `virsh autostart iksar_buddy`.

## Guest changes
- **nvidia driver** installed (610.47 desktop DCH). The 4070 shows OK, **no
  Error 43** (modern driver allows consumer GPUs in a VM; no hypervisor hiding
  needed despite the hyperv enlightenments).
- **Hybrid rendering** — EQ2 is set to GpuPreference=2 (high-perf = 4070) in
  iksar's hive: `HKU\<iksar-SID>\Software\Microsoft\DirectX\UserGpuPreferences`,
  value = full path to `EverQuest2.exe`, data `GpuPreference=2;`. EQ2 renders on
  the 4070 and DWM composites to the virtio-gpu display, so **SPICE and
  `virsh screenshot` still see the game** — the host-side sensors keep working,
  no in-guest capture needed. (Use borderless/windowed, not exclusive fullscreen,
  or the composite path won't apply.)
- **Auto-login** — `AutoAdminLogon=1` (user iksar, domain IB) in HKLM Winlogon,
  so the console session is always logged in for rendering + the launcher.

## Known issue
First boot after a GPU/initramfs config change has twice come up with no video +
no sshd (network up/pingable) and needed one hard power-cycle; the second boot is
clean. Suspect a display-manager/GPU-transition race on the config-change boot.
Not yet root-caused — if the host ever reboots unattended and doesn't return,
hard-cycle once. Worth hardening (drop plymouth `splash`, or order the display
manager after the GPU settles).

## Reverting passthrough
Restore `/etc/default/grub.ib-bak` + `/etc/mkinitcpio.conf.ib-bak`, remove
`/etc/modprobe.d/vfio.conf`, `mkinitcpio -P`, `grub-mkconfig`, drop the two
`<hostdev>` from the VM XML, reboot. The 4070 returns to nvidia on the host.
