"""Compatibility shim — Guest was promoted to shared/guest.py (REFACTOR P0.1).

Import from shared.guest going forward; this re-export lives for one release so
older tools/scripts keep working.
"""
from shared.guest import VIRSH, Guest  # noqa: F401
