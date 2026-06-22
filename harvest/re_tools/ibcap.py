#!/usr/bin/env python3
"""Orchestrate: detached HW-bp watcher + in-place jitter via ibrun, then read capture."""
import base64, json, subprocess, sys, time
DOM="iksar_buddy"; V=["sudo","-n","virsh","-c","qemu:///system"]
def agent(cmd):
    r=subprocess.run(V+["qemu-agent-command",DOM,json.dumps(cmd)],capture_output=True,text=True)
    try: return json.loads(r.stdout)
    except Exception: return {"_err":r.stdout+r.stderr}
def gx(path,args,wait=60.0):
    r=agent({"execute":"guest-exec","arguments":{"path":path,"arg":args,"capture-output":True}})
    pid=r.get("return",{}).get("pid")
    if not pid: return "EXECFAIL:"+json.dumps(r)
    out="";t0=time.time()
    while time.time()-t0<wait:
        time.sleep(0.4)
        d=agent({"execute":"guest-exec-status","arguments":{"pid":pid}}).get("return",{})
        if d.get("exited"):
            for k in ("out-data","err-data"):
                if d.get(k): out+=base64.b64decode(d[k]).decode("utf-8","replace")
            return out
    return out+"[TIMEOUT]"
def ps(cmd,wait=30): return gx("powershell",["-NoProfile","-Command",cmd],wait)

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

def fire_ahk(script):
    b=base64.b64encode(script.encode()).decode()
    ps(f"$b=[Convert]::FromBase64String('{b}');[IO.File]::WriteAllBytes('C:\\ib\\launcher.ahk',$b);Start-ScheduledTask -TaskName ibrun",15)

mode=sys.argv[1] if len(sys.argv)>1 else "w"
print("[1] launch detached watcher (mode=%s)"%mode)
ps(r"Start-Process -FilePath 'C:\ib\py\python.exe' -ArgumentList 'C:\ib\find_accessors.py 16 %s' -WindowStyle Hidden"%mode,15)
print("[2] arm wait 2.5s"); time.sleep(2.5)
print("[3] jitter Fury via ibrun"); fire_ahk(AHK)
print("[4] wait for watcher to finish"); time.sleep(16)
print("[5] read capture:")
print(ps(r"Get-Content -Raw 'C:\ib\accessors.json'",20))
