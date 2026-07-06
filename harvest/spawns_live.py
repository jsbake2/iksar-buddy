"""One-pass nearby-entity scan for the dashboard: actors (vtable 0x1782848, pos+0x20)
and harvest-node candidates (vtable 0x14eb850, pos+0x60), within RADIUS of the player.
Emits JSON to stdout. ~5-6s; the host caches it on a background timer."""
import pymem, pymem.process, ctypes, struct, json, math
import ctypes.wintypes as w
try:  # offsets from the ONE shared module (was a stale inline copy — REFACTOR P0.4)
    import offsets as O                    # in-guest sibling
except ImportError:
    from guest_agent import offsets as O
pm=pymem.Pymem(O.PROC); m=pymem.process.module_from_name(pm.process_handle,O.PROC)
base=m.lpBaseOfDll
ACTOR_VT=struct.pack("<Q",base+O.ACTOR_VT)
NODE_VT=struct.pack("<Q",base+O.NODE_CLASSES[0][0])
px=pm.read_float(base+O.POS_OFF); py=pm.read_float(base+O.POS_OFF+4); pz=pm.read_float(base+O.POS_OFF+8)
RAD=120.0
def u64(a):
    try: return struct.unpack("<Q",pm.read_bytes(a,8))[0]
    except: return 0
def rstr(a):
    try:
        b=pm.read_bytes(a,64); e=b.find(b"\x00"); t=b[:e if e>0 else 0]
        return t.decode("latin-1") if t and all(32<=c<127 for c in t) else None
    except: return None
def actor_name(o):
    p1=u64(o+0x200)
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
def good(x,y,z): return all(map(math.isfinite,(x,y,z))) and abs(x)>5 and abs(z)>5 and abs(x)<1e5 and abs(z)<1e5 and abs(y)<1e4
mobs={};nodes={}
for b,sz in regions():
    try: buf=pm.read_bytes(b,sz)
    except: continue
    i=buf.find(ACTOR_VT)
    while i!=-1:
        if i+0x2c<=len(buf):
            x,y,z=struct.unpack_from("<fff",buf,i+0x20)
            if good(x,y,z) and math.hypot(x-px,z-pz)<RAD: mobs[b+i]=(x,y,z)
        i=buf.find(ACTOR_VT,i+1)
    i=buf.find(NODE_VT)
    while i!=-1:
        if i+0x6c<=len(buf):
            x,y,z=struct.unpack_from("<fff",buf,i+0x60)
            if good(x,y,z) and math.hypot(x-px,z-pz)<RAD: nodes[b+i]=(x,y,z)
        i=buf.find(NODE_VT,i+1)
def pack(d,withname):
    out=[]
    for a,(x,y,z) in d.items():
        e={"addr":hex(a),"xyz":[round(x,1),round(y,1),round(z,1)],"dist":round(math.hypot(x-px,z-pz),1)}
        if withname:
            nm=actor_name(a)
            if nm: e["name"]=nm
        out.append(e)
    return sorted(out,key=lambda e:e["dist"])
res={"player":[round(px,1),round(py,1),round(pz,1)],
     "mobs":pack(mobs,True),"nodes":pack(nodes,False)}
print(json.dumps(res))
