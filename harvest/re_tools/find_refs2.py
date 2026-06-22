"""Find pointers landing ANYWHERE inside a known actor object [A, A+0x200), report the
interface offset used and whether neighbors point into OTHER actor objects (=spawn array).
Usage: python find_refs2.py <actor_hex>"""
import pymem, pymem.process, ctypes, struct, json, sys
import ctypes.wintypes as w
import numpy as np
np.seterr(all="ignore")
pm=pymem.Pymem("EverQuest2.exe"); m=pymem.process.module_from_name(pm.process_handle,"EverQuest2.exe")
base=m.lpBaseOfDll; SPAWN_VT=base+0x1782848
A=int(sys.argv[1],16); LO=A; HI=A+0x200
class MBI(ctypes.Structure):
    _fields_=[("BaseAddress",ctypes.c_ulonglong),("AllocationBase",ctypes.c_ulonglong),
              ("AllocationProtect",w.DWORD),("__a1",w.DWORD),("RegionSize",ctypes.c_ulonglong),
              ("State",w.DWORD),("Protect",w.DWORD),("Type",w.DWORD),("__a2",w.DWORD)]
VQ=ctypes.windll.kernel32.VirtualQueryEx; VQ.restype=ctypes.c_size_t; h=pm.process_handle
def regions(prot):
    addr=0; mbi=MBI()
    while addr<0x7fffffffffff:
        if not VQ(h,ctypes.c_void_p(addr),ctypes.byref(mbi),ctypes.sizeof(mbi)): break
        sz=mbi.RegionSize
        if mbi.State==0x1000 and (mbi.Protect&0xff) in prot and 0<sz<=256*1024*1024: yield mbi.BaseAddress,sz
        addr=mbi.BaseAddress+sz if sz else addr+0x1000
def vt_at(p):
    try: return struct.unpack("<Q",pm.read_bytes(p,8))[0]
    except: return 0
hits=[]
for b,sz in regions((0x04,)):
    try: buf=pm.read_bytes(b,sz)
    except: continue
    q=np.frombuffer(buf[:(len(buf)//8)*8],dtype="<u8")
    if not len(q): continue
    idx=np.where((q>=LO)&(q<HI))[0]
    for ii in idx:
        a=b+int(ii)*8; val=int(q[ii]); ifoff=val-A
        # neighbor check: how many of +/-16 slots point at an object whose vtable==SPAWN_VT (allowing same ifoff)
        nb=0; samples=[]
        for k in range(-16,17):
            jj=int(ii)+k
            if k==0 or jj<0 or jj>=len(q): continue
            pv=int(q[jj])
            if 0x10000<pv<0x7fffffffffff and vt_at(pv-ifoff)==SPAWN_VT:
                nb+=1
                if len(samples)<4: samples.append(hex(pv-ifoff))
        if nb>=2:
            hits.append({"at":hex(a),"iface_off":hex(ifoff),"spawn_neighbors":nb,"sample_objs":samples})
hits.sort(key=lambda x:-x["spawn_neighbors"])
print(json.dumps({"actor":hex(A),"n_array_hits":len(hits),"top":hits[:12]},indent=1))
