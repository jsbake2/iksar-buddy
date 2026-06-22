"""Measure turn-rate (deg/ms) and walk-speed (m/ms) with clean single moves. No center-click
(it grabs targets); WinActivate-only focus, which is enough for movement keys."""
import base64,json,subprocess,time,math
DOM="iksar_buddy";V=["sudo","-n","virsh","-c","qemu:///system"]
def agent(c):
    r=subprocess.run(V+["qemu-agent-command",DOM,json.dumps(c)],capture_output=True,text=True)
    try:return json.loads(r.stdout)
    except:return {}
def gx(path,args,wait=12):
    pid=agent({"execute":"guest-exec","arguments":{"path":path,"arg":args,"capture-output":True}}).get("return",{}).get("pid")
    if not pid:return ""
    t0=time.time()
    while time.time()-t0<wait:
        time.sleep(0.3)
        d=agent({"execute":"guest-exec-status","arguments":{"pid":pid}}).get("return",{})
        if d.get("exited"):
            o=""
            for k in("out-data","err-data"):
                if d.get(k):o+=base64.b64decode(d[k]).decode("utf-8","replace")
            return o
    return ""
def sense():
    for ln in gx(r"C:\ib\py\python.exe",[r"C:\ib\nav_cal.py"]).splitlines()[::-1]:
        if ln.strip().startswith("{"):
            try:return json.loads(ln)
            except:pass
    return None
def act(body):
    ahk='WinActivate("ahk_exe EverQuest2.exe")\nSleep(250)\n'+body
    b=base64.b64encode(ahk.encode()).decode()
    gx("powershell",["-NoProfile","-Command",f"$b=[Convert]::FromBase64String('{b}');[IO.File]::WriteAllBytes('C:\\ib\\launcher.ahk',$b);Start-ScheduledTask -TaskName ibrun"],8)
    time.sleep(0.4)
def hold(key,ms):act(f'Send("{{{key} down}}")\nSleep({ms})\nSend("{{{key} up}}")\n')
# --- turn rate ---
for ms in (700,1200):
    a=sense();hold("Right",ms);b=sense()
    if a and b:
        dh=(b["hdg"]-a["hdg"]+540)%360-180
        print(f"turn Right {ms}ms: {a['hdg']} -> {b['hdg']} = {dh:+.1f} deg  ({dh/ms*1000:.1f} deg/s)")
# --- walk speed ---
for ms in (800,1500):
    a=sense();hold("w",ms);b=sense()
    if a and b:
        dd=math.hypot(b["x"]-a["x"],b["z"]-a["z"])
        print(f"walk w {ms}ms: moved {dd:.2f} m  ({dd/ms*1000:.2f} m/s)")
