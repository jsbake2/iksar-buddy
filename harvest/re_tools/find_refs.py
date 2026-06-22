"""Find the spawn-list container: scan for pointers to the known player Spawn object,
then detect which reference sits inside an ARRAY of Spawn* (neighbors also point to
objects whose vtable == the Spawn class). That array is the spawn list."""
import pymem, pymem.process, ctypes, struct, json
import ctypes.wintypes as w
PROC="EverQuest2.exe"
pm=pymem.Pymem(PROC)
m=pymem.process.module_from_name(pm.process_handle,PROC)
base=m.lpBaseOfDll; mod_end=base+m.SizeOfImage
SPAWN_VT=base+0x1782848
PLAYER=int(__import__("sys").argv[1],16) if len(__import__("sys").argv)>1 else 0x1a775dd69a8
patt=struct.pack("<Q",PLAYER)
class MBI(ctypes.Structure):
    _fields_=[("BaseAddress",ctypes.c_ulonglong),("AllocationBase",ctypes.c_ulonglong),
              ("AllocationProtect",w.DWORD),("__a1",w.DWORD),("RegionSize",ctypes.c_ulonglong),
              ("State",w.DWORD),("Protect",w.DWORD),("Type",w.DWORD),("__a2",w.DWORD)]
VQ=ctypes.windll.kernel32.VirtualQueryEx; VQ.restype=ctypes.c_size_t
h=pm.process_handle
def regions():
    addr=0; mbi=MBI()
    while addr<0x7fffffffffff:
        if not VQ(h,ctypes.c_void_p(addr),ctypes.byref(mbi),ctypes.sizeof(mbi)): break
        sz=mbi.RegionSize
        if mbi.State==0x1000 and (mbi.Protect&0xff) in (0x04,0x02) and 0<sz<=256*1024*1024:
            yield mbi.BaseAddress,sz
        addr=mbi.BaseAddress+sz if sz else addr+0x1000
def is_spawn(ptr):
    if not (0x10000<ptr<0x7fffffffffff): return False
    try: return struct.unpack("<Q",pm.read_bytes(ptr,8))[0]==SPAWN_VT
    except Exception: return False
refs=[]
for b,sz in regions():
    try: buf=pm.read_bytes(b,sz)
    except Exception: continue
    i=buf.find(patt)
    while i!=-1:
        a=b+i
        # examine neighbors ±24 qwords; count Spawn* among them
        lo=max(0,i-24*8); hi=min(len(buf),i+24*8)
        neigh=[struct.unpack_from("<Q",buf,j)[0] for j in range(lo,hi-8,8)]
        nsp=sum(1 for p in neigh if is_spawn(p))
        refs.append({"at":hex(a),"region":hex(b),"spawn_neighbors":nsp})
        i=buf.find(patt,i+1)
arrays=[r for r in refs if r["spawn_neighbors"]>=3]
open(r"C:\ib\refs.json","w").write(json.dumps({"player_spawn":hex(PLAYER),
    "n_refs":len(refs),"array_candidates":arrays,"all_refs":refs[:200]},indent=1))
print(json.dumps({"player_spawn":hex(PLAYER),"n_refs":len(refs),
                  "array_candidates":arrays[:20]}))
