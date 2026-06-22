"""Dump a real node object's structure (from the live harvestable array) and hunt its NAME.
Read-only. Node names are like 'stonecrest ore','high plains shrubbery' (seen in the log)."""
import pymem, pymem.process, struct, json, math
pm=pymem.Pymem("EverQuest2.exe"); m=pymem.process.module_from_name(pm.process_handle,"EverQuest2.exe")
base=m.lpBaseOfDll; mod_end=base+m.SizeOfImage
px=pm.read_float(base+0x1822b68); pz=pm.read_float(base+0x1822b68+8)
def u64(a):
    try: return struct.unpack("<Q",pm.read_bytes(a,8))[0]
    except: return 0
# nearest node obj from the array
best=None;bd=1e9
for off in range(0x177bf00,0x177c100,8):
    ptr=u64(base+off)
    if not(0x10000000000<ptr<0x7ff000000000): continue
    vt=u64(ptr)
    if not(base<=vt<mod_end and 0x1490000<=vt-base<=0x14f0000): continue
    try: x,y,z=struct.unpack("<fff",pm.read_bytes(ptr+0x60,12))
    except: continue
    if math.isfinite(x) and 5<abs(x)<5000 and 5<abs(z)<5000 and abs(y)<2000:
        d=math.hypot(x-px,z-pz)
        if d<bd: bd=d;best=(ptr,vt-base,[round(x,1),round(y,1),round(z,1)])
if not best:
    print(json.dumps({"err":"no node in array"})); raise SystemExit
ptr,vt,pos=best
obj=pm.read_bytes(ptr,0x300)
# scan object for pointers that deref to readable ascii strings (the name)
def rstr(a):
    try:
        b=pm.read_bytes(a,64); e=b.find(b"\x00")
        t=b[:e if e>0 else 0]
        if 2<=len(t)<=48 and all(32<=c<127 for c in t): return t.decode("latin-1")
    except: pass
    return None
strings=[]
for o in range(0,len(obj)-8,8):
    v=struct.unpack_from("<Q",obj,o)[0]
    if 0x10000<v<0x7fffffffffff:
        s=rstr(v)
        if s and any(c.isalpha() for c in s): strings.append({"off":hex(o),"str":s})
# also 2-level: deref each pointer field, scan THAT object for name strings
deep=[]
for o in range(0,len(obj)-8,8):
    v=struct.unpack_from("<Q",obj,o)[0]
    if not(0x10000000000<v<0x7ff000000000): continue
    try: sub=pm.read_bytes(v,0x120)
    except: continue
    for o2 in range(0,len(sub)-8,8):
        v2=struct.unpack_from("<Q",sub,o2)[0]
        if 0x10000<v2<0x7fffffffffff:
            s=rstr(v2)
            if s and ' ' in s and sum(c.isalpha() or c==' ' for c in s)/len(s)>0.85:
                deep.append({"path":"+%s->+%s"%(hex(o),hex(o2)),"str":s})
print(json.dumps({"node_obj":hex(ptr),"vt":hex(vt),"pos":pos,"dist":round(bd,1),
                  "direct_strings":strings[:20],"deep_name_candidates":deep[:20]},indent=1))
