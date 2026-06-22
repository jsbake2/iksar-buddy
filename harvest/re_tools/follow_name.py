"""BFS an object's pointer graph (depth<=3) to find where its display name lives.
Reports the offset PATH from the actor to a string field. Reusable for all spawns.
Usage: python follow_name.py <obj_hex> [needle1 needle2 ...]"""
import pymem, pymem.process, struct, sys, json
pm=pymem.Pymem("EverQuest2.exe")
base=pymem.process.module_from_name(pm.process_handle,"EverQuest2.exe").lpBaseOfDll
mod_end=base+0x1c00000
root=int(sys.argv[1],16)
needles=[s.encode() for s in (sys.argv[2:] or ["Shrubbery","Furyflatulence"])]
def rd(a,n):
    try: return pm.read_bytes(a,n)
    except Exception: return b""
def is_str_at(a):
    b=rd(a,48)
    e=b.find(b"\x00")
    t=b[:e if e>0 else 0]
    if len(t)>=3 and all(32<=c<127 for c in t): return t
    return None
found=[]
seen=set()
# BFS: each node is (addr, path_offsets)
from collections import deque
dq=deque([(root,[])])
while dq and len(found)<40:
    a,path=dq.popleft()
    if a in seen or len(path)>3: continue
    seen.add(a)
    blob=rd(a,0x240)
    if not blob: continue
    for o in range(0,len(blob)-8,8):
        v=struct.unpack_from("<Q",blob,o)[0]
        if not (0x10000<v<0x7fffffffffff): continue
        # is v a pointer to a string?
        t=is_str_at(v)
        if t:
            for nd in needles:
                if nd in t:
                    found.append({"name":t.decode("latin-1"),"path":[hex(p) for p in path]+["+0x%x->str"%o]})
            continue
        if len(path)<3 and v not in seen and base>v:  # heap only, recurse
            dq.append((v,path+[o]))
out={"root":hex(root),"hits":found[:25]}
print(json.dumps(out,indent=1))
