"""Read-only: find CLUSTERS of entity-pointers in module static data (handles strided/gappy
arrays). Reveals the game's nearby-entity lists by what vtables each cluster points to."""
import pymem, pymem.process, ctypes, struct, json, math
import ctypes.wintypes as w
from collections import Counter
pm=pymem.Pymem("EverQuest2.exe"); m=pymem.process.module_from_name(pm.process_handle,"EverQuest2.exe")
base=m.lpBaseOfDll; size=m.SizeOfImage; mod_end=base+size
px=pm.read_float(base+0x1822b68); pz=pm.read_float(base+0x1822b68+8)
class MBI(ctypes.Structure):
    _fields_=[("BaseAddress",ctypes.c_ulonglong),("AllocationBase",ctypes.c_ulonglong),
              ("AllocationProtect",w.DWORD),("__a1",w.DWORD),("RegionSize",ctypes.c_ulonglong),
              ("State",w.DWORD),("Protect",w.DWORD),("Type",w.DWORD),("__a2",w.DWORD)]
VQ=ctypes.windll.kernel32.VirtualQueryEx; VQ.restype=ctypes.c_size_t; h=pm.process_handle
_vtcache={}
def is_mod_vt(v):
    return base<=v<mod_end
def entity(ptr):
    if not(0x10000000000<ptr<0x7ff000000000): return None
    try: o=pm.read_bytes(ptr,0xc0)
    except: return None
    vt=struct.unpack_from("<Q",o,0)[0]
    if not is_mod_vt(vt): return None
    for off in (0x18,0x20,0x40,0x60,0x70,0x88,0x90):
        x,y,z=struct.unpack_from("<fff",o,off)
        if all(map(math.isfinite,(x,y,z))) and 5<abs(x)<6000 and 5<abs(z)<6000 and abs(y)<3000 and math.hypot(x-px,z-pz)<700:
            return (vt-base,off,[round(x,1),round(y,1),round(z,1)])
    return None
# collect all entity-pointer hits (offset -> (vt,posoff,pos))
hits=[]
addr=base; mbi=MBI()
while addr<mod_end:
    if not VQ(h,ctypes.c_void_p(addr),ctypes.byref(mbi),ctypes.sizeof(mbi)): break
    sz=mbi.RegionSize
    if mbi.State==0x1000 and (mbi.Protect&0xff)==0x04:
        try: buf=pm.read_bytes(mbi.BaseAddress,sz)
        except: buf=b""
        for o in range(0,len(buf)-8,8):
            ptr=struct.unpack_from("<Q",buf,o)[0]
            e=entity(ptr)
            if e: hits.append((mbi.BaseAddress+o-base,ptr,e))
    addr=mbi.BaseAddress+sz if sz else addr+0x1000
# cluster by offset proximity (<=0x40 apart)
hits.sort(key=lambda x:x[0])
clusters=[]; cur=[]
for hgap in hits:
    if cur and hgap[0]-cur[-1][0]>0x40:
        if len(cur)>=3: clusters.append(cur)
        cur=[]
    cur.append(hgap)
if len(cur)>=3: clusters.append(cur)
out=[]
for c in clusters:
    vts=Counter(hex(e[2][0]) for e in c)
    poffs=Counter(hex(e[2][1]) for e in c)
    out.append({"at":hex(c[0][0]),"count":len(c),"stride":hex(c[1][0]-c[0][0]) if len(c)>1 else "?",
                "vtables":vts.most_common(5),"pos_offsets":poffs.most_common(3),
                "positions":[e[2][2] for e in c[:8]]})
out.sort(key=lambda a:-a["count"])
print(json.dumps({"player":[round(px,1),round(pz,1)],"total_hits":len(hits),
                  "n_clusters":len(out),"clusters":out[:15]},indent=1))
