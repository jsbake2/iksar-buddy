import base64,json,subprocess,sys,time
DOM="iksar_buddy";V=["sudo","-n","virsh","-c","qemu:///system"]
def agent(c):
    r=subprocess.run(V+["qemu-agent-command",DOM,json.dumps(c)],capture_output=True,text=True)
    try:return json.loads(r.stdout)
    except:return {"_e":r.stdout+r.stderr}
def gx(path,args,wait=20):
    pid=agent({"execute":"guest-exec","arguments":{"path":path,"arg":args,"capture-output":True}}).get("return",{}).get("pid")
    if not pid:return ""
    t0=time.time()
    while time.time()-t0<wait:
        time.sleep(0.4)
        d=agent({"execute":"guest-exec-status","arguments":{"pid":pid}}).get("return",{})
        if d.get("exited"):
            o=""
            for k in("out-data","err-data"):
                if d.get(k):o+=base64.b64decode(d[k]).decode("utf-8","replace")
            return o
    return ""
ahk=sys.stdin.read()
b=base64.b64encode(ahk.encode()).decode()
print(gx("powershell",["-NoProfile","-Command",
    f"$b=[Convert]::FromBase64String('{b}');[IO.File]::WriteAllBytes('C:\\ib\\launcher.ahk',$b);Start-ScheduledTask -TaskName ibrun"],15))
