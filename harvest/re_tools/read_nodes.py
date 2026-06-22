"""Read the game's nearby-harvestable array (module-static) -> real nodes only.
Scans a window of the static data for pointers to harvest-node objects (vtable in the
0x149x-0x14ex family) and reads each node's world position at obj+0x60."""
import pymem, pymem.process, struct, json, math
pm=pymem.Pymem("EverQuest2.exe"); m=pymem.process.module_from_name(pm.process_handle,"EverQuest2.exe")
base=m.lpBaseOfDll; mod_end=base+m.SizeOfImage
px=pm.read_float(base+0x1822b68); pz=pm.read_float(base+0x1822b68+8)
def u64(a):
    try: return struct.unpack("<Q",pm.read_bytes(a,8))[0]
    except: return 0
def vt_of(p):
    v=u64(p)
    return v-base if base<=v<mod_end else None
# scan a generous window of static data around the array we found
LO=0x177bf00; HI=0x177c200
nodes=[]
for off in range(LO,HI,8):
    ptr=u64(base+off)
    if not (0x10000000000<ptr<0x7ff000000000): continue
    vt=vt_of(ptr)
    if vt is None or not (0x1490000<=vt<=0x14f0000): continue   # harvest-node vtable family
    try:
        x,y,z=struct.unpack("<fff",pm.read_bytes(ptr+0x60,12))
    except: continue
    if not (math.isfinite(x) and 5<abs(x)<5000 and 5<abs(z)<5000 and abs(y)<2000): continue
    nodes.append({"slot":hex(off),"obj":hex(ptr),"vt":hex(vt),
                  "xyz":[round(x,1),round(y,1),round(z,1)],"dist":round(math.hypot(x-px,z-pz),1)})
nodes.sort(key=lambda n:n["dist"])
print(json.dumps({"player":[round(px,1),round(pz,1)],"array_at":hex(LO),
                  "n_nodes":len(nodes),"nodes":nodes},indent=1))
