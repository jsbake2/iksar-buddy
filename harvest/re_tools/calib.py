"""Measure turn-rate (deg/ms) and walk-speed (m/ms) with clean single moves. No center-click
(it grabs targets); WinActivate-only focus, which is enough for movement keys."""
import json
import math
import time

from shared.guest import Guest

g = Guest("iksar_buddy")


def sense():
    for ln in g.exec_out(r"C:\ib\py\python.exe", [r"C:\ib\nav_cal.py"], wait=12).splitlines()[::-1]:
        if ln.strip().startswith("{"):
            try:
                return json.loads(ln)
            except Exception:
                pass
    return None


def act(body):
    g.run_ahk('WinActivate("ahk_exe EverQuest2.exe")\nSleep(250)\n' + body)
    time.sleep(0.4)


def hold(key, ms):
    act(f'Send("{{{key} down}}")\nSleep({ms})\nSend("{{{key} up}}")\n')


# --- turn rate ---
for ms in (700, 1200):
    a = sense(); hold("Right", ms); b = sense()
    if a and b:
        dh = (b["hdg"] - a["hdg"] + 540) % 360 - 180
        print(f"turn Right {ms}ms: {a['hdg']} -> {b['hdg']} = {dh:+.1f} deg  ({dh/ms*1000:.1f} deg/s)")
# --- walk speed ---
for ms in (800, 1500):
    a = sense(); hold("w", ms); b = sense()
    if a and b:
        dd = math.hypot(b["x"] - a["x"], b["z"] - a["z"])
        print(f"walk w {ms}ms: moved {dd:.2f} m  ({dd/ms*1000:.2f} m/s)")
