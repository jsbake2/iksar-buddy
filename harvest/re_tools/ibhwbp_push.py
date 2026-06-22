#!/usr/bin/env python3
"""push/run/ps helper for iksar_buddy VM (run ON 10.0.0.16)."""
import base64, json, subprocess, sys, time
DOM="iksar_buddy"; V=["sudo","-n","virsh","-c","qemu:///system"]
def agent(cmd):
    r=subprocess.run(V+["qemu-agent-command",DOM,json.dumps(cmd)],capture_output=True,text=True)
    try: return json.loads(r.stdout)
    except Exception: return {"_err":r.stdout+r.stderr}
def gx(path,args,wait=120.0):
    r=agent({"execute":"guest-exec","arguments":{"path":path,"arg":args,"capture-output":True}})
    pid=r.get("return",{}).get("pid")
    if not pid: return "EXECFAIL:"+json.dumps(r)
    out=""; t0=time.time()
    while time.time()-t0<wait:
        time.sleep(0.4)
        d=agent({"execute":"guest-exec-status","arguments":{"pid":pid}}).get("return",{})
        if d.get("exited"):
            for k in ("out-data","err-data"):
                if d.get(k): out+=base64.b64decode(d[k]).decode("utf-8","replace")
            return out
    return out+"\n[TIMEOUT]"
def push(local,remote):
    b64=base64.b64encode(open(local,"rb").read()).decode()
    gx("powershell",["-NoProfile","-Command",f"Remove-Item '{remote}','{remote}.b64' -EA SilentlyContinue"],30)
    for i in range(0,len(b64),6000):
        gx("powershell",["-NoProfile","-Command",f"Add-Content -Path '{remote}.b64' -Value '{b64[i:i+6000]}' -NoNewline"],30)
    gx("powershell",["-NoProfile","-Command",f"[IO.File]::WriteAllBytes('{remote}',[Convert]::FromBase64String((Get-Content -Raw '{remote}.b64')))"],30)
    print(f"pushed {local} -> {remote}")
if __name__=="__main__":
    op=sys.argv[1]
    if op=="push": push(sys.argv[2],sys.argv[3])
    elif op=="run": print(gx(sys.argv[2],sys.argv[3:]))
    elif op=="ps": print(gx("powershell",["-NoProfile","-Command",sys.argv[2]]))
