import pymem,pymem.process,sys,struct
pm=pymem.Pymem("EverQuest2.exe")
base=pymem.process.module_from_name(pm.process_handle,"EverQuest2.exe").lpBaseOfDll
mod_end=base+0x1c00000
for aS in sys.argv[1:]:
    a=int(aS,16)
    try: b=pm.read_bytes(a,0x200)
    except Exception as e:
        print(aS,"READ FAIL",e); continue
    print("=== object %s ==="%aS)
    for o in range(0,0x200,8):
        v=struct.unpack_from("<Q",b,o)[0]
        tag=""
        if base<=v<mod_end: tag="MOD+0x%x"%(v-base)
        elif 0x10000<v<0x7fffffffffff:
            # try string deref
            try:
                s=pm.read_bytes(v,24); e=s.find(b"\x00")
                txt=s[:e if e>0 else 24]
                if txt and all(32<=c<127 for c in txt) and len(txt)>=2: tag="-> '%s'"%txt.decode()
                else: tag="heap"
            except Exception: tag="heap"
        fx,fy=struct.unpack_from("<ff",b,o)
        ftag=("  f=%.2f,%.2f"%(fx,fy)) if (abs(fx)>0.01 and abs(fx)<1e6) else ""
        print("  +0x%03x %016x  %-22s%s"%(o,v,tag,ftag))
