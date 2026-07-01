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
import os
import re
import time
from functools import partial
from pathlib import Path

import yaml

from . import recipes, sensors
from .guest import Guest
from .recipes import prepare_search, search_name, trade_settings
from .telemetry import ForgeTelemetry

log = logging.getLogger("forge.worker")

WAIT_BUTTON_S = 30.0      # give up waiting for Begin/Retry after this (then idle)

# Woodworker AMMO recipes (batch-craft 100/combine): arrows, crossbow bolts, shuriken, throwing
# axe/dagger/hammer. Whole-word match so it can't catch a non-ammo name. Bows/totems/etc. -> 1.
_AMMO_RE = re.compile(r"\b(?:arrow|bolt|shuriken|throwing)\b", re.IGNORECASE)


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
        self._row_click: list | None = None # last matched recipe row (guest re-selects with it)
        self._filler_i = 0                  # rotating index into the mode's 3 filler arts
        self._last_counter = None           # debounce: counter # we last pressed (None = region clear)

    # -- control (called by the controller) --------------------------------
    def start(self, mode: str, trade_class: str, recipe: str = "",
              count: int = 1, queue: list | None = None, search: str = "", station: str = "",
              writ_mode: str = "standard") -> None:
        if mode == "writ":
            q = []
            for it in (queue or []):
                d = dict(it, done=0)
                try:
                    cnt = max(1, int(d.get("count", 1)))
                except (TypeError, ValueError):
                    cnt = 1
                # If this item was ALREADY collapsed to combines at read time
                # (controller.reads_writ sets writ_count then), `cnt` is already the
                # combine count — do NOT collapse again. Re-collapsing an EVEN combine
                # count double-divides (make-4 -> 2 combines -> 1) and under-crafts.
                if d.get("writ_count"):
                    d["count"] = cnt
                else:
                    comb = self._batch_combines(cnt, trade_class, d.get("name", ""))
                    d["count"] = comb             # what the bot actually combines
                    if comb != cnt:
                        d["writ_count"] = cnt      # remember the EQ2 objective (yield-N per combine)
                q.append(d)
            # station != "" -> craft ONLY that table's recipes this pass (owner reads one writ,
            # crafts table-by-table without deleting/re-reading). "" / "all" = whole queue.
            self._pending = {"mode": "writ", "trade_class": trade_class, "queue": q,
                             "station": "" if station in ("", "all") else station,
                             "writ_mode": writ_mode or "standard"}
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
    def _batch_combines(self, count: int, trade_class: str, recipe_name: str = "") -> int:
        """Some recipes batch-craft: ONE combine yields N items, so a writ objective of N is N/yield
        combines, not N. Per owner: alchemist consumable writs yield 10 ('make 10' = 1 craft);
        PROVISIONER food yields 2. WOODWORKER is MIXED — AMMO (arrows / crossbow bolts / shuriken /
        throwing weapons) batch 100 per combine, while bows/totems/etc. make 1 — so woodworker yield
        is decided PER RECIPE by name, not per class. craft.yaml `batch_yield:` MERGES over the
        defaults; `woodworker_ammo` overrides the 100. Only collapses EXACT multiples of the yield
        (100->1, 6->3) so a non-batch writ is never under-crafted."""
        tc = (trade_class or "").lower()
        yields = {"alchemist": 10, "provisioner": 2, **(self.cfg.get("batch_yield") or {})}
        y = int(yields.get(tc, 1))
        if tc == "woodworker":                       # mixed class: ammo batches, everything else = 1
            ammo_y = int((self.cfg.get("batch_yield") or {}).get("woodworker_ammo", 100))
            y = ammo_y if _AMMO_RE.search(recipe_name or "") else 1
        if y > 1 and count >= y and count % y == 0:
            return count // y
        return count

    def _reread_writ_queue(self, tc: str) -> list:
        """Re-OCR the quest journal and resolve it to a fresh queue of what's STILL needed.
        EQ2 decrements the writ objectives as items are made, so a re-read after a craft pass
        shows the REMAINDER. Used by timed-writ auto-complete (occasional craft failures leave
        items, and the whole timed quest fails if not finished). Same resolve + batch-collapse
        as a manual journal read. (Reads the calibrated journal region — the journal/tracker
        must be visible.)"""
        try:
            raw = sensors.ocr_journal(self.guest, self.cfg, tc)
        except Exception:
            return []
        out = []
        for rawname, resolved, verified, count, warn in recipes.resolve_writ(
                raw, flavor_prefixes=self.cfg.get("writ_flavor_prefixes"),
                pristine_items=self.cfg.get("pristine_prefix_items")):
            cnt = max(1, int(count))
            comb = self._batch_combines(cnt, tc, resolved)
            item = {"name": resolved, "count": comb, "done": 0, "verified": verified,
                    "station": recipes.recipe_station(resolved) if verified else "", "search": ""}
            if comb != cnt:
                item["writ_count"] = cnt
            out.append(item)
        return out

    async def _craft_writ_pass(self, q: list, station: str, tc: str) -> None:
        """Craft every in-scope item in a writ queue once, recording actual crafts in it['done']."""
        for i, it in enumerate(q, 1):
            if self._stop.is_set():
                break
            if station and (it.get("station") or "") != station:
                continue                         # different table this pass — leave for later
            if it.get("writ_count"):             # batch recipe: writ wants N, one combine yields N
                self.t.push_log(self.id, f"{it['name']}: batch recipe — writ wants "
                                f"{it['writ_count']}, crafting {it['count']} combine(s)")
            made = await self._craft_recipe(it["name"], it["count"], tc, i, len(q),
                                            gate_power=False,            # writs barrel forward
                                            search=it.get("search", ""))
            it["done"] = made                    # ACTUAL crafts done, not assumed
            self.t.update_bot(self.id, queue=q)

    def _writ_status(self, q: list, station: str) -> None:
        """Final status for a single-pass writ/list: 'done' ONLY if every in-scope recipe
        finished, else 'incomplete' so a 0/6 recipe can't masquerade as done."""
        scope = [it for it in q if not (station and (it.get("station") or "") != station)]
        short = [it for it in scope if int(it.get("done", 0)) < int(it.get("count", 0))]
        if short and not self._stop.is_set():
            names = ", ".join(f"{it['name']} {it.get('done', 0)}/{it['count']}" for it in short)
            self.t.push_event(self.id, "craft", f"batch INCOMPLETE — {names}")
            self.t.update_bot(self.id, state="incomplete", durability_mode=None)
        else:
            self.t.push_event(self.id, "craft", "batch complete" + (f" ({station})" if station else ""))
            self.t.notify(self.id, "Craft cycle complete",
                          f"{len(scope)} recipe(s) done" + (f" ({station})" if station else ""),
                          level="good")
            self.t.update_bot(self.id, state="done", durability_mode=None)

    def _report_failures(self, q: list, station: str) -> None:
        """TRACK-FAILURES: collect every in-scope recipe that didn't fully succeed (made < count)
        into a saved list 'craft-failures-<char>-<ts>' and stash a failure_report on the bot so
        the dashboard can pop a notification when the WHOLE list is done."""
        scope = [it for it in q if not (station and (it.get("station") or "") != station)]
        fails = []
        for it in scope:
            short = int(it.get("count", 0)) - int(it.get("done", 0))
            if short > 0:
                fails.append({"name": it["name"], "count": short,
                              "search": it.get("search", "")})
        if not fails:
            self.t.push_event(self.id, "craft", "track failures: all crafted — nothing failed ✅")
            self.t.update_bot(self.id, failure_report={"list": "", "items": [], "ts": time.time()})
            return
        char = (self.t.bot(self.id) or {}).get("character") or "crafter"
        list_name = self._save_failures_list(char, fails)
        n = sum(f["count"] for f in fails)
        self.t.push_event(self.id, "craft",
                          f"track failures: {n} craft(s) failed across {len(fails)} recipe(s) "
                          f"-> saved list '{list_name}'")
        self.t.update_bot(self.id, failure_report={"list": list_name, "items": fails,
                                                   "ts": time.time()})

    def _save_failures_list(self, char: str, items: list) -> str:
        """Append a 'craft-failures-<char>-<ts>' list to the owner's lists.yaml (read-modify-write,
        matching the dashboard's /api/forgelists format) so it shows up in the saved-lists dropdown
        ready to re-craft. Returns the list name (even if the write fails, so the UI can show it)."""
        safe_char = re.sub(r"[^A-Za-z0-9]", "", str(char)) or "crafter"
        name = f"craft-failures-{safe_char}-{time.strftime('%Y%m%d-%H%M%S')}"
        rows = [{"name": it["name"], "count": int(it["count"]),
                 "search": it.get("search", "")} for it in items]
        try:
            cfg_dir = Path(os.environ.get("IB_FORGE_DIR",
                           Path(__file__).resolve().parent.parent / "config" / "forge"))
            p = cfg_dir / "lists.yaml"
            data = {}
            if p.exists():
                data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            lists = data.get("lists") or {}
            lists[name] = rows
            data["lists"] = lists
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# Named profit-craft lists (dashboard-edited).\n"
                         + yaml.safe_dump(data, sort_keys=True, allow_unicode=True),
                         encoding="utf-8")
        except Exception as e:                       # noqa: BLE001
            self.t.push_log(self.id, f"track failures: could NOT save list ({e})")
        return name

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

    async def _wait_chat_safe(self) -> bool:
        """Block until the chat-safety gate is clear (in-world + chat line uncovered).
        A covered chat line — Windows taskbar drawn over the bottom of EQ2, EQ2 not in
        the foreground, not in-world, or chat open — makes chat_safe() fail. The old
        behavior aborted the type and returned, so the writ loop marched through every
        recipe doing nothing (done=0) and called the writ 'incomplete'. That looks like
        a bot bug when it's actually the fail-safe doing its job, and it silently burns a
        whole writ. Instead: surface a BLOCKED state and WAIT for the human to restore
        fullscreen/focus, then resume exactly where we left off (done counts preserved).
        Returns True once safe, False if the job was stopped/superseded while blocked."""
        await self._ex(self.guest.grab)
        if await self._ex(sensors.chat_safe, self.guest, self.cfg):
            return True
        self.t.update_bot(self.id, state="blocked")
        self.t.push_log(self.id, "BLOCKED: chat-safety failing — EQ2 not in front / taskbar "
                                 "over the chat line / not in-world. Restore fullscreen+focus.")
        self.t.push_event(self.id, "control", "BLOCKED — chat-safety failing (check fullscreen/taskbar)")
        while not self._stop.is_set():
            await asyncio.sleep(1.0)
            await self._ex(self.guest.grab)
            if await self._ex(sensors.chat_safe, self.guest, self.cfg):
                self.t.push_log(self.id, "chat-safety restored — resuming")
                self.t.push_event(self.id, "control", "chat-safety restored — resuming")
                return True
        return False

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
        # What to type: the owner's tuned search if set, else the recipe name. Drop
        # parentheticals + abbreviate each WORD to fit EQ2's ~18-char field — typing a
        # >18 string overran the field and scrambled the input.
        keep_tier = trade_settings(trade_class).get("search_keep_tier", False)   # default: tier off (picker matches it)
        query = prepare_search((search or "").strip() or search_name(name, trade_class), 18,
                               keep_tier=keep_tier)
        sb = rs.get("search_click")
        attempts = int(rs.get("focus_attempts", 3))

        clr = rs.get("clear_click")
        post_search = float(timings.get("post_search", 0.6))
        self.t.push_log(self.id, f"search box <- '{query}'  (OCR-match recipe '{name}')")
        row_click = None
        for i in range(1, attempts + 1):
            if self._stop.is_set():
                return False
            # HARD WINDOW GATE (owner): NEVER click the search field or type a recipe unless the
            # crafting window is actually OPEN. With it closed the clicks + recipe letters land in
            # the WORLD and run the character around hailing/moving. chat_safe only proves in-world,
            # NOT that the window is up — so check craft_window explicitly and STOP the whole job if
            # it's gone (fail-closed; one re-check to avoid a single-frame miss).
            await self._ex(self.guest.grab)
            if not await self._ex(sensors.craft_window_present, self.guest, self.cfg):
                await asyncio.sleep(0.25)
                await self._ex(self.guest.grab)
                if not await self._ex(sensors.craft_window_present, self.guest, self.cfg):
                    self.t.push_event(self.id, "control", "STOPPED — craft window not on screen")
                    self.t.push_log(self.id, "craft window NOT present — refusing to type to the "
                                    "world; stopping (open the crafting station window, then Start)")
                    self.t.notify(self.id, "Table not targeted",
                                  "craft window not on screen — open the station, then Start",
                                  level="error")
                    self._stop.set()
                    self.t.update_bot(self.id, state="error", durability_mode=None)
                    return False
            # clear the box first (the X) so stale/previous text doesn't corrupt the
            # query (owner-required; EQ2's field keeps the last search).
            if rs.get("use_clear") and clr:
                await self._ex(partial(self.guest.click, clr[0], clr[1], True))
                await asyncio.sleep(click_settle)
            # chat-safety gate: never type unless in-world + chat clear (the invariant).
            # If it's not safe, DON'T silently skip the recipe (that burns the whole writ
            # and reads as a bot bug) — surface BLOCKED and wait for the human to restore
            # fullscreen/focus, then fall through and type this attempt.
            await self._ex(self.guest.grab)
            if not await self._ex(sensors.chat_safe, self.guest, self.cfg):
                self._aborted += 1
                if not await self._wait_chat_safe():
                    return False                  # job stopped/superseded while blocked
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
            # Poll for the recipe to appear in the filtered list. The type fires async
            # (AHK ibrun, fire-and-forget), so these post_search sleeps ALSO give the
            # keystrokes time to land + EQ2 time to filter before we read.
            for _ in range(int(rs.get("match_polls", 5))):
                if self._stop.is_set():        # STOP must bail mid-select, not after it
                    return False
                await asyncio.sleep(post_search)
                row_click = await self._ex(sensors.match_recipe_row, self.guest, self.cfg, name)
                if row_click:
                    break
            if row_click:
                break
            # No match. Now that the box has had time to render, distinguish a FOCUS RACE
            # (query never reached the box -> retype helps) from a genuine not-in-list
            # (wrong name / not in the OCR'd rows). Checked here, NOT right after typing —
            # an immediate check races the async type and false-fails every time.
            await self._ex(self.guest.grab)
            if not await self._ex(sensors.search_landed, self.guest, self.cfg, query):
                self.t.push_log(self.id, f"search '{query}' did NOT land (focus race) — retyping (attempt {i}/{attempts})")
                continue
            # Search landed but no row matched — log what the OCR actually SAW so this is
            # never silent (wrong name vs unfiltered list vs OCR miss).
            seen = await self._ex(sensors.recipe_row_blobs, self.guest, self.cfg)
            self.t.push_log(self.id, f"'{name}' not in filtered list (attempt {i}/{attempts}) "
                                     f"— rows seen: {seen or '[]'} — retrying")
        if not row_click:
            self.t.push_log(self.id, f"recipe '{name}' not matched after {attempts} tries — skipping")
            self.t.notify(self.id, "Recipe not found",
                          f"OCR failed to find a valid recipe for '{name}'", level="error")
            return False
        self._row_click = list(row_click)            # remember the row so the guest can RE-SELECT
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
        self.t.notify(self.id, "Recipe won't load",
                      f"'{name}' matched but wouldn't open — skipping", level="error")
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
        """Between crafts: if mana is low, press the keymap mana-recover hotkey and WAIT
        until mana is back (owner: recover, then continue). No-op when mana is fine, so
        it's safe to call before every craft. Bounded so a missing/forgotten key can't
        hang the list forever (gives up after mana_wait and presses on)."""
        await self._ex(self.guest.grab)
        if await self._ex(sensors.power_ok, self.guest, self.cfg):
            return
        mk = (self.keymap.get("mana_recover") or "").strip()
        timings = self.cfg.get("timings", {})
        if mk:
            self.t.push_log(self.id, "low mana between crafts -> recover")
            self.t.update_bot(self.id, state="waiting_power", power_gated=True)
            await self._press(mk, "mana recover")
        else:
            self.t.push_log(self.id, "low mana, no recover key set -> waiting for mana")
            self.t.update_bot(self.id, state="waiting_power", power_gated=True)
        t0 = time.time()
        wait = float(timings.get("mana_wait", 25.0))
        while time.time() - t0 < wait and not self._stop.is_set():
            await asyncio.sleep(1.0)
            await self._ex(self.guest.grab)
            if await self._ex(sensors.power_ok, self.guest, self.cfg):
                self.t.update_bot(self.id, power_gated=False)
                return
        self.t.update_bot(self.id, power_gated=False)       # timed out -> press on anyway

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
            "art_interval": float(timings.get("art_interval", 1.0)),   # ~1s between FILLER arts (owner spec)
            # counter handling: press the art counter_max_presses times, then back to filler
            "counter_press_interval": float((c.get("reaction", {}) or {}).get("press_interval", 0.12)),
            "counter_max_presses": int((c.get("reaction", {}) or {}).get("counter_presses", 3)),
            "green_delta": float((c.get("reaction", {}) or {}).get("green_delta", 16.0)),
            "red_delta": float((c.get("reaction", {}) or {}).get("red_delta", 16.0)),
            "loop_sleep": float(timings.get("agent_loop_sleep", timings.get("loop_sleep", 0.04))),
            "done_check_interval": float(timings.get("done_check_interval", 0.5)),
            "max_craft_time": float(timings.get("max_craft_time", 90.0)),
            # Completion is gated on the EQ2 log's "You created …" line (authoritative),
            # not the Create/Begin pixel (which also shows pre-start -> false DONEs). The
            # guest finds the active char's eq2log itself; override the root if non-standard.
            "done_via_log": bool((c.get("done_detect", {}) or {}).get("via_log", True)),
            "eq2_log_root": (c.get("eq2_log", {}) or {}).get("dir", ""),
            # craft-window presence gate (the always-there top-right glyph strip). Defaults
            # are baked into the reflex; override region/threshold via craft.yaml craft_window.
            "craft_window": c.get("craft_window", {}) or {},
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
                # Reflex ended WITHOUT completing (state back to idle, not done) = the craft
                # never went active — recipe couldn't start (missing materials / Begin disabled).
                # Skip rather than false-complete (the chemistry 'rail').
                if st.get("state") == "idle" and time.time() - t0 > 5.0:
                    self._agent_set("idle")
                    self.t.push_log(self.id, "craft never went active (no reactions — missing "
                                             "materials / didn't start) — skipping recipe")
                    self.t.notify(self.id, "Unable to start crafting",
                                  "craft never went active (missing materials?)", level="error")
                    return False
            if not st.get("alive"):              # agent died mid-craft -> finish host-side
                self.t.push_log(self.id, "agent went silent mid-craft — taking over host-side")
                self._agent_set("idle")
                return await self._craft_cycle()
        self._agent_set("idle")
        return False

    # -- FAST PATH: whole list run IN-GUEST (no host round-trip per craft) --
    def _guest_loop_ok(self) -> bool:
        """Use the in-guest list loop only if enabled AND the agent is alive."""
        if not bool(self.cfg.get("guest_loop", False)):
            return False
        st = self._agent_get() or {}
        return bool(st.get("alive"))

    def _ruleset_craft_run(self, count: int, name: str = "") -> dict:
        """The 'react' ruleset PLUS what the guest needs to start each craft locally:
        the start-button click points + presence pixels + running detection + count."""
        c = self.cfg
        timings = c.get("timings", {}) or {}
        rs = self._ruleset()
        rs.update({
            "count": int(count),
            "expected_item": name,        # log-match the created item to this recipe (precise)
            # the matched recipe row, so the guest can RE-SELECT before each continuation
            # combine (owner: a multi-count recipe must be re-clicked or Begin won't start it)
            "select_click": self._row_click,
            "create": c.get("create", {}) or {},
            "repeat": c.get("repeat", {}) or {},
            "running_detect": c.get("running_detect", {}) or {},
            "start": {
                "attempts": int((c.get("recipe_select", {}) or {}).get("start_attempts", 4)),
                "confirm_timeout": float(timings.get("confirm_timeout", 6.0)),
                "poll": float(timings.get("poll", 0.12)),
                "post_begin": float(timings.get("post_begin", 0.25)),
                "post_select": float(timings.get("post_select", 0.4)),   # wait after re-select for Begin to relight
                # Begin reappears a beat after a craft completes (↻ flash first) -> wait longer
                "begin_detect": float(timings.get("guest_begin_detect", 4.0)),
            },
            # mouse-to-safe-spot: clicked IN-GUEST AFTER Begin so the art keys land in the
            # focused craft window (owner's order: click start, THEN mouse to safe spot).
            "safe_click": c.get("craft_focus_click"),
            "post_focus": float(timings.get("post_focus", 0.15)),
        })
        return rs

    async def _craft_list_via_agent(self, name: str, count: int) -> int:
        """Hand the full recipe (count crafts) to the in-guest run_list loop and poll
        its progress. The guest does start->react->repeat locally. Returns crafts done.
        The guest also does the safe-spot focus AFTER each Begin (owner's order). This
        pre-handoff _focus_craft stays as harmless insurance until the guest update lands."""
        if self._stop.is_set():
            return 0                              # a stop landed during select — don't (re)start the guest
        await self._focus_craft()
        epoch = self._agent_set("craft_run", **self._ruleset_craft_run(count, name))
        self.t.update_bot(self.id, state="crafting")
        self.t.push_log(self.id, f"handed LIST to in-guest agent — {count} craft(s) (epoch {epoch})")
        watchdog = float(self.cfg.get("timings", {}).get("max_craft_time", 90.0)) + 20.0
        base_crafts = self.t.bot(self.id)["crafts_done"]
        last_done = 0
        t0 = time.time()
        deadline = t0 + watchdog
        while not self._stop.is_set() and time.time() < deadline:
            await self._wait_unpaused()
            await asyncio.sleep(0.3)
            st = self._agent_get() or {}
            if int(st.get("epoch", -1)) != epoch:
                continue
            self.t.update_bot(self.id, reactions=int(st.get("reactions", 0) or 0))
            cd = int(st.get("crafts_done", 0) or 0)
            if cd > last_done:                    # only move FORWARD (ignore stale/idle 0s)
                last_done = cd
                self.t.push_log(self.id, f"craft {cd}/{count} complete (in-guest)")
                self.t.update_bot(self.id, count={"done": cd, "total": count},
                                  crafts_done=base_crafts + cd)
                self.t.push_event(self.id, "craft", f"{name} {cd}/{count}")
                deadline = time.time() + watchdog
            if st.get("done") or (st.get("state") == "idle" and time.time() - t0 > 3.0):
                self._agent_set("idle")
                if last_done < count:
                    self.t.push_log(self.id, f"in-guest list ended at {last_done}/{count}")
                return last_done
            if not st.get("alive"):
                self.t.push_log(self.id, "agent went silent mid-list — stopping")
                self._agent_set("idle")
                return last_done
        self._agent_set("idle")
        self.t.push_log(self.id, f"in-guest list watchdog timeout at {last_done}/{count}")
        return last_done

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
        # FAST PATH: hand the whole begin->react->repeat loop to the in-guest agent
        # (local clicks + ~5ms grabs, no virsh round-trip per craft). Host only selected.
        if self._guest_loop_ok():
            return await self._craft_list_via_agent(name, count)
        begin = (self.cfg.get("begin", {}) or {}).get("click")
        create = (self.cfg.get("create", {}) or {}).get("click")
        repeat = (self.cfg.get("repeat", {}) or {}).get("click")

        attempts = int(self.cfg.get("recipe_select", {}).get("start_attempts", 4))
        done = 0
        while done < count and not self._stop.is_set():
            await self._recover_mana()            # low mana -> recover + wait, then craft (no-op if fine)
            if self._stop.is_set():
                return done
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
                    poll = float(timings.get("poll", 0.15))
                    while time.time() - t0 < float(timings.get("begin_detect", 1.5)) and not self._stop.is_set():
                        await self._ex(self.guest.grab)
                        if await self._ex(sensors.begin_or_retry, self.guest, self.cfg):
                            clk, label = begin, "begin"
                            break
                        await asyncio.sleep(poll)
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
                    await asyncio.sleep(float(timings.get("poll", 0.15)))
                if started:
                    break
                self.t.push_log(self.id, "not running (no stop sign) — clicking start again")
            if not started:
                self.t.push_log(self.id, f"couldn't start craft {done + 1}/{count} — stopping")
                self.t.notify(self.id, "Unable to start crafting",
                              f"'{name}' wouldn't begin (materials? Begin disabled?)", level="error")
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
            station = job.get("station", "")
            writ_mode = job.get("writ_mode", "standard")
            self.t.update_bot(self.id, queue=q)
            await self._craft_writ_pass(q, station, tc)

            # TRACK FAILURES (owner): for any list (writ / saved / hand-built), collect everything
            # that didn't fully succeed (made < count -> the reflex confirms each craft via the log)
            # into a saved list 'craft-failures-<char>-<ts>' and pop a notification when the WHOLE
            # list is done, so a stray failure doesn't silently sink the run.
            if writ_mode == "track_failures":
                self._report_failures(q, station)
                self._writ_status(q, station)
                return

            # TIMED WRIT (owner): occasional craft failures leave items on the writ, and the whole
            # timed quest fails if not finished. Re-read the journal after a short delay and craft
            # whatever still remains, looping until the journal is clear (or the round cap). EQ2
            # updates the objective counts live, so the re-read shows only the remainder.
            cleared = None
            if writ_mode == "timed":
                cleared = False
                delay = float(self.cfg.get("timed_writ_delay", 3.0))
                rounds = int(self.cfg.get("timed_writ_max_rounds", 12))
                # An EMPTY re-read means either the writ is genuinely done OR the journal
                # OCR just failed/blipped (tracker not visible for that frame). We must NOT
                # treat a single empty read as "complete" — that was the bug where the retry
                # never ran and a still-unfinished writ got reported done. Require N
                # CONSECUTIVE empty reads before concluding the journal is clear.
                confirm = max(1, int(self.cfg.get("timed_writ_confirm_empties", 2)))
                empty_streak = 0
                retried = False
                for rnd in range(1, rounds + 1):
                    if self._stop.is_set():
                        break
                    await asyncio.sleep(delay)               # owner: ~3s before re-reading the OCR
                    if self._stop.is_set():
                        break
                    rq = await self._ex(self._reread_writ_queue, tc)
                    if station:
                        rq = [it for it in rq if (it.get("station") or "") in ("", station)]
                    rq = [it for it in rq if int(it.get("count", 0)) > 0]
                    if not rq:
                        empty_streak += 1
                        if empty_streak >= confirm:
                            cleared = True
                            self.t.push_event(self.id, "craft", "timed writ: journal clear — complete")
                            break
                        self.t.push_event(self.id, "craft",
                                          f"timed writ: re-read empty ({empty_streak}/{confirm}) "
                                          f"— re-checking before calling it done")
                        continue                             # re-read again; don't false-complete
                    empty_streak = 0                         # got a real read -> reset
                    self.t.push_event(self.id, "craft",
                                      f"timed writ: re-read found {len(rq)} remaining "
                                      f"(round {rnd}/{rounds}) — crafting")
                    if not retried:                          # first recovery pass -> notify the owner
                        retried = True
                        self.t.notify(self.id, "Timed writ retry",
                                      f"{len(rq)} recipe(s) still on the writ — re-crafting",
                                      level="warn")
                    self.t.update_bot(self.id, queue=rq)
                    await self._craft_writ_pass(rq, station, tc)
                    q = rq                                   # for the final status check

            # Status must reflect reality: 'done' ONLY if every in-scope recipe finished;
            # else 'incomplete' so a 0/6 recipe (missing mats / failed start) can't
            # masquerade as done. (Owner: "finished one, other still to go, says done — wtf".)
            if cleared is True:
                self.t.push_event(self.id, "craft", "timed writ COMPLETE" + (f" ({station})" if station else ""))
                self.t.notify(self.id, "Craft cycle complete",
                              ("timed writ done — recovered from a failure" if retried
                               else "timed writ done") + (f" ({station})" if station else ""),
                              level="good")
                self.t.update_bot(self.id, state="done", durability_mode=None)
                return
            if cleared is False and not self._stop.is_set():
                self.t.push_event(self.id, "craft", "timed writ: still incomplete after retries — check it")
                self.t.notify(self.id, "Timed writ incomplete",
                              "still items left after retries — check it", level="error")
                self.t.update_bot(self.id, state="incomplete", durability_mode=None)
                return
            self._writ_status(q, station)
        else:
            made = await self._craft_recipe(job["recipe"], job["count"], tc,
                                            search=job.get("search", ""))
            full = made >= int(job.get("count", 1))
            self.t.push_event(self.id, "craft", "done" if full else f"incomplete — {made}/{job.get('count',1)}")
            if full:
                self.t.notify(self.id, "Craft cycle complete",
                              f"{job.get('recipe','recipe')} ×{made} done", level="good")
            self.t.update_bot(self.id, state="done" if full else "incomplete", durability_mode=None)

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
