"""Filter the target-diff candidates: a real 'current target' ptr points to an NPC OBJECT
(module-range vtable at +0) that contains a plausible world-position triplet."""
import pymem, pymem.process, struct, json, math
pm=pymem.Pymem("EverQuest2.exe"); m=pymem.process.module_from_name(pm.process_handle,"EverQuest2.exe")
base=m.lpBaseOfDll; mod_end=base+m.SizeOfImage
px=pm.read_float(base+0x1822b68); pz=pm.read_float(base+0x1822b68+8)
a=json.load(open(r"C:\ib\snap_pre.json")); b=json.load(open(r"C:\ib\snap_post.json"))
def rd(addr,n):
    try: return pm.read_bytes(addr,n)
    except: return b""
hits=[]
for off,bv in b.items():
    av=a.get(off)
    if av==bv: continue
    post=bv
    if not (0x10000000000<post<0x7ff000000000): continue
    obj=rd(post,0x200)
    if len(obj)<0x40: continue
    vt=struct.unpack_from("<Q",obj,0)[0]
    is_obj = base<=vt<mod_end
    # find a world-position triplet anywhere in first 0x200
    pos=None
    for o in range(0,len(obj)-12,4):
        x,y,z=struct.unpack_from("<fff",obj,o)
        if all(map(math.isfinite,(x,y,z))) and 5<abs(x)<4000 and 5<abs(z)<4000 and abs(y)<2000:
            d=math.hypot(x-px,z-pz)
            if d<300:                      # target should be within ~300m
                pos={"off":hex(o),"xyz":[round(x,1),round(y,1),round(z,1)],"dist":round(d,1)};break
    if is_obj or pos:
        hits.append({"static_off":hex(int(off)),"target_obj":hex(post),
                     "vtable":hex(vt-base) if is_obj else hex(vt),"is_obj":is_obj,"pos":pos})
hits.sort(key=lambda x:(not x["is_obj"], not x["pos"]))
print(json.dumps({"player":[round(px,1),round(pz,1)],"candidates":hits[:25]},indent=1))
