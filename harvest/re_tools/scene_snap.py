"""Scene snapshot — captures the heap landscape around the live player so two known
spawns (player + a node the owner parks next to) can pin the spawn-list container.

Dumps:
 - player pos/heading (from the module statics)
 - every heap object that looks like a SPAWN: a heap allocation whose first qword is a
   module-range pointer (vtable) AND which contains a world-position float triplet
   (plausible zone coords) at some offset. Records vtable, addr, pos, and the offset.
Writes C:\\ib\\scene.json."""
import pymem, pymem.process, ctypes, struct, json
import ctypes.wintypes as w
import numpy as np
np.seterr(all="ignore")
PROC="EverQuest2.exe"; OUT=r"C:\ib\scene.json"
pm=pymem.Pymem(PROC)
m=pymem.process.module_from_name(pm.process_handle,PROC)
base=m.lpBaseOfDll; size=m.SizeOfImage; mod_end=base+size
px=pm.read_float(base+0x1822b68); py=pm.read_float(base+0x1822b68+4); pz=pm.read_float(base+0x1822b68+8)
hdg=pm.read_float(base+0x1822b74)
class MBI(ctypes.Structure):
    _fields_=[("BaseAddress",ctypes.c_ulonglong),("AllocationBase",ctypes.c_ulonglong),
              ("AllocationProtect",w.DWORD),("__a1",w.DWORD),("RegionSize",ctypes.c_ulonglong),
              ("State",w.DWORD),("Protect",w.DWORD),("Type",w.DWORD),("__a2",w.DWORD)]
VQ=ctypes.windll.kernel32.VirtualQueryEx; VQ.restype=ctypes.c_size_t
h=pm.process_handle; RW={0x04}   # only RW heap (objects live here)
def regions():
    addr=0; mbi=MBI()
    while addr<0x7fffffffffff:
        if not VQ(h,ctypes.c_void_p(addr),ctypes.byref(mbi),ctypes.sizeof(mbi)): break
        sz=mbi.RegionSize
        if mbi.State==0x1000 and (mbi.Protect&0xff) in RW and 0<sz<=256*1024*1024:
            yield mbi.BaseAddress,sz
        addr=mbi.BaseAddress+sz if sz else addr+0x1000
# A world position near the player: within ~80m horizontally, Y within ~40.
RAD=80.0
def near(x,y,z):
    return abs(x-px)<RAD and abs(z-pz)<RAD and abs(y-py)<40 and (abs(x)>1 or abs(z)>1)
cands=[]
for b,sz in regions():
    try: buf=pm.read_bytes(b,sz)
    except Exception: continue
    arr=np.frombuffer(buf[:(len(buf)//4)*4],dtype="<f4")
    if len(arr)<3: continue
    mask=(np.abs(arr[:-2]-px)<RAD)&(np.abs(arr[2:]-pz)<RAD)&(np.abs(arr[1:-1]-py)<40)
    for i in np.where(mask)[0]:
        x,y,z=float(arr[i]),float(arr[i+1]),float(arr[i+2])
        if not near(x,y,z): continue
        pa=b+int(i)*4
        # look back up to 0x400 for a vtable (module-range ptr, 8-aligned) = object start
        lo=max(0,int(i)*4-0x400)
        objbase=None;vt=None
        for q in range(int(i)*4-8,lo,-8):
            if q+8>len(buf):continue
            v=struct.unpack_from("<Q",buf,q)[0]
            if base<=v<mod_end:
                objbase=b+q; vt=v; break
        cands.append({"pos_addr":hex(pa),"xyz":[round(x,2),round(y,2),round(z,2)],
                      "dist":round(((x-px)**2+(z-pz)**2)**0.5,2),
                      "objbase":hex(objbase) if objbase else None,
                      "vtable":hex(vt-base) if vt else None,
                      "pos_off":hex(pa-objbase) if objbase else None})
# group by vtable to see which classes are common (spawn vtable should repeat)
from collections import Counter
vtc=Counter(c["vtable"] for c in cands if c["vtable"])
res={"player":{"pos":[round(px,2),round(py,2),round(pz,2)],"hdg":round(hdg,1)},
     "n_candidates":len(cands),
     "vtable_histogram":vtc.most_common(25),
     "candidates":sorted(cands,key=lambda c:c["dist"])[:120]}
open(OUT,"w").write(json.dumps(res,indent=1))
print(json.dumps({"n":len(cands),"top_vtables":vtc.most_common(8),
                  "player":res["player"]}))
