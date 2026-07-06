#!/usr/bin/env python3
"""push/run/ps helper for the iksar_buddy VM (run ON 10.0.0.16).

Usage:
  ibhwbp_push.py push <local> <remote>   chunked-b64 file push
  ibhwbp_push.py run <path> [args...]    guest-exec any binary, print output
  ibhwbp_push.py ps <command>            run a PowerShell one-liner
"""
import sys

from shared.guest import Guest

if __name__ == "__main__":
    g = Guest("iksar_buddy")
    op = sys.argv[1]
    if op == "push":
        g.push_file(sys.argv[2], sys.argv[3])
        print(f"pushed {sys.argv[2]} -> {sys.argv[3]}")
    elif op == "run":
        print(g.exec_out(sys.argv[2], sys.argv[3:], wait=120.0))
    elif op == "ps":
        print(g.exec_ps(sys.argv[2], poll=600))
