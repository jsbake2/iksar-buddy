"""Autonomous harvest cycle — fully deplete a node (failures are normal, don't leave it
half-done). Sends Ctrl+9, captures ALL new log lines each attempt so we learn the
success / FAIL / DEPLETED patterns. Stops only when nothing happens for several tries.
Run ON 10.0.0.16."""
import time

from shared.guest import Guest

LOG = r"C:\Users\Public\Daybreak Game Company\Installed Games\EverQuest II\logs\Wuoshi\eq2log_Furyflatulence.txt"
g = Guest("iksar_buddy")


def ps(c, wait=12):
    return g.exec_ps(c, poll=int(wait * 5))   # 0.2s poll -> wait seconds total


def logsize():
    try:
        return int(ps(f'(Get-Item "{LOG}").Length').strip())
    except Exception:
        return 0


def logfrom(off):
    return ps(f"$fs=[IO.File]::Open(\"{LOG}\",'Open','Read','ReadWrite');$fs.Seek({off},'Begin')|Out-Null;"
              f"$sr=New-Object IO.StreamReader($fs);$sr.ReadToEnd();$sr.Close();$fs.Close()")


def harvest_key():
    g.run_ahk('SendMode "Event"\nSetKeyDelay 50, 30\nSetTitleMatchMode 2\n'
              'if !WinExist("EverQuest II")\n    ExitApp\n'
              'WinActivate("EverQuest II")\nWinWaitActive("EverQuest II",, 2)\nSleep 150\n'
              'Send("{Ctrl down}")\nSleep 50\nSend("9")\nSleep 50\nSend("{Ctrl up}")\n')


# only count lines that are about ME harvesting (filter the chat spam)
def mine_lines(txt):
    out = []
    for ln in txt.splitlines():
        low = ln.lower()
        if "tells " in low or "says " in low or " loc " in low:
            continue
        if any(k in low for k in ("you mine", "you forage", "you gather", "you fell", "you trap",
                "you acquire", "you catch", "you chop", "you cut", "harvest", "resource", "node",
                "you fail", "nothing", "no longer", "deplet", "you get better at")):
            out.append(ln.strip())
    return out


off = logsize(); print(f"log baseline {off}")
got = []; idle = 0
for pull in range(12):
    harvest_key()
    time.sleep(5)
    new = logfrom(off); off = logsize()
    rel = mine_lines(new)
    print(f"--- pull {pull+1} ---")
    for l in rel:
        print("   " + l)
    if rel:
        idle = 0
        got += [l for l in rel if "you get better" not in l.lower()]
    else:
        idle += 1; print(f"   (nothing relevant; idle {idle})")
        if idle >= 3:
            print("3 idle in a row -> node gone / out of range, STOP"); break
print("\n=== TOTAL relevant lines ===")
for gline in got:
    print("  " + gline)
