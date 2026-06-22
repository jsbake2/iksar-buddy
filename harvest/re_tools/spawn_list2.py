"""Spawn list v2: objects with vtable 0x1782848 and a REAL world position at +0x20.
This is the live actor list (player/nodes/mobs/NPCs). Names come via components (later)."""
import pymem, pymem.process, ctypes, struct, json, math
import ctypes.wintypes as w
PROC="EverQuest2.exe"
pm=pymem.Pymem(PROC); m=pymem.process.module_from_name(pm.process_handle,PROC)
base=m.lpBaseOfDll; SPAWN_VT=base+0x1782848; patt=struct.pack("<Q",SPAWN_VT)
px=pm.read_float(base+0x1822b68); py=pm.read_float(base+0x1822b68+4); pz=pm.read_float(base+0x1822b68+8)
class MBI(ctypes.Structure):
    _fields_=[("BaseAddress",ctypes.c_ulonglong),("AllocationBase",ctypes.c_ulonglong),
              ("AllocationProtect",w.DWORD),("__a1",w.DWORD),("RegionSize",ctypes.c_ulonglong),
              ("State",w.DWORD),("Protect",w.DWORD),("Type",w.DWORD),("__a2",w.DWORD)]
VQ=ctypes.windll.kernel32.VirtualQueryEx; VQ.restype=ctypes.c_size_t; h=pm.process_handle
def regions():
    addr=0; mbi=MBI()
    while addr<0x7fffffffffff:
        if not VQ(h,ctypes.c_void_p(addr),ctypes.byref(mbi),ctypes.sizeof(mbi)): break
        sz=mbi.RegionSize
        if mbi.State==0x1000 and (mbi.Protect&0xff)==0x04 and 0<sz<=256*1024*1024: yield mbi.BaseAddress,sz
        addr=mbi.BaseAddress+sz if sz else addr+0x1000
sp=[]
for b,sz in regions():
    try: buf=pm.read_bytes(b,sz)
    except Exception: continue
    i=buf.find(patt)
    while i!=-1:
        if i+0x40<=len(buf):
            x,y,z=struct.unpack_from("<fff",buf,i+0x20)
            if all(map(math.isfinite,(x,y,z))) and abs(x)>5 and abs(z)>5 and abs(x)<100000 and abs(z)<100000 and abs(y)<10000:
                sp.append({"addr":hex(b+i),"xyz":[round(x,1),round(y,1),round(z,1)],
                           "dist":round(math.hypot(x-px,z-pz),1)})
        i=buf.find(patt,i+1)
seen=set();uniq=[]
for s in sorted(sp,key=lambda s:s["dist"]):
    if s["addr"] in seen: continue
    seen.add(s["addr"]);uniq.append(s)
open(r"C:\ib\spawns2.json","w").write(json.dumps({"player":[round(px,1),round(py,1),round(pz,1)],
    "count":len(uniq),"spawns":uniq},indent=1))
print(json.dumps({"count":len(uniq),"player":[round(px,1),round(pz,1)],"nearest":uniq[:25]}))
