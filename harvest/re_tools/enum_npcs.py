"""Enumerate nearby NPCs/actors (vtable 0x1782848, pos@+0x20) and resolve each NAME via a
short pointer-graph BFS. Harvest nodes are NPCs (gather targets all NPCs) so they appear
here by name (beast den, stonecrest ore, ...) next to mobs (a timber badger, ...)."""
import pymem, pymem.process, ctypes, struct, json, math
import ctypes.wintypes as w
pm=pymem.Pymem("EverQuest2.exe"); m=pymem.process.module_from_name(pm.process_handle,"EverQuest2.exe")
base=m.lpBaseOfDll; mod_end=base+m.SizeOfImage
SPAWN_VT=base+0x1782848; patt=struct.pack("<Q",SPAWN_VT)
px=pm.read_float(base+0x1822b68); pz=pm.read_float(base+0x1822b68+8)
def u64(a):
    try: return struct.unpack("<Q",pm.read_bytes(a,8))[0]
    except: return 0
def rstr(a):
    try:
        b=pm.read_bytes(a,48); e=b.find(b"\x00")
        t=b[:e if e>0 else 0]
        if 2<=len(t)<=42 and all(32<=c<127 for c in t): return t.decode("latin-1")
    except: return None
    return None
def name_bfs(root):
    # try the known player path first
    p1=u64(root+0x200)
    if 0x10000<p1<0x7fffffffffff:
        p2=u64(p1+0xb8)
        if 0x10000<p2<0x7fffffffffff:
            s=rstr(p2+0x20)
            if s and any(ch.isalpha() for ch in s): return s
    # shallow BFS
    seen=set(); frontier=[root]
    for depth in range(3):
        nxt=[]
        for obj in frontier:
            if obj in seen: continue
            seen.add(obj)
            try: blob=pm.read_bytes(obj,0x240)
            except: continue
            for o in range(0,len(blob)-8,8):
                v=struct.unpack_from("<Q",blob,o)[0]
                if 0x10000<v<0x7fffffffffff:
                    s=rstr(v)
                    if s and (' ' in s or s[0].islower()) and any(ch.isalpha() for ch in s) and not s.startswith(('C:','\\',':')):
                        # filter to name-like (has space or looks like a name)
                        if len(s)>=3 and sum(ch.isalpha() or ch==' ' or ch=="'" for ch in s)/len(s)>0.8:
                            return s
                    if base>v and depth<2: nxt.append(v)
        frontier=nxt
    return None
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
acts=[];seen=set()
for b,sz in regions():
    try: buf=pm.read_bytes(b,sz)
    except: continue
    i=buf.find(patt)
    while i!=-1:
        oa=b+i
        if oa not in seen and i+0x2c<=len(buf):
            seen.add(oa)
            x,y,z=struct.unpack_from("<fff",buf,i+0x20)
            if all(map(math.isfinite,(x,y,z))) and abs(x)>5 and abs(z)>5 and abs(x)<1e5 and abs(z)<1e5 and abs(y)<1e4:
                d=math.hypot(x-px,z-pz)
                if d<70: acts.append({"addr":hex(oa),"xyz":[round(x,1),round(y,1),round(z,1)],"dist":round(d,1)})
        i=buf.find(patt,i+1)
acts.sort(key=lambda a:a["dist"])
for a in acts[:30]: a["name"]=name_bfs(int(a["addr"],16))
print(json.dumps({"player":[round(px,1),round(pz,1)],"n":len(acts),"near":acts[:30]},indent=1))
