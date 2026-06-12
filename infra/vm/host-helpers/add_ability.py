#!/usr/bin/env python3
"""Insert one ability into the LIVE keymap (IB_CONFIG_DIR/ability_map.yaml) as a
text edit -- preserves the owner's comments/formatting and never clobbers their
edits. Idempotent (no-op if the ability already exists). Atomic write + .bak.
Usage: add_ability.py <name> <key> <desc>
"""
import os, sys, shutil

name, key, desc = sys.argv[1], sys.argv[2], " ".join(sys.argv[3:])
path = os.path.expanduser("~/ib-data/config/ability_map.yaml")
lines = open(path, encoding="utf-8").read().splitlines(keepends=True)

if any(line.rstrip().endswith(f"{name}:") and line.startswith("  ") for line in lines):
    print(f"{name} already present; no change")
    sys.exit(0)

block = [f"  {name}:\n", f"    key: '{key}'\n", "    mode: manual\n", f"    desc: {desc}\n"]
out, inserted = [], False
for line in lines:
    out.append(line)
    if not inserted and line.rstrip() == "abilities:":
        out.extend(block)
        inserted = True
if not inserted:
    sys.exit("no 'abilities:' section found")

shutil.copy2(path, path + ".bak")
tmp = path + ".tmp"
open(tmp, "w", encoding="utf-8").write("".join(out))
os.replace(tmp, path)
print(f"added {name} -> '{key}'")
