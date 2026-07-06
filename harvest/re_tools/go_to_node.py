"""Host-driven closed-loop nav: turn toward a target world (X,Z), then walk to it.
Senses via in-guest nav_cal.py, acts via ibrun AHK. Slow (guest-exec per step) but proves
the full stack: pos+heading memory -> control -> movement. Run ON 10.0.0.16.
Usage: python go_to_node.py <tx> <tz>"""
import json
import math
import sys
import time

from shared.guest import Guest

TX, TZ = float(sys.argv[1]), float(sys.argv[2])
g = Guest("iksar_buddy")


def sense():
    for ln in g.exec_out(r"C:\ib\py\python.exe", [r"C:\ib\nav_cal.py"], wait=12).splitlines()[::-1]:
        if ln.strip().startswith("{"):
            try:
                return json.loads(ln)
            except Exception:
                pass
    return None


def act(ahk):
    g.run_ahk(ahk)
    time.sleep(0.6)


FOCUS = 'WinActivate("ahk_exe EverQuest2.exe")\nSleep(250)\nClick(960,540)\nSleep(200)\n'


def turn(key, ms):
    act(FOCUS + f'Send("{{{key} down}}")\nSleep({ms})\nSend("{{{key} up}}")\n')


def fwd(ms):
    act(FOCUS + f'Send("{{w down}}")\nSleep({ms})\nSend("{{w up}}")\n')


def bearing(px, pz):
    return math.degrees(math.atan2(TX - px, TZ - pz)) % 360


turn_sign = None
for step in range(40):
    s = sense()
    if not s:
        print("sense fail"); continue
    px, pz, H = s["x"], s["z"], s["hdg"]
    d = math.hypot(TX - px, TZ - pz); B = bearing(px, pz)
    diff = (B - H + 540) % 360 - 180
    print(f"step {step}: pos=({px},{pz}) hdg={H} -> tgt=({TX},{TZ}) dist={d:.1f} bearing={B:.0f} diff={diff:+.0f}")
    if d < 3.0:
        print("ARRIVED"); break
    if abs(diff) > 20:
        # turn toward bearing; auto-detect sign once
        if turn_sign is None:
            h0 = H; turn("Right", 350); s2 = sense()
            dh = ((s2["hdg"] - h0 + 540) % 360 - 180) if s2 else 0
            turn_sign = 1 if dh > 0 else -1
            print(f"   [calib] Right pulse changed hdg by {dh:+.0f} -> Right={'+' if turn_sign > 0 else '-'}")
            continue
        key = "Right" if (diff > 0) == (turn_sign > 0) else "Left"
        ms = min(600, max(150, int(abs(diff) * 4)))
        turn(key, ms)
    else:
        fwd(min(1600, max(400, int(d * 120))))
print("done")
