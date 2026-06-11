import sys, json, subprocess
DOM="iksar_buddy"; W,H=1024,768
px,py=int(sys.argv[1]),int(sys.argv[2])
ax=int(px/W*32767); ay=int(py/H*32767)
ev={"execute":"input-send-event","arguments":{"events":[
 {"type":"abs","data":{"axis":"x","value":ax}},
 {"type":"abs","data":{"axis":"y","value":ay}},
 {"type":"btn","data":{"button":"left","down":True}},
 {"type":"btn","data":{"button":"left","down":False}}]}}
subprocess.run(["sudo","-n","virsh","-c","qemu:///system","qemu-monitor-command",DOM,json.dumps(ev)],check=True,
               stdout=subprocess.DEVNULL)
