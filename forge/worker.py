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
                 tele: ForgeTelemetry, bot_id: str, keymap: dict | None = None,
                 agent_set=None, agent_get=None) -> None:
        self.guest = guest
        self.cfg = profile                  # craft.yaml dict (calibration)
        self.profile_dir = profile_dir
        self.t = tele
        self.id = bot_id
        self.keymap = keymap or {}          # camp + arts keys (owner-configurable)
        # in-guest reflex agent hooks (set by the controller): agent_set(action,**p)->epoch
        # hands the running craft's reaction loop to the guest; agent_get()->status dict.
        self._agent_set = agent_set
        self._agent_get = agent_get
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
              count: int = 1, queue: list | None = None, search: str = "") -> None:
        if mode == "writ":
            q = [dict(it, done=0) for it in (queue or [])]
            self._pending = {"mode": "writ", "trade_class": trade_class, "queue": q}
        else:
            self._pending = {"mode": "single", "trade_class": trade_class,
                             "recipe": recipe or "", "search": search or "",
                             "count": max(1, int(count or 1))}
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

    async def _press(self, seq: str, role: str = "craft", gated: bool = True) -> bool:
        """THE chat-safety gate (fail-closed) wrapping every keypress. Single-key craft
        arts (1-6) go via virsh send-key (type_text): SYNCHRONOUS + instant, so a counter
        actually lands in its window — ibkey is an async scheduled task that fires LATE
        and DROPS when filler+counter presses collide. Complex keys (modifiers/F-keys,
        e.g. mana-recover/camp) still use ibkey (send-key can't express them).

        gated=True grabs + checks chat_safe here. gated=False SKIPS that (the craft loop
        already verified chat_safe off its single per-iteration frame) — avoids a second
        ~170ms grab per keypress so counters land fast."""
        if gated:
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
    async def _select_recipe(self, name: str, trade_class: str, search: str = "") -> bool:
        """Focus the search field, type the SEARCH text, filter, click the matching
        row's icon, then park focus on the craft window. Returns False on failure.

        Two-field design (owner): `name` is the full recipe name used for OCR row-
        matching; `search` is what's actually typed into the box (owner-tuned around
        EQ2's search-field length limit). When `search` is empty we type the name.

        Focusing the EQ2 search box is intermittent (a single ibgclick sometimes only
        activates the window, not the field). So we focus+type+verify in a RETRY loop:
        the proof of focus is that the result list FILTERED (match_recipe_row finds the
        recipe). The focus-click WAITS for the click to land (wait=True) so we never
        type into a not-yet-focused box, and typing is gated on chat-safety. Only after
        a verified match do we click the row icon to select it."""
        rs = self.cfg.get("recipe_select", {})
        timings = self.cfg.get("timings", {})
        click_settle = float(timings.get("click_settle", 0.8))
        # owner-tuned search text (capped to EQ2's 18-char field), else the full name
        # (EQ2 truncates that itself; we still OCR-match the row on the full `name`).
        query = (search or "").strip()[:18] or search_name(name, trade_class)
        sb = rs.get("search_click")
        attempts = int(rs.get("focus_attempts", 3))

        clr = rs.get("clear_click")
        post_search = float(timings.get("post_search", 0.6))
        self.t.push_log(self.id, f"search box <- '{query}'  (OCR-match recipe '{name}')")
        row_click = None
        for i in range(1, attempts + 1):
            if self._stop.is_set():
                return False
            # clear the box first (the X) so stale/previous text doesn't corrupt the
            # query (owner-required; EQ2's field keeps the last search).
            if rs.get("use_clear") and clr:
                await self._ex(partial(self.guest.click, clr[0], clr[1], True))
                await asyncio.sleep(click_settle)
            # chat-safety gate: never type unless in-world + chat clear (the invariant)
            await self._ex(self.guest.grab)
            if not await self._ex(sensors.chat_safe, self.guest, self.cfg):
                self._aborted += 1
                self.t.push_log(self.id, "recipe type ABORTED (chat unsafe / not in-world)")
                return False
            # ATOMIC: activate EQ2 + click the search field + type + Enter, all in ONE
            # AHK run. Fusing focus-click+type+Enter means nothing can steal focus
            # between them, so the recipe letters can't leak into the world as movement
            # (that race — split ibgclick-then-type — is what ran the character into the
            # wall). Enter lives INSIDE the focused run, so it filters the list (owner
            # spec) and can't open chat in a normal focus. chat_safe above already
            # proved in-world; if focus fails the next art press is chat_safe-gated too.
            if sb:
                await self._ex(partial(self.guest.type_field, query, True, (sb[0], sb[1])))
            else:
                await self._ex(self.guest.type_field, query, True)
            # Proof the search worked = the row appears in the filtered list. Poll for it.
            for _ in range(int(rs.get("match_polls", 5))):
                await asyncio.sleep(post_search)
                row_click = await self._ex(sensors.match_recipe_row, self.guest, self.cfg, name)
                if row_click:
                    break
            if row_click:
                break
            # row not found — re-clear + re-type (atomic focus again) and try once more.
            self.t.push_log(self.id, f"'{name}' not in filtered list (attempt {i}/{attempts}) — retrying")
        if not row_click:
            self.t.push_log(self.id, f"recipe '{name}' not matched after {attempts} tries — skipping")
            return False
        # DOUBLE-click the row icon to LOAD, then VERIFY it loaded (Begin/Create appears).
        # If it didn't load, re-find the row and click again. (ibgclick single-click only
        # highlights; double loads. The owner's single mouse-click also loads.)
        for attempt in range(int(rs.get("load_attempts", 3))):
            if self._stop.is_set():
                return False
            self.t.push_log(self.id, f"select recipe -> double-click {row_click}")
            await self._ex(partial(self.guest.double_click, row_click[0], row_click[1]))
            await asyncio.sleep(float(timings.get("post_select", 0.3)))
            t0 = time.time()
            while time.time() - t0 < 2.0 and not self._stop.is_set():
                await self._ex(self.guest.grab)
                if await self._ex(sensors.begin_or_retry, self.guest, self.cfg):
                    return True                  # loaded — Begin/Create is up
                await asyncio.sleep(0.3)
            self.t.push_log(self.id, "recipe didn't load — re-finding the row")
            r2 = await self._ex(sensors.match_recipe_row, self.guest, self.cfg, name)
            if r2:
                row_click = r2
        self.t.push_log(self.id, f"recipe '{name}' didn't load — skipping")
        return False

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
    async def _counter(self, mode: str, safe: bool) -> bool:
        """If a counter is showing in the watch area, press its key. Reads the LAST
        grabbed frame (fresh=False) so the caller's single per-iteration screenshot
        feeds it. Dino-style: NO debounce — press the matching key whenever the counter
        is visible. The art is on cooldown after it fires, so re-pressing while the icon
        lingers is harmless, and it guarantees we never miss the window. Returns True
        if a counter was detected (so the caller skips filler this iteration)."""
        n = await self._ex(partial(sensors.reaction_event, self.guest, self.cfg,
                                   self._ref_buttons, False))
        if not n:
            return False
        key = self._counter_key(mode, n)
        if key and safe:
            await self._press(key, f"counter{n}:{mode}", gated=False)
            if n != self._last_counter:          # count + log only on a NEW counter (not every re-press)
                self.t.update_bot(self.id, reactions=self.t.bot(self.id)["reactions"] + 1)
                self.t.push_log(self.id, f"counter#{n} ({mode}) -> {key}")
        self._last_counter = n
        return True                              # counter present -> handled (skip filler)

    # -- react: hand off to the in-guest agent, else fall back host-side -----
    def _agent_alive(self) -> bool:
        if not (self._agent_set and self._agent_get):
            return False
        try:
            return bool(self._agent_get().get("alive"))
        except Exception:                        # noqa: BLE001
            return False

    def _ruleset(self) -> dict:
        """The reaction config the guest agent needs (from craft.yaml + keymap). Sent in
        the 'react' command so the guest holds no state; one source of truth on the host."""
        c = self.cfg
        timings = c.get("timings", {}) or {}
        return {
            "reaction": c.get("reaction", {}) or {},
            "durability_mode": c.get("durability_mode", {}) or {},
            "done_detect": c.get("done_detect", {}) or {},
            "begin": c.get("begin", {}) or {},
            "retry": c.get("retry", {}) or {},
            "game_present": c.get("game_present", {}) or {},
            "chat_input": c.get("chat_input", {}) or {},
            "arts": {"durability": self._arts("durability"), "progress": self._arts("progress")},
            "debug": bool((c.get("reaction", {}) or {}).get("debug", False)),
            "loop_sleep": float(timings.get("agent_loop_sleep", timings.get("loop_sleep", 0.03))),
            "done_check_interval": float(timings.get("done_check_interval", 0.5)),
            "max_craft_time": float(timings.get("max_craft_time", 90.0)),
        }

    async def _react(self, gate_power: bool = True) -> bool:
        """React to counters until the craft completes. Prefer the IN-GUEST agent (fast
        local mss+cv2 loop, tens-of-ms reactions); fall back to the host-side loop if the
        agent isn't alive. The host has already confirmed the craft is RUNNING."""
        if self._agent_alive():
            return await self._react_via_agent()
        return await self._craft_cycle(gate_power=gate_power)

    async def _react_via_agent(self) -> bool:
        """Hand the running craft to the guest agent and wait for its done signal."""
        await self._focus_craft()                # ensure EQ2 has focus before the agent presses
        epoch = self._agent_set("react", **self._ruleset())
        self.t.update_bot(self.id, state="crafting")
        self.t.push_log(self.id, f"handed craft to in-guest agent (epoch {epoch})")
        max_t = float(self.cfg.get("timings", {}).get("max_craft_time", 90.0)) + 15.0
        t0 = time.time()
        while not self._stop.is_set() and time.time() - t0 < max_t:
            await self._wait_unpaused()
            await asyncio.sleep(0.2)
            st = self._agent_get() or {}
            if int(st.get("epoch", -1)) == epoch:
                self.t.update_bot(self.id, reactions=int(st.get("reactions", 0) or 0))
                if st.get("done"):
                    self._agent_set("idle")
                    return True
            if not st.get("alive"):              # agent died mid-craft -> finish host-side
                self.t.push_log(self.id, "agent went silent mid-craft — taking over host-side")
                self._agent_set("idle")
                return await self._craft_cycle()
        self._agent_set("idle")
        return False

    # -- one craft cycle (HOST-SIDE fallback) -------------------------------
    async def _craft_cycle(self, gate_power: bool = True) -> bool:
        """Counter-FIRST loop until the craft completes. When no counter is up, send
        the filler sequence (1-2-3 in durability mode / 4-5-6 in progress mode) then
        an interruptible pause (longer when mana is low). Both the sequence and the
        pause break IMMEDIATELY the instant a counter appears. Returns True on
        completion, False if stopped. gate_power=False (writs) skips the low-mana
        pause and barrels forward to finish the order (owner rule)."""
        timings = self.cfg.get("timings", {})
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
        done_every = float(timings.get("done_check_interval", 0.5))  # finish-check cadence (not timing-critical)
        loop_sleep = float(timings.get("loop_sleep", 0.03))
        # THE DINO MODEL: ONE grab per iteration, every sensor reads that single frame.
        # Counter FIRST and pressed the instant it's seen (the reaction window is short —
        # a virsh grab is ~170ms, so we cannot afford 4-6 grabs/iter or a 1s filler pause).
        # Completion (owner): a craft-DONE button (repeat ↻ / Begin / Create) reappears
        # AFTER we've seen the craft running (saw_active) — guards against the just-clicked
        # start button or a stale state false-completing; a craft that never starts times out.
        saw_active = False
        last_done = 0.0
        while not self._stop.is_set() and time.time() - cycle_start < max_t:
            await self._wait_unpaused()
            if self._stop.is_set():
                return False
            await self._ex(self.guest.grab)                       # the ONE screenshot
            safe = await self._ex(sensors.chat_safe, self.guest, self.cfg)  # gate, off this frame
            mode = await self._ex(sensors.durability_mode, self.guest, self.cfg) or "progress"
            # COUNTER FIRST — press it immediately (no debounce), then loop tight. Skip the
            # filler + finish checks this iteration so the next grab comes ASAP.
            if await self._counter(mode, safe):
                self.t.update_bot(self.id, durability_mode=mode)
                await asyncio.sleep(loop_sleep)
                continue
            # No counter -> fire the next rotating filler art (1-2-3 / 4-5-6).
            self.t.update_bot(self.id, durability_mode=mode)
            if safe:
                arts = self._arts(mode)
                if arts:
                    key = arts[self._filler_i % len(arts)]
                    self._filler_i += 1
                    await self._press(key, f"art:{mode}", gated=False)
            # FINISH check — rate-limited (not timing-critical), off the current frame.
            now = time.time()
            if now - last_done >= done_every:
                last_done = now
                if await self._ex(sensors.craft_running, self.guest, self.cfg):
                    saw_active = True
                    self.t.update_bot(self.id, power_gated=False)
                elif saw_active and await self._ex(sensors.craft_done, self.guest, self.cfg):
                    return True
            await asyncio.sleep(loop_sleep)
        return False

    async def _craft_recipe(self, name: str, count: int, trade_class: str,
                            item_idx: int = 0, item_total: int = 0,
                            gate_power: bool = True, search: str = "") -> int:
        timings = self.cfg.get("timings", {})
        self.t.update_bot(self.id, state="selecting", recipe=name,
                          count={"done": 0, "total": count},
                          item={"idx": item_idx, "total": item_total})
        if not await self._select_recipe(name, trade_class, search):
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
            for attempt in range(attempts):
                if self._stop.is_set():
                    return done
                if done > 0 and attempt == 0:
                    clk, label = repeat, "repeat"   # repeats: try the green-↻ first
                else:
                    # first craft, or repeat fallback: Begin if it's up, else Create
                    clk, label = create, "create"
                    t0 = time.time()
                    while time.time() - t0 < 2.5 and not self._stop.is_set():
                        await self._ex(self.guest.grab)
                        if await self._ex(sensors.begin_or_retry, self.guest, self.cfg):
                            clk, label = begin, "begin"
                            break
                        await asyncio.sleep(0.3)
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
            if await self._react(gate_power=gate_power):
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
                                                gate_power=False,            # writs barrel forward
                                                search=it.get("search", ""))
                it["done"] = made                # ACTUAL crafts done, not assumed
                self.t.update_bot(self.id, queue=q)
            self.t.push_event(self.id, "craft", "batch complete")
        else:
            await self._craft_recipe(job["recipe"], job["count"], tc,
                                     search=job.get("search", ""))
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
