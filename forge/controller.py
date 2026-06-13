"""ForgeController — live backend (FORGE.md §2/§3). Same public interface as
forge/sim.py so the web app uses either interchangeably; this one drives real
Guests + CraftWorkers and enforces the cross-tool account interlock.

The web endpoints call these methods synchronously (fire-and-forget); anything
slow (OCR, launch) is scheduled as an asyncio task so the dashboard never blocks.
"""
from __future__ import annotations

import asyncio
import logging
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
                 profile_dir: Path, characters: dict) -> None:
        self.t = tele
        self.cfg_profile = craft_profile
        self.profile_dir = profile_dir
        self.chars = characters or {}
        self.lock = AccountLock()
        self.guests: dict[str, Guest] = {}
        self.workers: dict[str, CraftWorker] = {}
        self.stations: dict[str, dict] = {}
        for bot in stations.get("bots", []):
            bid = bot["id"]
            g = Guest(bot["dom"], bot.get("width", 1920), bot.get("height", 1080))
            self.guests[bid] = g
            self.workers[bid] = CraftWorker(g, craft_profile, profile_dir, tele, bid)
            self.stations[bid] = bot

    # -- interlock helpers -------------------------------------------------
    def _account(self, bid: str) -> str:
        char = (self.stations.get(bid, {}).get("character") or "")
        return self.chars.get(char, {}).get("account", "")

    def _holder(self, bid: str) -> str:
        s = self.stations.get(bid, {})
        return f"forge:{s.get('dom', bid)}:{s.get('character') or '?'}"

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
        char = s.get("character") or ""
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
        TODO: char-select OCR region needs in-game calibration; until then this is a
        no-op that logs, leaving the client at char-select for manual pick."""
        self.t.push_log(bot_id, f"char-select: pick {char} (OCR pick pending calibration)")

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
