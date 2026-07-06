#!/usr/bin/env python3
"""Orchestrate: detached HW-bp watcher + in-place jitter via ibrun, then read capture."""
import sys
import time

from shared.guest import Guest

g = Guest("iksar_buddy")

# AHK v2 jitter: focus game world, tap forward/back in place to force position writes
AHK = r'''
WinActivate("ahk_exe EverQuest2.exe")
Sleep(500)
Loop 8 {
  Send("{w down}")
  Sleep(350)
  Send("{w up}")
  Send("{s down}")
  Sleep(350)
  Send("{s up}")
}
'''.strip()

mode = sys.argv[1] if len(sys.argv) > 1 else "w"
print("[1] launch detached watcher (mode=%s)" % mode)
g.exec_ps(r"Start-Process -FilePath 'C:\ib\py\python.exe' -ArgumentList 'C:\ib\find_accessors.py 16 %s' -WindowStyle Hidden" % mode, poll=60)
print("[2] arm wait 2.5s"); time.sleep(2.5)
print("[3] jitter Fury via ibrun"); g.run_ahk(AHK)
print("[4] wait for watcher to finish"); time.sleep(16)
print("[5] read capture:")
print(g.exec_ps(r"Get-Content -Raw 'C:\ib\accessors.json'", poll=100))
