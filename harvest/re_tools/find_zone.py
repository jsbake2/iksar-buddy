"""Read-only: find a module-static pointer to the current zone-name string so the dashboard
header can show it. EQ2 map shows 'The Thundering Steppes'."""
import pymem, pymem.process, ctypes, struct, json
import ctypes.wintypes as w
pm=pymem.Pymem("EverQuest2.exe"); m=pymem.process.module_from_name(pm.process_handle,"EverQuest2.exe")
base=m.lpBaseOfDll; size=m.SizeOfImage; mod_end=base+size
class MBI(ctypes.Structure):
    _fields_=[("BaseAddress",ctypes.c_ulonglong),("AllocationBase",ctypes.c_ulonglong),
              ("AllocationProtect",w.DWORD),("__a1",w.DWORD),("RegionSize",ctypes.c_ulonglong),
              ("State",w.DWORD),("Protect",w.DWORD),("Type",w.DWORD),("__a2",w.DWORD)]
VQ=ctypes.windll.kernel32.VirtualQueryEx; VQ.restype=ctypes.c_size_t; h=pm.process_handle
def regions(prot=(0x04,0x02)):
    addr=0;mbi=MBI()
    while addr<0x7fffffffffff:
        if not VQ(h,ctypes.c_void_p(addr),ctypes.byref(mbi),ctypes.sizeof(mbi)): break
        sz=mbi.RegionSize
        if mbi.State==0x1000 and (mbi.Protect&0xff) in prot and 0<sz<=256*1024*1024: yield mbi.BaseAddress,sz
        addr=mbi.BaseAddress+sz if sz else addr+0x1000
# 1) find the zone display string
needle=b"Thundering Steppes"
straddrs=[]
for b,sz in regions():
    try: buf=pm.read_bytes(b,sz)
    except: continue
    i=buf.find(needle)
    while i!=-1:
        s=i
        while s>0 and 32<=buf[s-1]<127: s-=1
        straddrs.append(b+s); i=buf.find(needle,i+1)
straddrs=sorted(set(straddrs))
# 2) find MODULE-STATIC pointers to any of those strings (stable across the session)
import numpy as np
sa=np.array(straddrs,dtype="<u8") if straddrs else np.array([],dtype="<u8")
statics=[]
addr=base;mbi=MBI()
while addr<mod_end:
    if not VQ(h,ctypes.c_void_p(addr),ctypes.byref(mbi),ctypes.sizeof(mbi)): break
    sz=mbi.RegionSize
    if mbi.State==0x1000 and (mbi.Protect&0xff)==0x04 and len(sa):
        try: buf=pm.read_bytes(mbi.BaseAddress,sz)
        except: buf=b""
        q=np.frombuffer(buf[:(len(buf)//8)*8],dtype="<u8")
        for hi in np.where(np.isin(q,sa))[0]:
            statics.append(hex(mbi.BaseAddress+int(hi)*8-base))
    addr=mbi.BaseAddress+sz if sz else addr+0x1000
def deref_name(off):
    try:
        p=struct.unpack("<Q",pm.read_bytes(base+int(off,16),8))[0]
        b=pm.read_bytes(p,48); e=b.find(b"\x00"); return b[:e].decode("latin-1")
    except: return None
print(json.dumps({"n_strings":len(straddrs),"static_ptrs":statics[:10],
                  "sample_deref":[{"off":o,"name":deref_name(o)} for o in statics[:5]]},indent=1))
