"""Named spawn list: vtable 0x1782848 actors, position@+0x20, name via [+0x200][+0xb8]+0x20."""
import pymem, pymem.process, ctypes, struct, json, math
import ctypes.wintypes as w
pm=pymem.Pymem("EverQuest2.exe"); m=pymem.process.module_from_name(pm.process_handle,"EverQuest2.exe")
base=m.lpBaseOfDll; SPAWN_VT=base+0x1782848; patt=struct.pack("<Q",SPAWN_VT)
px=pm.read_float(base+0x1822b68); py=pm.read_float(base+0x1822b68+4); pz=pm.read_float(base+0x1822b68+8)
def u64(a):
    try: return struct.unpack("<Q",pm.read_bytes(a,8))[0]
    except: return 0
def rstr(a):
    try:
        b=pm.read_bytes(a,64); e=b.find(b"\x00")
        t=b[:e if e>0 else 0]
        return t.decode("latin-1") if t and all(32<=c<127 for c in t) else None
    except: return None
def name_of(actor):
    p1=u64(actor+0x200)
    if not(0x10000<p1<0x7fffffffffff): return None
    p2=u64(p1+0xb8)
    if not(0x10000<p2<0x7fffffffffff): return None
    return rstr(p2+0x20)
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
sp=[];seen=set()
for b,sz in regions():
    try: buf=pm.read_bytes(b,sz)
    except: continue
    i=buf.find(patt)
    while i!=-1:
        oa=b+i
        if oa not in seen and i+0x40<=len(buf):
            seen.add(oa)
            x,y,z=struct.unpack_from("<fff",buf,i+0x20)
            if all(map(math.isfinite,(x,y,z))) and abs(x)>5 and abs(z)>5 and abs(x)<1e5 and abs(z)<1e5 and abs(y)<1e4:
                sp.append({"addr":hex(oa),"name":name_of(oa),
                           "xyz":[round(x,1),round(y,1),round(z,1)],"dist":round(math.hypot(x-px,z-pz),1)})
        i=buf.find(patt,i+1)
sp=sorted(sp,key=lambda s:s["dist"])
named=[s for s in sp if s["name"]]
open(r"C:\ib\named_spawns.json","w").write(json.dumps({"player":[round(px,1),round(py,1),round(pz,1)],
    "total":len(sp),"named":len(named),"spawns":sp},indent=1))
print(json.dumps({"total":len(sp),"named":len(named),"nearest":[{k:s[k] for k in('name','dist','xyz')} for s in sp[:18]]},indent=1))
