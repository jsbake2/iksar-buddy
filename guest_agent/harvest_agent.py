r"""In-guest harvest agent — thin entrypoint (REFACTOR P3.2).

The old 1470-line monolith is now six modules deployed alongside this file to
C:\ib\agent\ (all code moved verbatim):

  agentio       control/status file paths, STOP flag, _status/_dbg writers
  win_input     user32 input: focus, keybd_event taps, chat/login typists, Keys
  eq2mem        pymem plumbing: _live_eq2, pos/heading state, node+actor scans
  nav           waypoint-graph navigation: nav/goto/_settle/unstuck
  harvest_loops log-driven harvest() + the loop/route/gather work loops
  diag          RE diagnostics (--diag/--diag2/--dump memory scans)

This file just dispatches C:\ib\nav_target.json to the right verb, exactly as
before — the deployer/host contract (nav_target/nav_status/STOP) is unchanged.

Run it inside the Windows guest:  python C:\ib\agent\harvest_agent.py
Fire-and-forget from the host via QEMU guest-exec; progress/results land in
C:\ib\nav_status.json and the gdbg.log/crash.log debug files.
"""
from __future__ import annotations
import json, os, time
from pathlib import Path

try:
    from agentio import STOP_FLAG, TARGET, StopRequested, _status
    from win_input import Keys, _eq2_window_any, _tap, _u, focus_eq2, type_chat, type_login_form
    from eq2mem import _live_eq2
    from nav import nav
    from harvest_loops import gather_loop_main, harvest, loop_main, route_loop_main
    from diag import diag_dump_main, diag_scan_main, diag_wide_main
except ImportError:
    from guest_agent.agentio import STOP_FLAG, TARGET, StopRequested, _status
    from guest_agent.win_input import (Keys, _eq2_window_any, _tap, _u, focus_eq2,
                                       type_chat, type_login_form)
    from guest_agent.eq2mem import _live_eq2
    from guest_agent.nav import nav
    from guest_agent.harvest_loops import (gather_loop_main, harvest, loop_main,
                                           route_loop_main)
    from guest_agent.diag import diag_dump_main, diag_scan_main, diag_wide_main


def main():
    keys = Keys()
    try:                                       # clear any stale STOP flag from a prior run
        os.remove(STOP_FLAG)
    except OSError:
        pass
    try:
        tgt = json.loads(Path(TARGET).read_text())
    except Exception as e:
        _status(json.dumps({"ok": False, "err": f"target: {e}"})); return
    try:
        if tgt.get("chat"):
            hwnd = _eq2_window_any()
            if not hwnd:
                _status(json.dumps({"ok": False, "err": "no EQ2 window"})); return
            type_chat(hwnd, str(tgt["chat"]))
            _status(json.dumps({"ok": True, "chat": tgt["chat"], "ts": time.time()}))
        elif tgt.get("login_form"):
            p = tgt["login_form"]                    # {user, password, character, world, fields, submit}
            hwnd = _eq2_window_any()
            if not hwnd:
                _status(json.dumps({"ok": False, "err": "no EQ2 window"})); return
            type_login_form(hwnd, p["user"], p["password"], p["character"], p.get("world", "Wuoshi"),
                            user_click=(p.get("fields") or {}).get("user"),
                            submit=bool(p.get("submit", True)))
            _status(json.dumps({"ok": True, "typed": p["character"], "ts": time.time()}))
        elif tgt.get("submit_enter"):
            hwnd = _eq2_window_any()
            if hwnd:
                focus_eq2(hwnd); time.sleep(0.3); _tap(0x0D)
            _status(json.dumps({"ok": True, "submit": True, "ts": time.time()}))
        elif tgt.get("form_type") is not None:
            # TEST: focus the EQ2 login form and type into the USERNAME field via keybd_event
            # (the proven in-world input path). Default focus = password; Shift+Tab -> username.
            hwnd = _eq2_window_any()
            if not hwnd:
                _status(json.dumps({"ok": False, "err": "no EQ2 window"})); return
            focus_eq2(hwnd); time.sleep(0.4)
            _tap(0x09, shift=True); time.sleep(0.3)     # Shift+Tab: password -> username
            _tap(0x23); time.sleep(0.1)                  # End
            for _ in range(40):
                _tap(0x08)                               # BackSpace x40 (clear)
            time.sleep(0.2)
            for ch in str(tgt["form_type"]):
                res = _u.VkKeyScanW(ord(ch))
                if res != -1:
                    _tap(res & 0xFF, bool((res >> 8) & 1)); time.sleep(0.03)
            _status(json.dumps({"ok": True, "form_type": tgt["form_type"], "ts": time.time()}))
        elif tgt.get("diag"):
            diag_scan_main()
            _status(json.dumps({"ok": True, "diag": True, "ts": time.time()}))
        elif tgt.get("diag2"):
            diag_wide_main(float(tgt.get("rad", 10.0)))
            _status(json.dumps({"ok": True, "diag2": True, "ts": time.time()}))
        elif tgt.get("dump"):
            diag_dump_main()
            _status(json.dumps({"ok": True, "dump": True, "ts": time.time()}))
        elif tgt.get("gather_loop"):
            gather_loop_main(keys, int(tgt.get("laps", 1)))
        elif tgt.get("route_loop"):
            route_loop_main(keys, int(tgt.get("laps", 1)))
        elif tgt.get("loop"):
            loop_main(keys, int(tgt.get("max_nodes", 5)))
        else:
            tx, tz = float(tgt["tx"]), float(tgt["tz"])
            do_harvest = bool(tgt.get("harvest", True))
            hwnd, pid, pm, base = _live_eq2()
            if not hwnd:
                _status(json.dumps({"ok": False, "err": "live EQ2 not found"})); return
            _u.ShowWindow(hwnd, 3); _u.SetForegroundWindow(hwnd); time.sleep(0.3)
            ok, d, _ = nav(pm, base, hwnd, tx, tz, keys); keys.release_all()
            out = {"ok": ok, "dist": round(d, 2), "pid": pid, "ts": time.time()}
            if ok and do_harvest:
                out["harvest"] = harvest(hwnd)
            _status(json.dumps(out))
    except StopRequested:
        keys.release_all()
        _status(json.dumps({"stopped": True, "ts": time.time()}))
    except Exception as e:
        import traceback
        keys.release_all()
        _status(json.dumps({"ok": False, "err": str(e),
                                            "tb": traceback.format_exc()[-400:], "ts": time.time()}))
    finally:
        keys.release_all()


if __name__ == "__main__":
    import faulthandler
    try:
        _cf = open(r"C:\ib\crash.log", "w")
        faulthandler.enable(file=_cf)            # native crash (segfault) -> crash.log
    except Exception:
        _cf = None
    try:
        main()
    except BaseException:
        import traceback
        try:
            with open(r"C:\ib\crash.log", "a") as f:
                f.write("PYEXC:\n" + traceback.format_exc())
        except Exception:
            pass
        raise
