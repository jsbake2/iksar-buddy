"""Fire an AHK v2 script (from stdin) in the guest via the ibrun scheduled task."""
import sys

from shared.guest import Guest

print(Guest("iksar_buddy").run_ahk(sys.stdin.read()))
