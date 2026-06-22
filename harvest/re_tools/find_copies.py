"""Fast (numpy) enumeration of every readable copy of the live player XYZ triplet.
Writes C:\\ib\\copies.json. Classifies module vs heap; counts name strings."""
import pymem, pymem.process, ctypes, json
import ctypes.wintypes as w
import numpy as np
np.seterr(all="ignore")
PROC="EverQuest2.exe"; OUT=r"C:\ib\copies.json"
pm=pymem.Pymem(PROC)
m=pymem.process.module_from_name(pm.process_handle,PROC)
base=m.lpBaseOfDll; size=m.SizeOfImage; mod_end=base+size
ax=pm.read_float(base+0x1822b68); ay=pm.read_float(base+0x1822b68+4); az=pm.read_float(base+0x1822b68+8)
class MBI(ctypes.Structure):
    _fields_=[("BaseAddress",ctypes.c_ulonglong),("AllocationBase",ctypes.c_ulonglong),
              ("AllocationProtect",w.DWORD),("__a1",w.DWORD),("RegionSize",ctypes.c_ulonglong),
              ("State",w.DWORD),("Protect",w.DWORD),("Type",w.DWORD),("__a2",w.DWORD)]
VQ=ctypes.windll.kernel32.VirtualQueryEx; VQ.restype=ctypes.c_size_t
h=pm.process_handle; READABLE={0x02,0x04,0x20,0x40,0x80}
def regions():
    addr=0; mbi=MBI()
    while addr<0x7fffffffffff:
        if not VQ(h,ctypes.c_void_p(addr),ctypes.byref(mbi),ctypes.sizeof(mbi)): break
        sz=mbi.RegionSize
        if mbi.State==0x1000 and (mbi.Protect&0xff) in READABLE and 0<sz<=256*1024*1024:
            yield mbi.BaseAddress,sz,mbi.Type
        addr=mbi.BaseAddress+sz if sz else addr+0x1000
tol=0.03; heap=[]; modmatch=[]; names=0
nb16="Furyflatulence".encode("utf-16le")
for b,sz,typ in regions():
    try: buf=pm.read_bytes(b,sz)
    except Exception: continue
    names+=buf.count(nb16)
    arr=np.frombuffer(buf[:(len(buf)//4)*4],dtype="<f4")
    if len(arr)<3: continue
    mask=(np.abs(arr[:-2]-ax)<tol)&(np.abs(arr[1:-1]-ay)<tol)&(np.abs(arr[2:]-az)<tol)
    for i in np.where(mask)[0]:
        a=b+int(i)*4
        if base<=a<mod_end: modmatch.append(hex(a-base))
        else: heap.append({"addr":hex(a),"type":hex(typ)})
res={"pos":[round(ax,2),round(ay,2),round(az,2)],"name_strings":names,
     "module_offsets":modmatch,"heap_count":len(heap),"heap":[x["addr"] for x in heap]}
open(OUT,"w").write(json.dumps(res,indent=1))
print(json.dumps({"heap_count":len(heap),"module":len(modmatch),"names":names}))
