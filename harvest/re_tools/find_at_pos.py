"""Find every object holding an EXACT world position (x,y,z), backtrack to its vtable.
Used to isolate a stationary node after the player walks away.
Usage: python find_at_pos.py X Y Z"""
import pymem, pymem.process, ctypes, struct, json, sys
import ctypes.wintypes as w
import numpy as np
np.seterr(all="ignore")
pm=pymem.Pymem("EverQuest2.exe"); m=pymem.process.module_from_name(pm.process_handle,"EverQuest2.exe")
base=m.lpBaseOfDll; mod_end=base+m.SizeOfImage
X,Y,Z=float(sys.argv[1]),float(sys.argv[2]),float(sys.argv[3])
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
res=[]
for b,sz in regions():
    try: buf=pm.read_bytes(b,sz)
    except Exception: continue
    arr=np.frombuffer(buf[:(len(buf)//4)*4],dtype="<f4")
    if len(arr)<3: continue
    mask=(np.abs(arr[:-2]-X)<0.05)&(np.abs(arr[1:-1]-Y)<0.05)&(np.abs(arr[2:]-Z)<0.05)
    for i in np.where(mask)[0]:
        pa=b+int(i)*4
        # backtrack <=0x300 for vtable
        ob=None;vt=None
        lo=max(0,int(i)*4-0x300)
        for q in range(int(i)*4-8,lo,-8):
            if q+8>len(buf):continue
            v=struct.unpack_from("<Q",buf,q)[0]
            if base<=v<mod_end: ob=b+q;vt=v;break
        res.append({"pos_addr":hex(pa),"obj":hex(ob) if ob else None,
                    "vtable":hex(vt-base) if vt else None,"pos_off":hex(pa-ob) if ob else None})
from collections import Counter
vtc=Counter(r["vtable"] for r in res if r["vtable"])
open(r"C:\ib\atpos.json","w").write(json.dumps({"target":[X,Y,Z],"n":len(res),
    "vtables":vtc.most_common(20),"objs":res[:60]},indent=1))
print(json.dumps({"n":len(res),"vtables":vtc.most_common(12)}))
