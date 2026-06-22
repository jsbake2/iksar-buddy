"""Enumerate all objects of a given vtable with a real world position at a given offset.
Usage: python enum_vt.py <vtable_modoff_hex> <pos_off_hex>"""
import pymem, pymem.process, ctypes, struct, json, sys, math
import ctypes.wintypes as w
pm=pymem.Pymem("EverQuest2.exe"); m=pymem.process.module_from_name(pm.process_handle,"EverQuest2.exe")
base=m.lpBaseOfDll
VT=base+int(sys.argv[1],16); POFF=int(sys.argv[2],16); patt=struct.pack("<Q",VT)
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
sp=[];seen=set()
for b,sz in regions():
    try: buf=pm.read_bytes(b,sz)
    except: continue
    i=buf.find(patt)
    while i!=-1:
        oa=b+i
        if oa not in seen and i+POFF+12<=len(buf):
            seen.add(oa)
            x,y,z=struct.unpack_from("<fff",buf,i+POFF)
            if all(map(math.isfinite,(x,y,z))) and abs(x)>5 and abs(z)>5 and abs(x)<1e5 and abs(z)<1e5 and abs(y)<1e4:
                sp.append({"addr":hex(oa),"xyz":[round(x,1),round(y,1),round(z,1)],"dist":round(math.hypot(x-px,z-pz),1)})
        i=buf.find(patt,i+1)
sp=sorted(sp,key=lambda s:s["dist"])
print(json.dumps({"vtable":sys.argv[1],"pos_off":sys.argv[2],"count":len(sp),
                  "player":[round(px,1),round(pz,1)],"nearest":sp[:20]},indent=1))
