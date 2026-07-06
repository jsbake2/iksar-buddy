r"""Agent file I/O plumbing: control/status file paths, the STOP flag, and the
never-raise status/debug writers (REFACTOR P3.2 — split out of harvest_agent.py).

Deployed flat to C:\ib\agent\ next to the other agent modules.
"""
from __future__ import annotations
import os, time

TARGET = r"C:\ib\nav_target.json"
STATUS = r"C:\ib\nav_status.json"
HUD = r"C:\ib\hud.json"            # clean, uncontended status mirror for the on-screen overlay
STOP_FLAG = r"C:\ib\STOP"          # touch this file to halt the bot near-instantly
_DBG = r"C:\ib\gdbg.log"


class StopRequested(Exception):
    pass


def _check_stop():
    if os.path.exists(STOP_FLAG):
        raise StopRequested()


def _dbg(m):
    try:
        with open(_DBG, "a") as f:
            f.write(f"{time.time():.1f} {m}\n")
    except Exception:
        pass


def _status(s):
    """Write nav_status atomically and NEVER raise. The status file is for the dashboard UI only —
    if a reader (dashboard/sensor) momentarily holds it open (sharing violation -> PermissionError),
    that must NOT crash the gather. Best-effort: write a temp then atomic-replace, swallow errors."""
    try:
        tmp = STATUS + ".tmp"
        with open(tmp, "w") as f:
            f.write(s)
        os.replace(tmp, STATUS)
    except Exception:
        pass
    try:                                  # HUD mirror: only the overlay reads this, so no lock
        tmp = HUD + ".tmp"
        with open(tmp, "w") as f:
            f.write(s)
        os.replace(tmp, HUD)
    except Exception:
        pass
