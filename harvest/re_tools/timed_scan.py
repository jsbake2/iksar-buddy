import pymem, pymem.process, ctypes, struct, time, math
import ctypes.wintypes as w
pm=pymem.Pymem("EverQuest2.exe"); m=pymem.process.module_from_name(pm.process_handle,"EverQuest2.exe")
base=m.lpBaseOfDll; VT=base+0x1782848; patt=struct.pack("<Q",VT)
px=pm.read_float(base+0x1822b68); pz=pm.read_float(base+0x1822b68+8)
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
def scan(restrict):
    t0=time.time(); nbytes=0; found=0
    for b,sz in regions():
        if restrict and not (0x1a700000000<=b<0x1a800000000): continue
        try: buf=pm.read_bytes(b,sz)
        except: continue
        nbytes+=sz
        i=buf.find(patt)
        while i!=-1:
            found+=1; i=buf.find(patt,i+1)
    return time.time()-t0, nbytes, found
for r in (True,False):
    dt,nb,fnd=scan(r)
    print("restrict=%s  %.2fs  %d MB  %d vtable-hits"%(r,dt,nb//(1<<20),fnd))
