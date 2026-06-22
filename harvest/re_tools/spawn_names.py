"""Enumerate vtable-0x1782848 objects, decode name@+0x118 as ASCII, keep real names
(printable letters/spaces). Reveals the named entities in the zone regardless of how
position is stored. Also dumps the player Spawn's full 0x200 for layout analysis."""
import pymem, pymem.process, ctypes, struct, json, re
import ctypes.wintypes as w
PROC="EverQuest2.exe"
pm=pymem.Pymem(PROC)
m=pymem.process.module_from_name(pm.process_handle,PROC)
base=m.lpBaseOfDll
SPAWN_VT=base+0x1782848
patt=struct.pack("<Q",SPAWN_VT)
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
        if mbi.State==0x1000 and (mbi.Protect&0xff)==0x04 and 0<sz<=256*1024*1024:
            yield mbi.BaseAddress,sz
        addr=mbi.BaseAddress+sz if sz else addr+0x1000
def astr(a):
    try: b=pm.read_bytes(a,64)
    except Exception: return None
    e=b.find(b"\x00")
    if e<=0: return None
    try: s=b[:e].decode("latin-1")
    except Exception: return None
    return s
good=re.compile(r"^[A-Za-z][A-Za-z '\-]{2,39}$")
names={}
player_dump=None
for b,sz in regions():
    try: buf=pm.read_bytes(b,sz)
    except Exception: continue
    i=buf.find(patt)
    while i!=-1:
        if i+0x120<=len(buf):
            npq=struct.unpack_from("<Q",buf,i+0x118)[0]
            nm=astr(npq) if 0x10000<npq<0x7fffffffffff else None
            if nm and good.match(nm):
                names[nm]=names.get(nm,0)+1
                if nm=="Furyflatulence" and player_dump is None:
                    player_dump=buf[i:i+0x200].hex()
        i=buf.find(patt,i+1)
items=sorted(names.items(),key=lambda kv:-kv[1])
open(r"C:\ib\names.json","w").write(json.dumps({"distinct":len(names),
    "names":items[:200],"player_dump":player_dump},indent=1))
print(json.dumps({"distinct":len(names),"top":items[:25]}))
