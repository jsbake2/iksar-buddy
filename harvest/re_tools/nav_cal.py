"""Read pos+heading, return JSON. Used to calibrate heading->world-direction."""
import pymem,pymem.process,json
pm=pymem.Pymem("EverQuest2.exe")
b=pymem.process.module_from_name(pm.process_handle,"EverQuest2.exe").lpBaseOfDll
x=pm.read_float(b+0x1822b68);y=pm.read_float(b+0x1822b68+4);z=pm.read_float(b+0x1822b68+8)
h=pm.read_float(b+0x1822b74)%360
print(json.dumps({"x":round(x,2),"y":round(y,2),"z":round(z,2),"hdg":round(h,1)}))
