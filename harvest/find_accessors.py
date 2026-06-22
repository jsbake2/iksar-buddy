r"""In-guest 'find what accesses' for the EQ2 client — headless, no Cheat Engine.

Replaces the fragile CE-GUI-over-SPICE dance. Sets a HARDWARE (debug-register)
watchpoint on the module-static player-position global ([EverQuest2.exe+POS_OFF]) and
records the CPU registers of every instruction that WRITES it. The writer copies the
actor's real position into the camera/global each frame, so its `this`/source register
(usually RCX) points at the object that OWNS the position component -> the doorway to the
spawn/actor list (see harvest-memory-re memory + HARVEST.md).

Runs INSIDE the VM via C:\ib\py\python.exe, invoked by the host through guest-exec.
Writes results JSON to C:\ib\accessors.json. Does NOT kill EQ2 on exit
(DebugSetProcessKillOnExit False), so a bug here can't take the game down.

Usage (in guest):  python.exe find_accessors.py [seconds] [rw]
  seconds: capture budget (default 12)
  rw:      'w' write-only (default), 'rw' read+write
"""
from __future__ import annotations
import ctypes, ctypes.wintypes as wt, json, sys, time, struct

POS_OFF = 0x1822b68
PROC = "EverQuest2.exe"
OUT = r"C:\ib\accessors.json"

k32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---- constants -------------------------------------------------------------
DEBUG_PROCESS = 0
DBG_CONTINUE = 0x00010002
DBG_EXCEPTION_NOT_HANDLED = 0x80010001
EXCEPTION_SINGLE_STEP = 0x80000004
EXCEPTION_BREAKPOINT = 0x80000003
CREATE_PROCESS_DEBUG_EVENT = 3
CREATE_THREAD_DEBUG_EVENT = 2
EXCEPTION_DEBUG_EVENT = 1
EXIT_PROCESS_DEBUG_EVENT = 5
THREAD_ALL = 0x1FFFFF
# CONTEXT flags (AMD64): need CONTROL|INTEGER|SEGMENTS|DEBUG, skip FLOATING_POINT so the
# XMM save area (and its 16-byte alignment fussiness) is never touched.
CONTEXT_AMD64 = 0x00100000
CTX_FLAGS = CONTEXT_AMD64 | 0x1 | 0x2 | 0x4 | 0x10   # CONTROL|INTEGER|SEGMENTS|DEBUG_REGS

# ---- CONTEXT (x64) ---------------------------------------------------------
class CONTEXT(ctypes.Structure):
    _fields_ = [
        ("P1Home", ctypes.c_uint64), ("P2Home", ctypes.c_uint64),
        ("P3Home", ctypes.c_uint64), ("P4Home", ctypes.c_uint64),
        ("P5Home", ctypes.c_uint64), ("P6Home", ctypes.c_uint64),
        ("ContextFlags", ctypes.c_uint32), ("MxCsr", ctypes.c_uint32),
        ("SegCs", ctypes.c_uint16), ("SegDs", ctypes.c_uint16),
        ("SegEs", ctypes.c_uint16), ("SegFs", ctypes.c_uint16),
        ("SegGs", ctypes.c_uint16), ("SegSs", ctypes.c_uint16),
        ("EFlags", ctypes.c_uint32),
        ("Dr0", ctypes.c_uint64), ("Dr1", ctypes.c_uint64),
        ("Dr2", ctypes.c_uint64), ("Dr3", ctypes.c_uint64),
        ("Dr6", ctypes.c_uint64), ("Dr7", ctypes.c_uint64),
        ("Rax", ctypes.c_uint64), ("Rcx", ctypes.c_uint64),
        ("Rdx", ctypes.c_uint64), ("Rbx", ctypes.c_uint64),
        ("Rsp", ctypes.c_uint64), ("Rbp", ctypes.c_uint64),
        ("Rsi", ctypes.c_uint64), ("Rdi", ctypes.c_uint64),
        ("R8", ctypes.c_uint64), ("R9", ctypes.c_uint64),
        ("R10", ctypes.c_uint64), ("R11", ctypes.c_uint64),
        ("R12", ctypes.c_uint64), ("R13", ctypes.c_uint64),
        ("R14", ctypes.c_uint64), ("R15", ctypes.c_uint64),
        ("Rip", ctypes.c_uint64),
        ("_tail", ctypes.c_byte * (1232 - 0x100)),   # FltSave + vector regs (unused)
    ]

GPR = ["Rax", "Rcx", "Rdx", "Rbx", "Rsp", "Rbp", "Rsi", "Rdi",
       "R8", "R9", "R10", "R11", "R12", "R13", "R14", "R15"]


def _aligned_ctx():
    """16-byte-aligned CONTEXT (API requirement)."""
    raw = (ctypes.c_byte * (ctypes.sizeof(CONTEXT) + 16))()
    addr = (ctypes.addressof(raw) + 15) & ~15
    ctx = ctypes.cast(addr, ctypes.POINTER(CONTEXT)).contents
    ctx._keep = raw            # prevent GC of the backing buffer
    return ctx


