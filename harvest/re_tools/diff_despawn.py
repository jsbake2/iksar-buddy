"""Diff pre-harvest vs now: positions that VANISHED near the node spot = the despawned
node's allocations. Backtrack each to its vtable to identify the harvest-node class."""
import pymem, pymem.process, ctypes, struct, json
import ctypes.wintypes as w
import numpy as np
np.seterr(all="ignore")
pm=pymem.Pymem("EverQuest2.exe"); m=pymem.process.module_from_name(pm.process_handle,"EverQuest2.exe")
base=m.lpBaseOfDll; mod_end=base+m.SizeOfImage
pre=json.load(open(r"C:\ib\near_preharvest.json"))
NX,NY,NZ=pre["player"]   # node was ~at the player's pre-harvest spot
class MBI(ctypes.Structure):
    _fields_=[("BaseAddress",ctypes.c_ulonglong),("AllocationBase",ctypes.c_ulonglong),
              ("AllocationProtect",w.DWORD),("__a1",w.DWORD),("RegionSize",ctypes.c_ulonglong),
              ("State",w.DWORD),("Protect",w.DWORD),("Type",w.DWORD),("__a2",w.DWORD)]
VQ=ctypes.windll.kernel32.VirtualQueryEx; VQ.restype=ctypes.c_size_t; h=pm.process_handle
def vtback(addr):
    """read backward from a position addr to the nearest module-range vtable -> obj base."""
    try: blob=pm.read_bytes(addr-0x300,0x300)
    except: return None,None
    for q in range(len(blob)-8,-1,-8):
        v=struct.unpack_from("<Q",blob,q)[0]
        if base<=v<mod_end: return addr-0x300+q, v
    return None,None
# preharvest addrs near the node (within 3m)
cand=[a for a in pre["addrs"] if abs(a["xyz"][0]-NX)<3 and abs(a["xyz"][2]-NZ)<3]
gone=[]
for a in cand:
    addr=int(a["addr"],16)
    try:
        x,y,z=struct.unpack("<fff",pm.read_bytes(addr,12))
        if abs(x-a["xyz"][0])>0.2 or abs(z-a["xyz"][2])>0.2:   # value changed/freed -> despawned
            ob,vt=vtback(addr)
            gone.append({"addr":a["addr"],"was":a["xyz"],"obj":hex(ob) if ob else None,
                         "vtable":hex(vt-base) if vt else None})
    except Exception:
        ob,vt=vtback(addr)
        gone.append({"addr":a["addr"],"was":a["xyz"],"freed":True,
                     "obj":hex(ob) if ob else None,"vtable":hex(vt-base) if vt else None})
from collections import Counter
vtc=Counter(g["vtable"] for g in gone if g["vtable"])
print(json.dumps({"node_spot":[NX,NY,NZ],"candidates_near_node":len(cand),
    "vanished":len(gone),"vanished_vtables":vtc.most_common(15),
    "sample":gone[:20]},indent=1))
