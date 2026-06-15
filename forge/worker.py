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
        self._pending: dict | None = None    # next job to run (set by start(), consumed by run())
        self._stop = asyncio.Event()
        self._paused = False
        self._new_job = asyncio.Event()
        self._aborted = 0                   # chat-unsafe injection aborts
        self._ref_buttons: list = []        # in-memory reaction-button references (per craft)
        self._filler_i = 0                  # rotating index into the mode's 3 filler arts
        self._last_counter = None           # debounce: counter # we last pressed (None = region clear)

    # -- control (called by the controller) --------------------------------
    def start(self, mode: str, trade_class: str, recipe: str = "",
              count: int = 1, queue: list | None = None) -> None:
        if mode == "writ":
            q = [dict(it, done=0) for it in (queue or [])]
            self._pending = {"mode": "writ", "trade_class": trade_class, "queue": q}
        else:
            self._pending = {"mode": "single", "trade_class": trade_class,
                             "recipe": recipe or "", "count": max(1, int(count or 1))}
        self._paused = False
        # SUPERSEDE any in-flight job: signal it to abort (its loops check _stop), then
        # wake run() which clears _stop and runs the pending job. Prevents a 2nd Start
        # from STACKING (which would re-search after the first craft finished).
        self._stop.set()
        self._new_job.set()

    def stop(self) -> None:
        self._pending = None
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
        """THE chat-safety gate (fail-closed) wrapping every keypress. Single-key craft
        arts (1-6) go via virsh send-key (type_text): SYNCHRONOUS + instant, so a counter
        actually lands in its window — ibkey is an async scheduled task that fires LATE
        and DROPS when filler+counter presses collide. Complex keys (modifiers/F-keys,
        e.g. mana-recover/camp) still use ibkey (send-key can't express them)."""
        if not await self._ex(self.guest.grab):
            return False
        if not await self._ex(sensors.chat_safe, self.guest, self.cfg):
            self._aborted += 1
            self.t.push_log(self.id, f"inject ABORTED (chat unsafe): {role}")
            return False
        if len(seq) == 1 and seq in "0123456789":
            await self._ex(self.guest.type_text, seq)      # fast synchronous send-key
        else:
            await self._ex(self.guest.press_keys, seq)     # ibkey for modifier/F-key specs
        return True

    async def _wait_unpaused(self) -> None:
        while self._paused and not self._stop.is_set():
            self.t.update_bot(self.id, state="paused")
            await asyncio.sleep(0.4)

    # -- recipe selection --------------------------------------------------
    async def _select_recipe(self, name: str, trade_class: str) -> bool:
        """Focus the search field, type the recipe name, filter, click the matching
        row's icon, then park focus on the craft window. Returns False on failure.

        Focusing the EQ2 search box is intermittent (a single ibgclick sometimes only
        activates the window, not the field). So we focus+type+verify in a RETRY loop:
        the proof of focus is that the result list FILTERED (match_recipe_row finds the
        recipe). The focus-click WAITS for the click to land (wait=True) so we never
        type into a not-yet-focused box, and typing is gated on chat-safety. Only after
        a verified match do we click the row icon to select it."""
        rs = self.cfg.get("recipe_select", {})
        timings = self.cfg.get("timings", {})
        click_settle = float(timings.get("click_settle", 0.8))
        type_settle = float(timings.get("pre_type_settle", 1.5))
        # EQ2's search field TRUNCATES long input (~18 chars), so "rawhide leather
        # backpack" becomes "rawhide leather ba" and matches unrelated "...leather..."
        # recipes. Use the TRAILING words that fit — the most distinctive part — so the
        # list filters precisely (owner-flagged: chop long names to fit the field).
        full = search_name(name, trade_class)
        maxlen = int(rs.get("search_maxlen", 18))
        query = full
        if len(full) > maxlen:
            ws = full.split()
            query = ws[-1]
            for w in reversed(ws[:-1]):
                if len(w) + 1 + len(query) <= maxlen:
                    query = f"{w} {query}"
                else:
                    break
        sb = rs.get("search_click")
        attempts = int(rs.get("focus_attempts", 3))

        row_click = None
        for i in range(1, attempts + 1):
            if self._stop.is_set():
                return False
            # clear the box first (the X) so stale/previous text doesn't corrupt the
            # query (owner-required; EQ2's field keeps the last search).
            clr = rs.get("clear_click")
            if rs.get("use_clear") and clr:
                await self._ex(partial(self.guest.click, clr[0], clr[1], True))
                await asyncio.sleep(click_settle)
            # focus the search field — double click (1st may only activate the window),
            # the 2nd WAITS for the click to land before we type.
            if sb:
                await self._ex(self.guest.click, sb[0], sb[1])
                await asyncio.sleep(click_settle)
                await self._ex(partial(self.guest.click, sb[0], sb[1], True))
                await asyncio.sleep(type_settle)
            # chat-safety gate: never type unless in-world + chat clear (the invariant)
            await self._ex(self.guest.grab)
            if not await self._ex(sensors.chat_safe, self.guest, self.cfg):
                self._aborted += 1
                self.t.push_log(self.id, "recipe type ABORTED (chat unsafe / not in-world)")
                return False
            # AHK Event-mode {Raw} — EQ2 UI fields ignore virsh send-key (type_text)
            await self._ex(self.guest.type_field, query, True)
            # POLL for the list to filter (takes ~1-2s) — don't re-type on the first
            # empty read or we double-search. Only a whole-window miss = focus failed.
            row_click = None
            for _ in range(int(rs.get("match_polls", 5))):
                await asyncio.sleep(float(timings.get("post_search", 0.6)))
                row_click = await self._ex(sensors.match_recipe_row, self.guest, self.cfg, name)
                if row_click:
                    break
            if row_click:
                break
            self.t.push_log(self.id, f"search not focused (attempt {i}/{attempts}) — retrying")
        if not row_click:
            self.t.push_log(self.id, f"recipe '{name}' not matched after {attempts} tries — skipping")
            return False
        self.t.push_log(self.id, f"select recipe -> double-click {row_click}")
        # DOUBLE-click to LOAD (owner does a single mouse click, but the programmatic
        # ibgclick single-click only HIGHLIGHTS — double reliably loads + shows Begin;
        # verified live). Do NOT click the safe-spot here (it deselects).
        await self._ex(partial(self.guest.double_click, row_click[0], row_click[1]))
        await asyncio.sleep(float(timings.get("post_select", 0.3)))
        return True

    async def _focus_craft(self) -> None:
        """Click the mouse-safe spot to FOCUS the craft window so the art keys (1-6) land
        in it, NOT on the combat hotbar. wait=True so the click LANDS before we press —
        send-key arts go to whatever's focused, so this MUST be reliable."""
        loc = self.cfg.get("craft_focus_click")
        if loc:
            await self._ex(partial(self.guest.click, loc[0], loc[1], True))
            await asyncio.sleep(float(self.cfg.get("timings", {}).get("post_focus", 0.3)))

    async def _recover_mana(self) -> None:
        """Between crafts: if mana is low, press the keymap mana-recover hotkey."""
        mk = (self.keymap.get("mana_recover") or "").strip()
        if not mk:
            return
        await self._ex(self.guest.grab)
        if await self._ex(sensors.power_ok, self.guest, self.cfg):
            return
        self.t.push_log(self.id, "low mana between crafts -> recover")
        await self._press(mk, "mana recover")
        await asyncio.sleep(float(self.cfg.get("timings", {}).get("post_begin", 0.5)))

    # -- counter check (highest priority, breaks any sequence) -------------
    async def _counter(self, mode: str) -> bool:
        """If a counter is showing in the watch area, press its key ONCE (durability or
        progress version for the current mode). The same event lingers ~3.5s, so we
        DEBOUNCE: press only when a NEW counter appears (region went empty, or a
        different counter #) — not every poll, which mashed the art 5-7x/event."""
        n = await self._ex(sensors.reaction_event, self.guest, self.cfg, self._ref_buttons)
        if not n:
            self._last_counter = None            # region clear -> next counter is "new"
            return False
        if n == self._last_counter:
            return False                         # same event still lingering -> already pressed;
            #                                      let filler SPAM resume until it clears + a new one shows
        self._last_counter = n
        key = self._counter_key(mode, n)
        if key:
            await self._press(key, f"counter{n}:{mode}")
            self.t.update_bot(self.id, reactions=self.t.bot(self.id)["reactions"] + 1)
            self.t.push_log(self.id, f"counter#{n} ({mode}) -> {key}")
        return True                              # NEW counter pressed once

    # -- one craft cycle ----------------------------------------------------
    async def _craft_cycle(self, gate_power: bool = True) -> bool:
        """Counter-FIRST loop until the craft completes. When no counter is up, send
        the filler sequence (1-2-3 in durability mode / 4-5-6 in progress mode) then
        an interruptible pause (longer when mana is low). Both the sequence and the
        pause break IMMEDIATELY the instant a counter appears. Returns True on
        completion, False if stopped. gate_power=False (writs) skips the low-mana
        pause and barrels forward to finish the order (owner rule)."""
        timings = self.cfg.get("timings", {})
        poll = max(0.05, 1.0 / float((self.cfg.get("reaction", {}) or {}).get("poll_hz", 6)))
        await self._focus_craft()
        # capture the reaction-button references FRESH for THIS craft (no saved lib)
        self._ref_buttons = await self._ex(sensors.capture_buttons, self.guest, self.cfg)
        self._filler_i = 0
        self._last_counter = None
        got = sum(1 for b in self._ref_buttons if b is not None)
        if got:
            self.t.push_log(self.id, f"captured {got} reaction-button references")
        self.t.update_bot(self.id, state="crafting")
        cycle_start = time.time()
        max_t = float(timings.get("max_craft_time", 90.0))   # safety: bail if it never completes
        # Completion (owner): a craft-DONE button (repeat ↻ / Begin / Create) reappears.
        # saw_active guards it — we only complete once we've SEEN the craft running (those
        # buttons absent = reaction arts on the bar). So the just-clicked start button or a
        # stale done-state can't false-complete, and a craft that never started can't either
        # (it just times out at max_t instead of spamming).
        saw_active = False
        while not self._stop.is_set() and time.time() - cycle_start < max_t:
            await self._wait_unpaused()
            if self._stop.is_set():
                return False
            await self._ex(self.guest.grab)
            # RUNNING (red stop sign) -> mark active. DONE (repeat ↻ / Begin / Create back)
            # AFTER it was running -> craft ended (success or fail).
            if await self._ex(sensors.craft_running, self.guest, self.cfg):
                saw_active = True
            elif saw_active and await self._ex(sensors.craft_done, self.guest, self.cfg):
                return True
            mode = await self._ex(sensors.durability_mode, self.guest, self.cfg) or "progress"
            self.t.update_bot(self.id, durability_mode=mode)
            if await self._counter(mode):
                continue
            # No counter: press the NEXT filler art, ROTATING through this mode's 3 so
            # all get used (1-2-3 / 4-5-6), then WATCH for a counter CONTINUOUSLY for the
            # spam interval and fire it the instant it appears (owner: never miss one).
            arts = self._arts(mode)
            if arts:
                key = arts[self._filler_i % len(arts)]
                self._filler_i += 1
                await self._press(key, f"art:{mode}")
            interval = float(timings.get("art_interval", 0.5))
            if gate_power and not await self._ex(sensors.power_ok, self.guest, self.cfg):
                self.t.update_bot(self.id, power_gated=True)     # low mana (single craft): hold, keep watching
                interval = float(timings.get("pause_low_mana", 2.5))
            else:
                self.t.update_bot(self.id, power_gated=False)
            t1 = time.time()
            while time.time() - t1 < interval and not self._stop.is_set():
                if await self._counter(mode):                    # counter handled the instant it shows
                    break
        return False

    async def _craft_recipe(self, name: str, count: int, trade_class: str,
                            item_idx: int = 0, item_total: int = 0,
                            gate_power: bool = True) -> int:
        timings = self.cfg.get("timings", {})
        self.t.update_bot(self.id, state="selecting", recipe=name,
                          count={"done": 0, "total": count},
                          item={"idx": item_idx, "total": item_total})
        if not await self._select_recipe(name, trade_class):
            return 0                              # bailed (chat-unsafe / not in-world)
        self.t.push_log(self.id, f"recipe selected: {name} — running {count} craft(s)")
        begin = (self.cfg.get("begin", {}) or {}).get("click")
        create = (self.cfg.get("create", {}) or {}).get("click")
        repeat = (self.cfg.get("repeat", {}) or {}).get("click")

        attempts = int(self.cfg.get("recipe_select", {}).get("start_attempts", 4))
        done = 0
        while done < count and not self._stop.is_set():
            # START the craft and CONFIRM it's RUNNING (red stop sign). If it's not
            # running, click the start button AGAIN (owner: "click begin again").
            started = False
            for _ in range(attempts):
                if self._stop.is_set():
                    return done
                if done == 0:
                    clk, label = create, "create"   # first craft: Begin if up, else Create
                    t0 = time.time()
                    while time.time() - t0 < 2.5 and not self._stop.is_set():
                        await self._ex(self.guest.grab)
                        if await self._ex(sensors.begin_or_retry, self.guest, self.cfg):
                            clk, label = begin, "begin"
                            break
                        await asyncio.sleep(0.3)
                else:
                    clk, label = repeat, "repeat"   # repeats: the green-↻ button
                if not clk:
                    break
                self.t.push_log(self.id, f"{label} -> start craft {done + 1}/{count}")
                await self._ex(partial(self.guest.click, clk[0], clk[1], True))
                await asyncio.sleep(float(timings.get("post_begin", 0.25)))
                t1 = time.time()
                while time.time() - t1 < float(timings.get("running_timeout", 3.0)) \
                        and not self._stop.is_set():
                    await self._ex(self.guest.grab)
                    if await self._ex(sensors.craft_running, self.guest, self.cfg):
                        started = True
                        break
                    await asyncio.sleep(0.3)
                if started:
                    break
                self.t.push_log(self.id, "not running (no stop sign) — clicking start again")
            if not started:
                self.t.push_log(self.id, f"couldn't start craft {done + 1}/{count} — stopping")
                break
            if await self._craft_cycle(gate_power=gate_power):
                self.t.push_log(self.id, f"craft {done + 1}/{count} complete")
                done += 1
                self.t.update_bot(self.id, count={"done": done, "total": count},
                                  crafts_done=self.t.bot(self.id)["crafts_done"] + 1)
                self.t.push_event(self.id, "craft", f"{name} {done}/{count}")
            else:
                self.t.push_log(self.id, f"craft {done + 1}/{count} didn't complete — stopping")
                break
        return done

    # -- job runner --------------------------------------------------------
    async def _run_job(self, job: dict) -> None:
        # Require EQ2 up + IN-WORLD. Use game_present (screenshot: the self power bar) —
        # the guest-exec path (eq2_running) intermittently returns empty and false-bailed
        # the job. The screenshot check is reliable and also confirms we're in-world.
        await self._ex(self.guest.grab)
        if not await self._ex(sensors.game_present, self.guest, self.cfg):
            self.t.update_bot(self.id, state="idle")
            self.t.push_log(self.id, "not in-world (no HUD) — Launch first, then Start")
            self.t.push_event(self.id, "control", "start ignored (not in-world)")
            return
        tc = job["trade_class"]
        self.t.update_bot(self.id, started_at=time.time(), reactions=0, crafts_done=0)
        if job["mode"] == "writ":
            q = job["queue"]
            self.t.update_bot(self.id, queue=q)
            for i, it in enumerate(q, 1):
                if self._stop.is_set():
                    break
                made = await self._craft_recipe(it["name"], it["count"], tc, i, len(q),
                                                gate_power=False)   # writs barrel forward
                it["done"] = made                # ACTUAL crafts done, not assumed
                self.t.update_bot(self.id, queue=q)
            self.t.push_event(self.id, "craft", "batch complete")
        else:
            await self._craft_recipe(job["recipe"], job["count"], tc)
            self.t.push_event(self.id, "craft", "done")
        self.t.update_bot(self.id, state="done", durability_mode=None)

    async def run(self) -> None:
        """Supervisor: idle until a job arrives, run it, idle again. A new start()
        aborts the in-flight job (via _stop) and supersedes it with the pending one."""
        while True:
            await self._new_job.wait()
            self._new_job.clear()
            self._stop.clear()                    # fresh run for the pending job
            job = self._pending
            self._pending = None
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