class DEBUG_EVENT(ctypes.Structure):
    _fields_ = [
        ("dwDebugEventCode", wt.DWORD),
        ("dwProcessId", wt.DWORD),
        ("dwThreadId", wt.DWORD),
        ("_pad", wt.DWORD),                 # union is 8-aligned -> 4 bytes padding
        ("u", ctypes.c_byte * 176),
    ]


def find_pid(name: str) -> int:
    import pymem, pymem.process
    pm = pymem.Pymem(name)
    return pm.process_id


_PM = None       # pymem handle kept open for reads while attached
_BASE = 0
_END = 0


def module_base(pid: int, name: str) -> int:
    global _PM, _BASE, _END
    import pymem, pymem.process
    _PM = pymem.Pymem(name)
    mod = pymem.process.module_from_name(_PM.process_handle, name)
    _BASE = mod.lpBaseOfDll
    _END = _BASE + mod.SizeOfImage
    return mod.lpBaseOfDll


def stack_returns(rsp: int, depth: int = 0x600):
    """Scan [rsp, rsp+depth] for 8-byte values that land in the module's code range —
    these are return addresses = the call chain above the accessing instruction."""
    out = []
    try:
        data = _PM.read_bytes(rsp, depth)
    except Exception:
        return out
    for i in range(0, len(data) - 8, 8):
        v = int.from_bytes(data[i:i + 8], "little")
        if _BASE <= v < _END:
            out.append({"sp_off": hex(i), "ret": hex(v - _BASE)})
    return out


def set_hw_bp(tid: int, addr: int, rw: int):
    """Arm DR0 watchpoint on one thread. rw: 1=write, 3=read/write. len=4 (encoding 0b11)."""
    h = k32.OpenThread(THREAD_ALL, False, tid)
    if not h:
        return False
    ctx = _aligned_ctx()
    ctx.ContextFlags = CTX_FLAGS
    if not k32.GetThreadContext(h, ctypes.byref(ctx)):
        k32.CloseHandle(h); return False
    ctx.Dr0 = addr
    # DR7: L0=1, RW0=rw<<16, LEN0=0b11<<18
    ctx.Dr7 = 1 | (rw << 16) | (0b11 << 18)
    ctx.Dr6 = 0
    ok = k32.SetThreadContext(h, ctypes.byref(ctx))
    k32.CloseHandle(h)
    return bool(ok)


def clear_hw_bp(tid: int):
    """Disarm DR0..DR3/DR7 on one thread. MUST run before detaching, else the leftover
    watchpoint fires #DB with no debugger attached and CRASHES the target."""
    h = k32.OpenThread(THREAD_ALL, False, tid)
    if not h:
        return
    k32.SuspendThread(h)
    ctx = _aligned_ctx(); ctx.ContextFlags = CTX_FLAGS
    if k32.GetThreadContext(h, ctypes.byref(ctx)):
        ctx.Dr0 = ctx.Dr1 = ctx.Dr2 = ctx.Dr3 = 0
        ctx.Dr7 = 0; ctx.Dr6 = 0
        k32.SetThreadContext(h, ctypes.byref(ctx))
    k32.ResumeThread(h)
    k32.CloseHandle(h)


def read_ctx(tid: int):
    h = k32.OpenThread(THREAD_ALL, False, tid)
    if not h:
        return None
    ctx = _aligned_ctx()
    ctx.ContextFlags = CTX_FLAGS
    ok = k32.GetThreadContext(h, ctypes.byref(ctx))
    k32.CloseHandle(h)
    return ctx if ok else None


