"""CraftWorker (FORGE.md §5/§6) — the per-guest craft loop + writ driver.

Ports the dino's logic (CraftThread/WritBatchThread) onto the host-side framework:
sense with sensors.py over a Guest, inject with Guest.press_keys/click/type_text.
One worker per crafter VM; runs as its own asyncio task. All guest I/O is wrapped
in run_in_executor so two workers + the dashboard share one loop without blocking.

Fail-safe by construction: with uncalibrated coords the begin/retry fingerprint
never matches, so a craft job simply waits and times out instead of mashing keys.
Chat-safety (PROJECT.md §6.2) gates EVERY keypress — never inject unless the chat
input is provably clear.
"""
from __future__ import annotations

import asyncio
import logging
import time
from functools import partial
from pathlib import Path

from . import sensors
from .guest import Guest
from .recipes import search_name
from .telemetry import ForgeTelemetry

log = logging.getLogger("forge.worker")

WAIT_BUTTON_S = 30.0      # give up waiting for Begin/Retry after this (then idle)


class CraftWorker:
    def __init__(self, guest: Guest, profile: dict, profile_dir: Path,
                 tele: ForgeTelemetry, bot_id: str, keymap: dict | None = None) -> None:
        self.guest = guest
        self.cfg = profile                  # craft.yaml dict (calibration)
        self.profile_dir = profile_dir
        self.t = tele
        self.id = bot_id
        self.keymap = keymap or {}          # camp + arts keys (owner-configurable)
        self._job: dict | None = None       # {mode, trade_class, queue/recipe, count}
        self._stop = asyncio.Event()
        self._paused = False
        self._new_job = asyncio.Event()
        self._aborted = 0                   # chat-unsafe injection aborts
        self._ref_buttons: list = []        # in-memory reaction-button references (per craft)

    # -- control (called by the controller) --------------------------------
    def start(self, mode: str, trade_class: str, recipe: str = "",
              count: int = 1, queue: list | None = None) -> None:
        if mode == "writ":
            q = [dict(it, done=0) for it in (queue or [])]
            self._job = {"mode": "writ", "trade_class": trade_class, "queue": q}
        else:
            self._job = {"mode": "single", "trade_class": trade_class,
                         "recipe": recipe or "", "count": max(1, int(count or 1))}
        self._stop.clear()
        self._paused = False
        self._new_job.set()

    def stop(self) -> None:
        self._job = None
        self._stop.set()
        self._new_job.set()

    def pause(self, on: bool | None = None) -> None:
        self._paused = (not self._paused) if on is None else on

    # -- helpers -----------------------------------------------------------
    async def _ex(self, fn, *a):
        """Run a (blocking) guest op in the default executor."""
        return await asyncio.get_running_loop().run_in_executor(None, partial(fn, *a))

    def _arts(self, mode: str) -> list[str]:
        """The counter#1/#2/#3 keys for the given mode (durability|progress), from
        the owner keymap. Defaults 1-3 (durability) / 4-6 (progress)."""
        defaults = {"durability": ["1", "2", "3"], "progress": ["4", "5", "6"]}
        return list((self.keymap.get("arts", {}) or {}).get(mode) or defaults.get(mode, []))

    def _counter_key(self, mode: str, counter: int) -> str | None:
        arts = self._arts(mode)
        return arts[counter - 1] if 1 <= counter <= len(arts) else None

    async def _press(self, seq: str, role: str = "craft") -> bool:
        """THE chat-safety gate (fail-closed) wrapping every keypress."""
        if not await self._ex(self.guest.grab):
            return False
        if not await self._ex(sensors.chat_safe, self.guest, self.cfg):
            self._aborted += 1
            self.t.push_log(self.id, f"inject ABORTED (chat unsafe): {role}")
            return False
        await self._ex(self.guest.press_keys, seq)
        return True

    async def _wait_unpaused(self) -> None:
        while self._paused and not self._stop.is_set():
            self.t.update_bot(self.id, state="paused")
            await asyncio.sleep(0.4)

    # -- recipe selection --------------------------------------------------
    async def _select_recipe(self, name: str, trade_class: str) -> None:
        rs = self.cfg.get("recipe_select", {})
        timings = self.cfg.get("timings", {})
        for key in ("clear_click", "search_click"):
            loc = rs.get(key)
            if loc:
                await self._ex(self.guest.click, loc[0], loc[1])
                await asyncio.sleep(0.2)
        await self._ex(self.guest.type_text, search_name(name, trade_class), True)
        await asyncio.sleep(0.3)
        for key in ("result_click", "focus_click"):
            loc = rs.get(key)
            if loc:
                await self._ex(self.guest.click, loc[0], loc[1])
                await asyncio.sleep(0.2)
        await asyncio.sleep(timings.get("post_select", 0.5))

    async def _focus_craft(self) -> None:
        loc = self.cfg.get("craft_focus_click")
        if loc:
            await self._ex(self.guest.click, loc[0], loc[1])
            await asyncio.sleep(0.1)

    # -- one craft cycle (reactions + arts until complete) -----------------
    async def _craft_cycle(self) -> bool:
        """Run arts (reacting to events) until the craft completes (Begin/Retry
        reappears). Returns True on completion, False if stopped."""
        timings = self.cfg.get("timings", {})
        await self._focus_craft()
        # capture the 3 reaction-button references FRESH for THIS craft (no saved
        # library) — works for any class, including random quest crafts.
        self._ref_buttons = await self._ex(sensors.capture_buttons, self.guest, self.cfg)
        got = sum(1 for b in self._ref_buttons if b is not None)
        if got:
            self.t.push_log(self.id, f"captured {got} reaction-button references")
        while not self._stop.is_set():
            await self._wait_unpaused()
            if self._stop.is_set():
                return False
            await self._ex(self.guest.grab)
            # power gate
            if not await self._ex(sensors.power_ok, self.guest, self.cfg):
                self.t.update_bot(self.id, state="waiting_power", power_gated=True)
                pkey = (self.cfg.get("power", {}) or {}).get("ability_key")
                if pkey:
                    await self._press(pkey, "power")
                await asyncio.sleep(timings.get("power_wait", 1.0))
                continue
            self.t.update_bot(self.id, state="crafting", power_gated=False)
            # current craft mode (durability vs progress) decides which art set
            mode = await self._ex(sensors.durability_mode, self.guest, self.cfg) or "progress"
            self.t.update_bot(self.id, durability_mode=mode)
            # counter EVENT (#1/#2/#3)? match the watch region vs the in-memory
            # references; press the keymap key for (mode, counter#)
            counter = await self._ex(sensors.reaction_event, self.guest, self.cfg, self._ref_buttons)
            if counter:
                key = self._counter_key(mode, counter)
                if key:
                    await self._press(key, f"counter{counter}:{mode}")
                    self.t.update_bot(self.id, reactions=self.t.bot(self.id)["reactions"] + 1)
                    self.t.push_log(self.id, f"counter#{counter} ({mode}) -> {key}")
            # complete?
            if await self._ex(sensors.begin_or_retry, self.guest, self.cfg):
                return True
            await asyncio.sleep(timings.get("art_interval", 0.85))
        return False

    async def _craft_recipe(self, name: str, count: int, trade_class: str,
                            item_idx: int = 0, item_total: int = 0) -> int:
        timings = self.cfg.get("timings", {})
        self.t.update_bot(self.id, state="selecting", recipe=name,
                          count={"done": 0, "total": count},
                          item={"idx": item_idx, "total": item_total})
        await self._select_recipe(name, trade_class)
        done = 0
        while done < count and not self._stop.is_set():
            # wait for Begin/Retry (fail-safe: times out if uncalibrated)
            t0 = time.time()
            while time.time() - t0 < WAIT_BUTTON_S and not self._stop.is_set():
                await self._ex(self.guest.grab)
                if await self._ex(sensors.begin_or_retry, self.guest, self.cfg):
                    break
                await asyncio.sleep(0.4)
            else:
                self.t.push_log(self.id, f"no Begin/Retry for {name} (calibration?) — skipping")
                break
            if self._stop.is_set():
                break
            # press Begin/Retry (click + confirm), then run the cycle
            which = await self._ex(sensors.begin_or_retry, self.guest, self.cfg)
            clk = (self.cfg.get(which or "begin", {}) or {}).get("click")
            if clk:
                await self._ex(self.guest.click, clk[0], clk[1])
                await asyncio.sleep(timings.get("post_begin", 0.5))
                await self._press("enter", "confirm")
            if await self._craft_cycle():
                done += 1
                self.t.update_bot(self.id, count={"done": done, "total": count},
                                  crafts_done=self.t.bot(self.id)["crafts_done"] + 1)
                self.t.push_event(self.id, "craft", f"{name} {done}/{count}")
        return done

    # -- job runner --------------------------------------------------------
    async def _run_job(self, job: dict) -> None:
        # don't fire clicks/keys at a closed game — that errors the guest AHK and
        # does nothing useful. Require EQ2 up + in-world (Launch first).
        if not await self._ex(self.guest.eq2_running):
            self.t.update_bot(self.id, state="idle")
            self.t.push_log(self.id, "EQ2 not running — press Launch first, then Start")
            self.t.push_event(self.id, "control", "start ignored (game not running)")
            return
        tc = job["trade_class"]
        self.t.update_bot(self.id, started_at=time.time(), reactions=0, crafts_done=0)
        if job["mode"] == "writ":
            q = job["queue"]
            self.t.update_bot(self.id, queue=q)
            for i, it in enumerate(q, 1):
                if self._stop.is_set():
                    break
                await self._craft_recipe(it["name"], it["count"], tc, i, len(q))
                it["done"] = it["count"]
                self.t.update_bot(self.id, queue=q)
            self.t.push_event(self.id, "craft", "batch complete")
        else:
            await self._craft_recipe(job["recipe"], job["count"], tc)
            self.t.push_event(self.id, "craft", "done")
        self.t.update_bot(self.id, state="done", durability_mode=None)

    async def run(self) -> None:
        """Supervisor: idle until a job arrives, run it, idle again."""
        while True:
            await self._new_job.wait()
            self._new_job.clear()
            job = self._job
            if job is None:                       # was a stop
                self.t.update_bot(self.id, state="idle")
                continue
            try:
                await self._run_job(job)
            except asyncio.CancelledError:
                raise
            except Exception as e:                # never let a worker die silently
                log.exception("worker %s crashed", self.id)
                self.t.update_bot(self.id, state="error")
                self.t.push_log(self.id, f"ERROR: {e}")
