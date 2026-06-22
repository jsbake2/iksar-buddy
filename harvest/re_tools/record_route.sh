#!/bin/bash
# Auto-record waypoints every 5s until killed. Writes /tmp/route_marks.json.
echo "[]" > /tmp/route_marks.json
while true; do
  P=$(ssh 10.0.0.16 'python3 /tmp/ibhwbp_push.py run "C:\\ib\\py\\python.exe" "C:\\ib\\nav_cal.py"' 2>/dev/null)
  python3 -c "
import json,os,sys
try: p=json.loads('''$P''')
except: sys.exit(0)
m=json.load(open('/tmp/route_marks.json'))
m.append([round(p['x'],1),round(p['z'],1)])
json.dump(m,open('/tmp/route_marks.json','w'))
print('wp %d: (%.1f, %.1f)'%(len(m),p['x'],p['z']),flush=True)
" 2>/dev/null
  sleep 5
done
