"""Find the 'current target' pointer by diffing memory across a target change.
  snap <file>  : record every heap-pointer qword in the MODULE's writable data, by offset.
  diff <a> <b> : report module offsets whose pointer changed (pre -> post) = target-ptr candidates.
Run in-guest. Module-static globals (like the current-target ptr) live in the module RW data."""
import pymem, pymem.process, ctypes, struct, sys, json
import ctypes.wintypes as w
pm=pymem.Pymem("EverQuest2.exe"); m=pymem.process.module_from_name(pm.process_handle,"EverQuest2.exe")
base=m.lpBaseOfDll; size=m.SizeOfImage; mod_end=base+size
class MBI(ctypes.Structure):
    _fields_=[("BaseAddress",ctypes.c_ulonglong),("AllocationBase",ctypes.c_ulonglong),
              ("AllocationProtect",w.DWORD),("__a1",w.DWORD),("RegionSize",ctypes.c_ulonglong),
              ("State",w.DWORD),("Protect",w.DWORD),("Type",w.DWORD),("__a2",w.DWORD)]
VQ=ctypes.windll.kernel32.VirtualQueryEx; VQ.restype=ctypes.c_size_t; h=pm.process_handle
def is_heap(v): return 0x10000000000 < v < 0x7ff000000000 and not (base<=v<mod_end)
def snap():
    out={}
    addr=base; mbi=MBI()
    while addr < mod_end:
        if not VQ(h,ctypes.c_void_p(addr),ctypes.byref(mbi),ctypes.sizeof(mbi)): break
        sz=mbi.RegionSize
        if mbi.State==0x1000 and (mbi.Protect&0xff)==0x04:   # module RW data
            try: buf=pm.read_bytes(mbi.BaseAddress,sz)
            except: buf=b""
            for o in range(0,len(buf)-8,8):
                v=struct.unpack_from("<Q",buf,o)[0]
                if is_heap(v): out[mbi.BaseAddress+o-base]=v   # key by module offset
        addr=mbi.BaseAddress+sz if sz else addr+0x1000
    return out
if sys.argv[1]=="snap":
    json.dump(snap(),open(sys.argv[2],"w"))
    print("snap",sys.argv[2],"ptrs")
elif sys.argv[1]=="diff":
    a=json.load(open(sys.argv[2])); b=json.load(open(sys.argv[3]))
    ch=[]
    for off,bv in b.items():
        av=a.get(off)
        if av!=bv and is_heap(bv):
            ch.append({"off":hex(int(off)),"pre":hex(av) if av else None,"post":hex(bv)})
    print(json.dumps({"changed":ch[:60],"n":len(ch)},indent=1))
