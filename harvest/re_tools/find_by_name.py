"""Find an entity object by NAME near the player (vectorized). Given a name substring,
locate its name strings, then find objects that POINT at them (a qword == a string addr)
and within each object a float triplet ~= the player's position (entity parked on player).
Reveals canonical layout: object base, vtable, name-ptr offset, position offset.
Usage: python find_by_name.py "<name substring>" [radius]"""
import pymem, pymem.process, ctypes, struct, json, sys, math
import ctypes.wintypes as w
import numpy as np
np.seterr(all="ignore")
PROC="EverQuest2.exe"
NEEDLE=(sys.argv[1] if len(sys.argv)>1 else "Furyflatulence").encode("latin-1")
RAD=float(sys.argv[2]) if len(sys.argv)>2 else 6.0
pm=pymem.Pymem(PROC)
m=pymem.process.module_from_name(pm.process_handle,PROC)
base=m.lpBaseOfDll; mod_end=base+m.SizeOfImage
px=pm.read_float(base+0x1822b68); py=pm.read_float(base+0x1822b68+4); pz=pm.read_float(base+0x1822b68+8)
class MBI(ctypes.Structure):
    _fields_=[("BaseAddress",ctypes.c_ulonglong),("AllocationBase",ctypes.c_ulonglong),
              ("AllocationProtect",w.DWORD),("__a1",w.DWORD),("RegionSize",ctypes.c_ulonglong),
              ("State",w.DWORD),("Protect",w.DWORD),("Type",w.DWORD),("__a2",w.DWORD)]
VQ=ctypes.windll.kernel32.VirtualQueryEx; VQ.restype=ctypes.c_size_t
h=pm.process_handle
def regions(prot):
    addr=0; mbi=MBI()
    while addr<0x7fffffffffff:
        if not VQ(h,ctypes.c_void_p(addr),ctypes.byref(mbi),ctypes.sizeof(mbi)): break
        sz=mbi.RegionSize
        if mbi.State==0x1000 and (mbi.Protect&0xff) in prot and 0<sz<=256*1024*1024:
            yield mbi.BaseAddress,sz
        addr=mbi.BaseAddress+sz if sz else addr+0x1000
# 1) name string start-addresses
str_addrs=[]
for b,sz in regions((0x04,0x02)):
    try: buf=pm.read_bytes(b,sz)
    except Exception: continue
    i=buf.find(NEEDLE)
    while i!=-1:
        s=i
        while s>0 and 32<=buf[s-1]<127: s-=1
        str_addrs.append(b+s)
        i=buf.find(NEEDLE,i+1)
str_arr=np.array(sorted(set(str_addrs)),dtype="<u8")
# 2) objects pointing at any name string (vectorized membership)
def near(x,y,z): return abs(x-px)<RAD and abs(z-pz)<RAD and abs(y-py)<8
results=[]; seen=set()
for b,sz in regions((0x04,)):
    try: buf=pm.read_bytes(b,sz)
    except Exception: continue
    q=np.frombuffer(buf[:(len(buf)//8)*8],dtype="<u8")
    if not len(q): continue
    hitpos=np.where(np.isin(q,str_arr))[0]
    for hi in hitpos:
        j=int(hi)*8
        lo=max(0,j-0x200); ob=None;vt=None
        for qoff in range(j-8,lo,-8):
            v=struct.unpack_from("<Q",buf,qoff)[0]
            if base<=v<mod_end: ob=b+qoff; vt=v; break
        if not ob or ob in seen: continue
        seen.add(ob)
        obj=buf[ob-b:ob-b+0x300]
        posoff=None;posval=None
        for o in range(0,len(obj)-12,4):
            x,y,z=struct.unpack_from("<fff",obj,o)
            if all(map(math.isfinite,(x,y,z))) and near(x,y,z):
                posoff=hex(o);posval=[round(x,2),round(y,2),round(z,2)];break
        results.append({"obj":hex(ob),"vtable":hex(vt-base),
                        "name_off":hex(b+j-ob),"pos_off":posoff,"pos":posval})
withpos=[r for r in results if r["pos_off"]]
open(r"C:\ib\named.json","w").write(json.dumps({"needle":NEEDLE.decode(),
    "player":[round(px,2),round(py,2),round(pz,2)],"n_strings":len(str_addrs),
    "n_objs":len(results),"with_pos":withpos,"objects":results[:60]},indent=1))
print(json.dumps({"needle":NEEDLE.decode(),"n_strings":len(str_addrs),
                  "n_objs":len(results),"with_pos":withpos[:8]}))
