"""ForgeController — live backend (FORGE.md §2/§3). Same public interface as
forge/sim.py so the web app uses either interchangeably; this one drives real
Guests + CraftWorkers and enforces the cross-tool account interlock.

The web endpoints call these methods synchronously (fire-and-forget); anything
slow (OCR, launch) is scheduled as an asyncio task so the dashboard never blocks.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from functools import partial
from pathlib import Path

import yaml

from shared.account_lock import AccountLock

from . import sensors
from .guest import Guest
from .telemetry import ForgeTelemetry
from .worker import CraftWorker

log = logging.getLogger("forge.controller")

LAUNCHER_LOG = r"C:\ib\launcher.log"


def _deep_merge(dst: dict, src: dict) -> dict:
    """Recursively merge src into dst (in place). Used to fold calibration captures
    into the loaded craft.yaml profile."""
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


class ForgeController:
    ACCOUNT_FOR_VM = {"vm1": "account1", "vm2": "account2"}

    def __init__(self, tele: ForgeTelemetry, stations: dict, craft_profile: dict,
                 profile_dir: Path, crafters: list | None = None,
                 keymap: dict | None = None) -> None:
        self.t = tele
        self.cfg_profile = craft_profile
        self.profile_dir = profile_dir
        self.crafters = crafters or []            # [{character, class, vm}]
        self.keymap = keymap or {}                # camp command + arts keys
        self.lock = AccountLock()
        self.guests: dict[str, Guest] = {}
        self.workers: dict[str, CraftWorker] = {}
        self.stations: dict[str, dict] = {}
        self._frames: dict[str, tuple[float, bytes]] = {}   # bot_id -> (ts, jpeg)
        for bot in stations.get("bots", []):
            bid = bot["id"]
            g = Guest(bot["dom"], bot.get("width", 1920), bot.get("height", 1080))
            self.guests[bid] = g
            self.workers[bid] = CraftWorker(g, craft_profile, profile_dir, tele, bid, self.keymap)
            self.stations[bid] = bot

    # -- character / interlock helpers -------------------------------------
    def _char_for(self, bid: str) -> str:
        """The character a bot uses = the crafter selected in its dropdown (stored
        on the bot as `character` when the combo is picked)."""
        return ((self.t.bot(bid) or {}).get("character") or "").strip()

    def _account(self, bid: str) -> str:
        """Interlock key derived from the bot's VM (vm1->account1, vm2->account2),
        matching the healer's account locks."""
        vm = (self.t.bot(bid) or {}).get("vm") or self.stations.get(bid, {}).get("vm", "")
        return self.ACCOUNT_FOR_VM.get(vm, "")

    def _holder(self, bid: str) -> str:
        return f"forge:{self.stations.get(bid, {}).get('dom', bid)}:{self._char_for(bid) or '?'}"

    def set_crafters(self, crafters: list) -> None:
        self.crafters = crafters or []

    def _acquire(self, bid: str) -> bool:
        acct = self._account(bid)
        if not acct:
            return True                            # unmapped char -> no lock
        ok, who = self.lock.acquire(acct, self._holder(bid))
        if not ok:
            self.t.push_event(bid, "interlock",
                              f"BLOCKED: account '{acct}' in use by {who}")
            self.t.push_log(bid, f"launch refused — {acct} held by {who}")
        return ok

    def _release(self, bid: str) -> None:
        acct = self._account(bid)
        if acct:
            self.lock.release(acct, self._holder(bid))

    # -- dashboard actions (mirror ForgeSim) -------------------------------
    def enable(self, bot_id: str, on: bool) -> None:
        self.t.update_bot(bot_id, enabled=on, state=("idle" if on else "off"))
        self.t.push_event(bot_id, "control", "enabled" if on else "disabled")

    def configure(self, bot_id: str, **fields) -> None:
        clean = {k: v for k, v in fields.items() if k in ("trade_class", "mode", "recipe")}
        if "count" in fields:
            try:
                clean["count"] = {"done": 0, "total": max(1, int(fields["count"]))}
            except (TypeError, ValueError):
                pass
        if "character" in fields:                  # lets the owner set the toon live
            self.stations.setdefault(bot_id, {})["character"] = fields["character"]
            self.t.update_bot(bot_id, character=fields["character"])
        if clean:
            self.t.update_bot(bot_id, **clean)

    def start(self, bot_id: str, mode: str, trade_class: str,
              recipe: str = "", count: int = 1) -> None:
        b = self.t.bot(bot_id)
        w = self.workers.get(bot_id)
        if not b or not w or not b["enabled"]:
            return
        if not self._acquire(bot_id):
            self.t.update_bot(bot_id, state="error")
            return
        if mode == "writ":
            w.start("writ", trade_class, queue=b.get("queue", []))
            self.t.push_event(bot_id, "craft", f"writ start ({len(b.get('queue', []))} recipes)")
        else:
            w.start("single", trade_class, recipe=recipe, count=count)
            self.t.push_event(bot_id, "craft", f"single start: {recipe or '(loaded)'} x{count}")
        self.t.update_bot(bot_id, mode=mode, trade_class=trade_class, state="selecting")

    def stop(self, bot_id: str) -> None:
        w = self.workers.get(bot_id)
        if w:
            w.stop()
        self._release(bot_id)
        self.t.update_bot(bot_id, state="idle", power_gated=False)
        self.t.push_event(bot_id, "control", "stopped")

    def pause(self, bot_id: str) -> None:
        w = self.workers.get(bot_id)
        if w:
            w.pause()
            self.t.push_event(bot_id, "control", "pause toggled")

    def set_queue(self, bot_id: str, queue: list) -> None:
        clean = []
        for it in queue or []:
            name = str(it.get("name", "")).strip()
            if not name:
                continue
            try:
                cnt = max(1, int(it.get("count", 1)))
            except (TypeError, ValueError):
                cnt = 1
            clean.append({"name": name, "count": cnt, "done": 0})
        self.t.update_bot(bot_id, mode="writ", queue=clean)
        self.t.push_event(bot_id, "queue", f"{len(clean)} recipes queued")

    # OCR / log reads run in the background (slow) ------------------------
    def ocr_journal(self, bot_id: str) -> None:
        asyncio.create_task(self._ocr_journal(bot_id))

    async def _ocr_journal(self, bot_id: str) -> None:
        g = self.guests.get(bot_id)
        b = self.t.bot(bot_id)
        if not g or not b:
            return
        self.t.push_event(bot_id, "ocr", "reading journal…")
        items = await asyncio.get_running_loop().run_in_executor(
            None, partial(sensors.ocr_journal, g, self.cfg_profile, b.get("trade_class", "")))
        queue = [{"name": n, "count": c, "done": 0} for n, c in items.items()]
        self.t.update_bot(bot_id, mode="writ", queue=queue)
        self.t.push_event(bot_id, "ocr", f"journal: {len(queue)} recipes")

    def read_log(self, bot_id: str) -> None:
        asyncio.create_task(self._read_log(bot_id))

    async def _read_log(self, bot_id: str) -> None:
        from .recipes import parse_scribed_recipes
        g = self.guests.get(bot_id)
        b = self.t.bot(bot_id)
        if not g or not b:
            return
        char = self._char_for(bot_id)
        if not char:
            self.t.push_log(bot_id, "read log: no crafter selected")
            self.t.push_event(bot_id, "log", "no character — pick a crafter first")
            return
        cfg = self.cfg_profile.get("eq2_log", {}) or {}
        log_dir = (cfg.get("dir")
                   or r"C:\Users\Public\Daybreak Game Company\Installed Games"
                      r"\EverQuest II\logs").rstrip("\\")
        server = (self.cfg_profile.get("char_select", {}) or {}).get("server", "")
        path = "\\".join(p for p in (log_dir, server, f"eq2log_{char}.txt") if p)
        tail = int(cfg.get("tail", 5000) or 5000)
        self.t.push_event(bot_id, "log", f"reading {char}'s chat log…")
        text = await asyncio.get_running_loop().run_in_executor(
            None, partial(g.read_file, path, tail))
        if not text:
            self.t.push_log(bot_id, f"log empty/not found: {path} (is /log on?)")
            self.t.push_event(bot_id, "log", "log not found — turn /log on in-game")
            return
        items = parse_scribed_recipes(text)
        # merge into the existing queue (dedupe by name) so scribing more books
        # and OCR reads accumulate rather than clobber.
        queue = list(b.get("queue", []) or [])
        have = {str(q.get("name", "")).strip().lower() for q in queue}
        added = 0
        for name in items:
            if name.lower() not in have:
                queue.append({"name": name, "count": 1, "done": 0})
                have.add(name.lower())
                added += 1
        self.t.update_bot(bot_id, mode="writ", queue=queue)
        self.t.push_event(bot_id, "log", f"log: +{added} scribed recipe(s) ({len(queue)} queued)")

    # -- live VM screen (dashboard thumbnail / calibration full-res) -------
    def frame_jpeg(self, bot_id: str, full: bool = False) -> bytes:
        """One virsh screenshot of the bot's VM as JPEG. full=False -> 640px (cached,
        for panels); full=True -> native 1920 (for the calibration picker, uncached
        so coords are exact). b'' if the VM isn't grabbable."""
        g = self.guests.get(bot_id)
        if not g:
            return b""
        if not full:
            ts, data = self._frames.get(bot_id, (0.0, b""))
            now = time.time()
            if now - ts < 1.0 and data:
                return data
        if not g.grab():
            return b""
        args = (["magick", g.ppm, "-quality", "85", "jpg:-"] if full
                else ["magick", g.ppm, "-scale", "640", "-quality", "65", "jpg:-"])
        try:
            out = subprocess.run(args, capture_output=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return b""
        if out and not full:
            self._frames[bot_id] = (time.time(), out)
        return out

    def vm_off(self, bot_id: str) -> bool:
        """True if the bot's VM is powered off (domstate 'shut off'). Lets the
        frame endpoint return 'powered off' instead of a stale last frame."""
        g = self.guests.get(bot_id)
        return bool(g and g.state() == "shut off")

    # -- calibration capture (the "set up tradeskills" window) -------------
    def pixel(self, bot_id: str, x: int, y: int) -> list | None:
        g = self.guests.get(bot_id)
        if not g or not g.grab():
            return None
        return list(g.pixel(int(x), int(y)))

    def save_calib(self, updates: dict) -> bool:
        """Deep-merge captured values into craft.yaml (in IB_FORGE_DIR) + reload.
        Workers share self.cfg_profile by reference, so they pick it up live."""
        if not isinstance(updates, dict):
            return False
        _deep_merge(self.cfg_profile, updates)
        try:
            (self.profile_dir / "craft.yaml").write_text(
                "# Forge craft calibration (dashboard-captured).\n"
                + yaml.safe_dump(self.cfg_profile, sort_keys=False, allow_unicode=True),
                encoding="utf-8")
        except OSError:
            return False
        return True

    # -- launch / switch (login automation, FORGE.md §5.5) ----------------
    def launch(self, bot_id: str) -> None:
        asyncio.create_task(self._launch(bot_id))

    async def _launch(self, bot_id: str) -> None:
        g = self.guests.get(bot_id)
        s = self.stations.get(bot_id, {})
        if not g:
            return
        if not self._acquire(bot_id):
            return
        loop = asyncio.get_running_loop()
        char = self._char_for(bot_id)
        self.t.update_bot(bot_id, state="launching", vm_running=True)
        self.t.push_event(bot_id, "launch", f"power on {g.dom} -> login {char or '?'}")
        if not await loop.run_in_executor(None, g.start_vm):
            self.t.push_log(bot_id, "VM start failed"); return
        for _ in range(40):                        # wait guest agent
            if await loop.run_in_executor(None, g.agent_ready):
                break
            await asyncio.sleep(3)
        # The guest agent answers at the Windows LOGIN screen — before the interactive
        # desktop exists. Firing ibrun then = "boots to Windows, nothing launches". Wait
        # for the user session (explorer.exe) + a settle, like the healer's launch_bot.sh.
        self.t.push_event(bot_id, "launch", "waiting for desktop (auto-login)…")
        for _ in range(25):                        # up to ~75s for auto-login
            out = await loop.run_in_executor(None, g.exec_ps,
                "if (Get-Process explorer -ErrorAction SilentlyContinue) {'Y'}")
            if out and "Y" in out:
                break
            await asyncio.sleep(3)
        await asyncio.sleep(10)                     # let the desktop/shell settle
        # Idempotent: if EQ2 is ALREADY running (e.g. parked at char-select), DON'T
        # re-fire the launcher (that pops a 2nd LaunchPad over the live client). Just
        # go pick the character.
        if await loop.run_in_executor(None, g.eq2_running):
            self.t.push_event(bot_id, "launch", "EQ2 already up — skipping launcher, selecting character")
        else:
            # tell the (craft) launcher which character to select, then fire it
            if char:
                await loop.run_in_executor(None, g.exec_ps,
                                           f"Set-Content C:\\ib\\target_char.txt '{char}' -NoNewline", False)
            # clear the launcher log FIRST so we wait for THIS run's char-select, not a
            # stale "char-select ready" from a prior launch (the "jumped to in-world" bug)
            await loop.run_in_executor(None, g.exec_ps, 'Set-Content C:\\ib\\launcher.log ""', False)
            await asyncio.sleep(1)
            await loop.run_in_executor(None, g.exec_ps, "Start-ScheduledTask -TaskName ibrun", False)
            # poll launcher.log for char-select, then host-side OCR pick
            ready = False
            for _ in range(120):
                tail = await loop.run_in_executor(None, g.read_file, LAUNCHER_LOG, 1)
                if tail and ("char-select ready" in tail or "in-world" in tail):
                    ready = True
                    break
                await asyncio.sleep(3)
            if not ready:
                self.t.push_log(bot_id, "launcher didn't reach char-select (calibration?)")
                self.t.update_bot(bot_id, state="idle")
                return
        await self._select_character(bot_id, char)
        self.t.update_bot(bot_id, state="idle")
        self.t.push_event(bot_id, "launch", f"in-world as {char or '?'} (verify)")

    async def _select_character(self, bot_id: str, char: str) -> None:
        """Validated host-side character pick at char-select (FORGE.md §5.5): click the
        row, read the detail-panel name to CONFIRM, then Play — same shared selector the
        healer login uses. Empty char => leave at char-select (owner picks a toon)."""
        if not char:
            self.t.push_log(bot_id, "no character set — left at char-select")
            return
        loop = asyncio.get_running_loop()
        g = self.guests[bot_id]
        ok = await loop.run_in_executor(
            None, partial(sensors.select_character, g, self.cfg_profile, char,
                          lambda m: self.t.push_log(bot_id, m), True))
        if not ok:
            self.t.push_log(bot_id, f"char-select: could not confirm '{char}' (left at char-select)")

    def switch_char(self, bot_id: str) -> None:
        self.t.push_event(bot_id, "launch", "camp + switch crafter (pending calibration)")

    # -- camp (log out to char-select) ------------------------------------
    def camp(self, bot_id: str) -> None:
        asyncio.create_task(self._camp(bot_id))

    def camp_all(self) -> None:
        for bid in list(self.workers):
            self.camp(bid)

    async def _camp(self, bot_id: str) -> None:
        """Force camp -> char-select. The owner's crafters bind a /camp macro to a KEY
        (e.g. Ctrl+-), so by default we PRESS that key (via ibkey, no chat typing —
        safest). If the camp value is instead a slash command ('/camp'), we click the
        chat bar and type it. Stops any craft + frees the account lock."""
        g = self.guests.get(bot_id)
        if not g:
            return
        loop = asyncio.get_running_loop()
        if not await loop.run_in_executor(None, g.eq2_running):
            self.t.push_log(bot_id, "camp: EQ2 not running")
            return
        w = self.workers.get(bot_id)
        if w:
            w.stop()
        camp = (self.keymap.get("camp") or "Ctrl+-").strip()
        if camp.startswith("/"):                   # slash command: type it into chat
            ci = (self.cfg_profile.get("chat_input", {}) or {}).get("region")
            if ci:
                await loop.run_in_executor(None, g.click, ci["x"] + ci["w"] // 2, ci["y"] + ci["h"] // 2)
                await asyncio.sleep(0.4)
            await loop.run_in_executor(None, g.type_text, camp, True)
        else:                                      # keybind (Ctrl+-): press via ibkey
            await loop.run_in_executor(None, g.press_keys, camp)
        self._release(bot_id)
        self.t.update_bot(bot_id, state="idle")
        self.t.push_event(bot_id, "control", f"camp ({camp})")

    def set_keymap(self, km: dict) -> None:
        self.keymap = km or {}
        for w in self.workers.values():
            w.keymap = self.keymap

    # -- shutdown (quit EQ2 + power off the VM) ----------------------------
    def shutdown(self, bot_id: str) -> None:
        asyncio.create_task(self._shutdown(bot_id))

    def shutdown_all(self) -> None:
        for bid in list(self.workers):
            self.shutdown(bid)

    async def _shutdown(self, bot_id: str) -> None:
        g = self.guests.get(bot_id)
        if not g:
            return
        loop = asyncio.get_running_loop()
        w = self.workers.get(bot_id)
        if w:
            w.stop()
        self._release(bot_id)
        self.t.update_bot(bot_id, state="off")
        self.t.push_event(bot_id, "control", "shutdown: quitting EQ2 + powering off VM")
        # quit EQ2 first (clean), then graceful VM power-off (ACPI -> Windows shutdown)
        await loop.run_in_executor(None, g.exec_ps,
                                   "Stop-Process -Name EverQuest2 -Force -ErrorAction SilentlyContinue", False)
        await asyncio.sleep(2)
        ok = await loop.run_in_executor(None, g.shutdown_vm)
        self.t.update_bot(bot_id, vm_running=False)
        self.t.push_event(bot_id, "control", "VM shutdown sent" if ok else "VM shutdown FAILED")

    # -- supervisor: run worker tasks + refresh held locks ----------------
    async def run(self) -> None:
        tasks = [asyncio.create_task(w.run()) for w in self.workers.values()]
        try:
            while True:
                for bid in list(self.workers):
                    acct = self._account(bid)
                    st = (self.t.bot(bid) or {}).get("state")
                    if acct and st in ("crafting", "selecting", "waiting_power", "launching"):
                        self.lock.refresh(acct, self._holder(bid))   # keep our lock alive
                    if st == "done":
                        self._release(bid)                            # free the account
                await asyncio.sleep(30)
        finally:
            for tk in tasks:
                tk.cancel()
