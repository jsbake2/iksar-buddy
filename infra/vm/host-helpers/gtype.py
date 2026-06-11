import sys, subprocess, time
DOM="iksar_buddy"
SH="KEY_LEFTSHIFT"
base={}
for c in "abcdefghijklmnopqrstuvwxyz": base[c]=("KEY_"+c.upper(),False)
for d in "0123456789": base[d]=("KEY_"+d,False)
spec={
 ' ':("KEY_SPACE",False),'\n':("KEY_ENTER",False),'\t':("KEY_TAB",False),
 '.':("KEY_DOT",False),',':("KEY_COMMA",False),'/':("KEY_SLASH",False),
 '\\':("KEY_BACKSLASH",False),'-':("KEY_MINUS",False),'_':("KEY_MINUS",True),
 ';':("KEY_SEMICOLON",False),':':("KEY_SEMICOLON",True),
 "'":("KEY_APOSTROPHE",False),'"':("KEY_APOSTROPHE",True),
 '(':("KEY_9",True),')':("KEY_0",True),'[':("KEY_LEFTBRACE",False),']':("KEY_RIGHTBRACE",False),
 '{':("KEY_LEFTBRACE",True),'}':("KEY_RIGHTBRACE",True),'=':("KEY_EQUAL",False),'+':("KEY_EQUAL",True),
 '|':("KEY_BACKSLASH",True),'*':("KEY_8",True),'&':("KEY_7",True),'^':("KEY_6",True),
 '%':("KEY_5",True),'$':("KEY_4",True),'#':("KEY_3",True),'@':("KEY_2",True),'!':("KEY_1",True),
 '~':("KEY_GRAVE",True),'`':("KEY_GRAVE",False),'<':("KEY_COMMA",True),'>':("KEY_DOT",True),'?':("KEY_SLASH",True),
}
def emit(keys):
    subprocess.run(["sudo","-n","virsh","-c","qemu:///system","send-key",DOM,"--codeset","linux"]+keys,
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
s=sys.argv[1]
for ch in s:
    if ch in base: k,sh=base[ch]
    elif ch.isupper(): k,sh=("KEY_"+ch,True)
    elif ch in spec: k,sh=spec[ch]
    else: continue
    emit([SH,k] if sh else [k])
    time.sleep(0.04)
if len(sys.argv)>2 and sys.argv[2]=="enter":
    emit(["KEY_ENTER"])
