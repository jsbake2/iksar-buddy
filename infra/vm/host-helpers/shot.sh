#!/bin/bash
sudo -n virsh -c qemu:///system screenshot iksar_buddy /tmp/ib-shot.ppm >/dev/null 2>&1
convert /tmp/ib-shot.ppm /tmp/ib-shot.png 2>/dev/null
sudo -n chown jbaker:jbaker /tmp/ib-shot.png
