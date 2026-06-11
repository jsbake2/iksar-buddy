import sys, json, subprocess, base64, time
DOM="iksar_buddy"
def agent(cmd):
    r=subprocess.run(["sudo","-n","virsh","-c","qemu:///system","qemu-agent-command",DOM,json.dumps(cmd)],
                     capture_output=True,text=True)
    if r.returncode!=0: raise SystemExit("virsh err: "+r.stderr)
    return json.loads(r.stdout)["return"]
psh=sys.argv[1]
ret=agent({"execute":"guest-exec","arguments":{"path":"powershell.exe",
     "arg":["-NoProfile","-NonInteractive","-Command",psh],"capture-output":True}})
pid=ret["pid"]
for _ in range(180):
    st=agent({"execute":"guest-exec-status","arguments":{"pid":pid}})
    if st.get("exited"):
        out=base64.b64decode(st.get("out-data","")).decode(errors="replace")
        err=base64.b64decode(st.get("err-data","")).decode(errors="replace")
        print("EXITCODE",st.get("exitcode"))
        if out.strip(): print("OUT:\n"+out)
        if err.strip(): print("ERR:\n"+err)
        break
    time.sleep(1)
else:
    print("timeout")
