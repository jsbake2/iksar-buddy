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

from shared.account_lock import AccountLock

from . import sensors
from .guest import Guest
from .telemetry import ForgeTelemetry
from .worker import CraftWorker

log = logging.getLogger("forge.controller")

LAUNCHER_LOG = r"C:\ib\launcher.log"


class ForgeController:
    def __init__(self, tele: ForgeTelemetry, stations: dict, craft_profile: dict,
                 profile_dir: Path, characters: dict, class_chars: dict | None = None) -> None:
        self.t = tele
        self.cfg_profile = craft_profile
        self.profile_dir = profile_dir
        self.chars = characters or {}
        self.class_chars = class_chars or {}      # tradeskill -> character
        self.lock = AccountLock()
        self.guests: dict[str, Guest] = {}
        self.workers: dict[str, CraftWorker] = {}
        self.stations: dict[str, dict] = {}
        self._frames: dict[str, tuple[float, bytes]] = {}   # bot_id -> (ts, jpeg)
        for bot in stations.get("bots", []):
            bid = bot["id"]
            g = Guest(bot["dom"], bot.get("width", 1920), bot.get("height", 1080))
            self.guests[bid] = g
            self.workers[bid] = CraftWorker(g, craft_profile, profile_dir, tele, bid)
            self.stations[bid] = bot

    # -- character / interlock helpers -------------------------------------
    def _char_for(self, bid: str) -> str:
        """The character a bot uses = the toon mapped to its chosen trade class
        (class_chars). Falls back to an explicit station character if set."""
        b = self.t.bot(bid) or {}
        tc = b.get("trade_class") or ""
        return (self.class_chars.get(tc) or self.stations.get(bid, {}).get("character") or "").strip()

    def _account(self, bid: str) -> str:
        return self.chars.get(self._char_for(bid), {}).get("account", "")

    def _holder(self, bid: str) -> str:
        return f"forge:{self.stations.get(bid, {}).get('dom', bid)}:{self._char_for(bid) or '?'}"

    def set_class_chars(self, mapping: dict) -> None:
        self.class_chars = mapping or {}

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
        from .recipes import parse_recipe_list
        g = self.guests.get(bot_id)
        if not g:
            return
        self.t.push_event(bot_id, "log", "reading recipe log…")
        text = await asyncio.get_running_loop().run_in_executor(
            None, partial(g.read_file, r"C:\ib\recipes.txt"))
        items = parse_recipe_list(text or "")
        queue = [{"name": n, "count": c, "done": 0} for n, c in items.items()]
        self.t.update_bot(bot_id, mode="writ", queue=queue)
        self.t.push_event(bot_id, "log", f"log: {len(queue)} recipes")

    # -- live VM screen (dashboard thumbnail) ------------------------------
    def frame_jpeg(self, bot_id: str) -> bytes:
        """One virsh screenshot of the bot's VM as a downscaled JPEG. Cached ~1s so
        rapid <img> reloads from two panels are cheap. b'' if the VM isn't grabbable."""
        g = self.guests.get(bot_id)
        if not g:
            return b""
        ts, data = self._frames.get(bot_id, (0.0, b""))
        now = time.time()
        if now - ts < 1.0 and data:
            return data
        if not g.grab():
            return b""
        try:
            out = subprocess.run(["magick", g.ppm, "-scale", "640", "-quality", "65", "jpg:-"],
                                 capture_output=True, timeout=4).stdout
        except (OSError, subprocess.SubprocessError):
            return b""
        if out:
            self._frames[bot_id] = (now, out)
        return out

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
            if tail and "char-select ready" in tail:
                ready = True
                break
            if tail and "in-world" in tail:
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
        """Host-side OCR-and-click character pick at char-select (FORGE.md §5.5).
        Empty char => leave at char-select (owner picks / creates a toon)."""
        if not char:
            self.t.push_log(bot_id, "no character set — left at char-select")
            return
        loop = asyncio.get_running_loop()
        g = self.guests[bot_id]
        pt = await loop.run_in_executor(None, partial(sensors.find_character, g, self.cfg_profile, char))
        if not pt:
            self.t.push_log(bot_id, f"char-select: '{char}' not found (calibration/scroll?)")
            return
        await loop.run_in_executor(None, g.click, pt[0], pt[1])     # select the row
        await asyncio.sleep(0.7)
        play = (self.cfg_profile.get("char_select", {}) or {}).get("play_click")
        if play:
            await loop.run_in_executor(None, g.click, play[0], play[1])  # Play -> in-world
        self.t.push_log(bot_id, f"selected {char} @ {pt} -> Play")

    def switch_char(self, bot_id: str) -> None:
        self.t.push_event(bot_id, "launch", "camp + switch crafter (pending calibration)")

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
