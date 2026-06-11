#!/usr/bin/env python3
"""Self-locating group-invite accept. OCR the screen, confirm it's a group
invite, find the 'Accept' word box, click its center. Gated: only clicks if
'invited' + 'group' are present, so it never clicks a stray Accept."""
import csv, io, os, re, subprocess
DOM="iksar_buddy"; PPM="/tmp/iv.ppm"; PNG="/tmp/iv.png"; OCRP="/tmp/iv_o.png"
def sh(*a): return subprocess.run(list(a),capture_output=True,text=True)
sh("sudo","-n","virsh","-c","qemu:///system","screenshot",DOM,PPM)
sh("magick",PPM,PNG)
sh("magick",PNG,"-colorspace","Gray","-threshold","55%","-negate",OCRP)
r=sh("tesseract",OCRP,"stdout","--psm","11","tsv")
words=[]
for row in csv.DictReader(io.StringIO(r.stdout),delimiter="\t"):
    try:
        if float(row["conf"])>30 and len(row["text"].strip())>1:
            words.append((row["text"].strip(),int(row["left"]),int(row["top"]),int(row["width"]),int(row["height"])))
    except (ValueError,KeyError): pass
allt=" ".join(w[0] for w in words).lower()
gate = "invited" in allt and "group" in allt
acc=[w for w in words if re.fullmatch(r"Accept",w[0],re.I)]
print("gate(invite+group):",gate)
print("accept words:",[(w[0],w[1],w[2]) for w in acc])
if gate and acc:
    w=acc[0]; cx=w[1]+w[3]//2; cy=w[2]+w[4]//2
    sh("python3",os.path.expanduser("~/ib-build/gexec.py"),
       f"Set-Content C:\\ib\\click.txt '{cx} {cy}' -NoNewline; Start-ScheduledTask -TaskName ibgclick")
    print(f"CLICKED Accept @ {cx},{cy}")
else:
    print("no action (gate or accept not found)")
