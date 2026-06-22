"""Snapshot every object within R metres of the player: objbase, vtable, position.
Pre/post-harvest diff (player MUST stay still) isolates the despawned node's class+object.
Usage: python snapshot_objs.py <tag> [radius]"""
import pymem, pymem.process, ctypes, struct, json, sys, math
import ctypes.wintypes as w
import numpy as np
np.seterr(all="ignore")
pm=pymem.Pymem("EverQuest2.exe"); m=pymem.process.module_from_name(pm.process_handle,"EverQuest2.exe")
base=m.lpBaseOfDll; mod_end=base+m.SizeOfImage
TAG=sys.argv[1] if len(sys.argv)>1 else "pre"; R=float(sys.argv[2]) if len(sys.argv)>2 else 8.0
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
objs={}
for b,sz in regions():
    try: buf=pm.read_bytes(b,sz)
    except: continue
    arr=np.frombuffer(buf[:(len(buf)//4)*4],dtype="<f4")
    if len(arr)<3: continue
    mask=(np.abs(arr[:-2]-px)<R)&(np.abs(arr[2:]-pz)<R)&(np.abs(arr[1:-1]-py)<8)&((np.abs(arr[:-2])>1)|(np.abs(arr[2:])>1))
    for i in np.where(mask)[0]:
        po=int(i)*4
        # backtrack <=0x300 for vtable -> object base
        ob=None;vt=None;lo=max(0,po-0x300)
        for q in range(po-8,lo,-8):
            if q+8>len(buf):continue
            v=struct.unpack_from("<Q",buf,q)[0]
            if base<=v<mod_end: ob=b+q;vt=v;break
        if ob and ob not in objs:
            x,y,z=float(arr[i]),float(arr[i+1]),float(arr[i+2])
            objs[ob]={"obj":hex(ob),"vtable":hex(vt-base),"pos_off":hex((b+po)-ob),
                      "xyz":[round(x,2),round(y,2),round(z,2)],
                      "dist":round(math.hypot(x-px,z-pz),2)}
out={"tag":TAG,"player":[round(px,2),round(py,2),round(pz,2)],"n":len(objs),
     "objs":sorted(objs.values(),key=lambda o:o["dist"])}
open(r"C:\ib\snap_%s.json"%TAG,"w").write(json.dumps(out))
print(json.dumps({"tag":TAG,"player":out["player"],"n":len(objs)}))