def diag():
    """Verify CONTEXT offsets + whether DR0/DR7 actually stick (anti-debug clears them?)."""
    rep = {"sizeof_CONTEXT": ctypes.sizeof(CONTEXT),
           "off_Dr0": CONTEXT.Dr0.offset, "off_Dr7": CONTEXT.Dr7.offset,
           "off_Rip": CONTEXT.Rip.offset}
    pid = find_pid(PROC); base = module_base(pid, PROC)
    target = base + POS_OFF
    rep["target"] = hex(target)
    if not k32.DebugActiveProcess(pid):
        rep["err"] = "attach"; print(json.dumps(rep)); return
    k32.DebugSetProcessKillOnExit(False)
    de = DEBUG_EVENT()
    armed_tid = None
    t0 = time.time()
    while time.time() - t0 < 5 and armed_tid is None:
        if not k32.WaitForDebugEvent(ctypes.byref(de), 200):
            continue
        if de.dwDebugEventCode in (CREATE_THREAD_DEBUG_EVENT, CREATE_PROCESS_DEBUG_EVENT):
            if set_hw_bp(de.dwThreadId, target, 3):
                ctx = read_ctx(de.dwThreadId)
                rep["armed_tid"] = de.dwThreadId
                rep["readback_Dr0"] = hex(ctx.Dr0) if ctx else None
                rep["readback_Dr7"] = hex(ctx.Dr7) if ctx else None
                armed_tid = de.dwThreadId
        k32.ContinueDebugEvent(de.dwProcessId, de.dwThreadId, DBG_CONTINUE)
    # wait, then re-read same thread's DR to see if it was cleared
    if armed_tid:
        # pump events for 3s so the process keeps running
        t1 = time.time()
        while time.time() - t1 < 3:
            if k32.WaitForDebugEvent(ctypes.byref(de), 200):
                k32.ContinueDebugEvent(de.dwProcessId, de.dwThreadId, DBG_CONTINUE)
        h = k32.OpenThread(THREAD_ALL, False, armed_tid)
        k32.SuspendThread(h)
        ctx2 = _aligned_ctx(); ctx2.ContextFlags = CTX_FLAGS
        okc = k32.GetThreadContext(h, ctypes.byref(ctx2))
        k32.ResumeThread(h); k32.CloseHandle(h)
        rep["after3s_Dr0"] = hex(ctx2.Dr0) if okc else "geterr"
        rep["after3s_Dr7"] = hex(ctx2.Dr7) if okc else "geterr"
        clear_hw_bp(armed_tid)
    k32.DebugActiveProcessStop(pid)
    print(json.dumps(rep))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "diag":
        diag(); return
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
    rwmode = (sys.argv[2] if len(sys.argv) > 2 else "w").lower()
    rw = 3 if rwmode == "rw" else 1

    pid = find_pid(PROC)
    base = module_base(pid, PROC)
    target = base + POS_OFF

    result = {"pid": pid, "base": hex(base), "target": hex(target),
              "rw": rwmode, "hits": [], "by_rip": {}, "threads_armed": 0}

    if not k32.DebugActiveProcess(pid):
        result["err"] = f"DebugActiveProcess failed {ctypes.get_last_error()}"
        open(OUT, "w").write(json.dumps(result)); print(json.dumps(result)); return
    k32.DebugSetProcessKillOnExit(False)

    de = DEBUG_EVENT()
    armed = set()
    t0 = time.time()
    hit_count = 0
    MAX_HITS = 400

    try:
        while time.time() - t0 < secs and hit_count < MAX_HITS:
            if not k32.WaitForDebugEvent(ctypes.byref(de), 200):
                continue
            code = de.dwDebugEventCode
            tid = de.dwThreadId
            cont = DBG_CONTINUE

            if code == CREATE_PROCESS_DEBUG_EVENT:
                # union: CREATE_PROCESS_DEBUG_INFO, hThread at offset 0x10
                hthread = struct.unpack_from("<Q", bytes(de.u), 0x10)[0]
                if set_hw_bp(tid, target, rw):
                    armed.add(tid)
                k32.CloseHandle(hthread)
            elif code == CREATE_THREAD_DEBUG_EVENT:
                if set_hw_bp(tid, target, rw):
                    armed.add(tid)
            elif code == EXCEPTION_DEBUG_EVENT:
                exc_code = struct.unpack_from("<I", bytes(de.u), 0)[0]
                if exc_code == EXCEPTION_SINGLE_STEP:
                    ctx = read_ctx(tid)
                    if ctx:
                        rip = ctx.Rip
                        regs = {r: getattr(ctx, r) for r in GPR}
                        hit_count += 1
                        key = hex(rip)
                        slot = result["by_rip"].get(key)
                        if slot is None:
                            slot = {"rip": key, "rip_off": hex(rip - _BASE) if _BASE <= rip < _END else None,
                                    "count": 0,
                                    "regs": {k_: hex(v) for k_, v in regs.items()},
                                    "stack": stack_returns(ctx.Rsp)}
                            result["by_rip"][key] = slot
                        slot["count"] += 1
                    # handled -> DBG_CONTINUE re-arms the watchpoint automatically
                elif exc_code == EXCEPTION_BREAKPOINT:
                    cont = DBG_CONTINUE       # initial attach breakpoint
                else:
                    cont = DBG_EXCEPTION_NOT_HANDLED   # let the game handle its own
            elif code == EXIT_PROCESS_DEBUG_EVENT:
                k32.ContinueDebugEvent(de.dwProcessId, tid, DBG_CONTINUE)
                break

            k32.ContinueDebugEvent(de.dwProcessId, tid, cont)
    finally:
        # CRITICAL: disarm every thread BEFORE detaching, or leftover watchpoints
        # crash the game on the next access with no debugger to handle the trap.
        for tid in list(armed):
            try:
                clear_hw_bp(tid)
            except Exception:
                pass
        k32.DebugActiveProcessStop(pid)

    result["threads_armed"] = len(armed)
    result["total_hits"] = hit_count
    # rank accessor sites by frequency
    result["sites"] = sorted(result["by_rip"].values(), key=lambda s: -s["count"])
    result.pop("by_rip", None)
    open(OUT, "w").write(json.dumps(result, indent=1))
    print(json.dumps({"pid": pid, "target": result["target"],
                      "threads_armed": len(armed), "total_hits": hit_count,
                      "sites": len(result["sites"])}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print(json.dumps({"err": str(e), "tb": traceback.format_exc()}))
