"""Find the player's Spawn object: among the scene candidates (objects holding the
player position), the real Spawn also points at the 'Furyflatulence' name string.
Its vtable = the Spawn class -> scan for the array of pointers to that vtable = spawn list."""
import pymem, pymem.process, json, struct
PROC="EverQuest2.exe"
pm=pymem.Pymem(PROC)
m=pymem.process.module_from_name(pm.process_handle,PROC)
base=m.lpBaseOfDll; mod_end=base+m.SizeOfImage
scene=json.load(open(r"C:\ib\scene.json"))
cands=scene["candidates"]
nb16="Furyflatulence".encode("utf-16le"); nb8=b"Furyflatulence"
def looks_ptr(v): return 0x10000<v<0x7fffffffffff
def reads(a,n):
    try: return pm.read_bytes(a,n)
    except Exception: return b""
hits=[]
seen=set()
for c in cands:
    ob=c["objbase"]
    if not ob or ob in seen: continue
    seen.add(ob)
    oa=int(ob,16)
    obj=reads(oa,0x400)
    if not obj: continue
    for q in range(0,len(obj)-8,8):
        v=struct.unpack_from("<Q",obj,q)[0]
        if not looks_ptr(v): continue
        s=reads(v,30)
        if s.startswith(nb16) or s.startswith(nb8):
            hits.append({"objbase":ob,"vtable":c["vtable"],"pos_off":c["pos_off"],
                         "name_ptr_off":hex(q),"name_at":hex(v),
                         "first0x80":obj[:0x80].hex()})
            break
out={"player_pos":scene["player"],"spawn_hits":hits,"n_hits":len(hits)}
open(r"C:\ib\spawn.json","w").write(json.dumps(out,indent=1))
print(json.dumps({"n_hits":len(hits),
                  "vtables":[h["vtable"] for h in hits],
                  "name_offs":[h["name_ptr_off"] for h in hits]}))
