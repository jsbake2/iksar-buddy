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

from . import recipes, sensors
from .guest import Guest
from .login import LoginDriver, WORLD, load_accounts
from .telemetry import ForgeTelemetry
from .worker import CraftWorker

log = logging.getLogger("forge.controller")


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
        self.accounts, self.world = load_accounts(profile_dir)
        self.guests: dict[str, Guest] = {}
        self.workers: dict[str, CraftWorker] = {}
        self.stations: dict[str, dict] = {}
        self._frames: dict[str, tuple[float, bytes]] = {}   # bot_id -> (ts, jpeg)
        # In-guest reflex agent channel (FORGE.md §agent): the agent polls _agent_cmd
        # (control) and pushes _agent_tele (state + the craft-done handoff signal). Both
        # keyed by bot_id. epoch bumps on every new command so the agent runs it once.
        self._agent_cmd: dict[str, dict] = {}
        self._agent_tele: dict[str, dict] = {}
        self._scribe_mark: dict[str, int] = {}   # per-bot EQ2-log line count at "mark"
        self._shutting_down: set[str] = set()    # bots mid auto-shutdown (don't re-trigger)
        for bot in stations.get("bots", []):
            bid = bot["id"]
            g = Guest(bot["dom"], bot.get("width", 1920), bot.get("height", 1080))
            self.guests[bid] = g
            self.workers[bid] = CraftWorker(
                g, craft_profile, profile_dir, tele, bid, self.keymap,
                agent_set=(lambda action, _b=bid, **p: self.set_agent_command(_b, action, **p)),
                agent_get=(lambda _b=bid: self.agent_status(_b)))
            self.stations[bid] = bot

    def _creds(self, bot_id: str) -> tuple[str, str]:
        dom = self.stations.get(bot_id, {}).get("dom", "")
        a = self.accounts.get(dom) or {}
        return (a.get("user") or ""), (a.get("password") or "")

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
        # Keyed by the VM (the thing that owns the account LOGIN), NOT the character.
        # Two characters on one VM/account is the /camp switch case — re-acquiring our
        # own lock must be idempotent, so the character must not be part of identity.
        return f"forge:{self.stations.get(bid, {}).get('dom', bid)}"

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
        clean = {k: v for k, v in fields.items()
                 if k in ("trade_class", "mode", "recipe", "search")}
        if "shutdown_when_done" in fields:
            clean["shutdown_when_done"] = bool(fields["shutdown_when_done"])
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
              recipe: str = "", count: int = 1, search: str = "", station: str = "") -> None:
        b = self.t.bot(bot_id)
        w = self.workers.get(bot_id)
        if not b or not w or not b["enabled"]:
            return
        if not self._acquire(bot_id):
            self.t.update_bot(bot_id, state="error")
            return
        self._shutting_down.discard(bot_id)        # new job -> re-arm auto-shutdown
        if mode == "writ":
            q = b.get("queue", [])
            w.start("writ", trade_class, queue=q, station=station)
            n = sum(1 for it in q if not station or (it.get("station") or "") == station)
            self.t.push_event(bot_id, "craft",
                              f"writ start ({n} recipes" + (f" @ {station}" if station else "") + ")")
        else:
            recipe = recipe or b.get("recipe", "")   # dashboard shows the saved recipe
            search = search or b.get("search", "")   # owner-tuned search text (empty -> name)
            w.start("single", trade_class, recipe=recipe, count=count, search=search)
            self.t.update_bot(bot_id, search=search)   # persist so the UI/snapshot reflects what we'll type
            self.t.push_event(bot_id, "craft",
                              f"single start: type '{search or recipe}' -> {recipe or '(loaded)'} x{count}")
        self.t.update_bot(bot_id, mode=mode, trade_class=trade_class, state="selecting")

    def stop(self, bot_id: str) -> None:
        w = self.workers.get(bot_id)
        if w:
            w.stop()
        # Halt the IN-GUEST reflex IMMEDIATELY — don't wait for the host worker loop to
        # unwind and send idle itself. The guest is the thing pressing keys; if the worker
        # is mid virsh-roundtrip or between handoffs when STOP is pressed, the VM keeps
        # crafting combines while the dashboard already reads idle. Bumping the command to
        # 'idle' here makes the guest's next poll (~250ms) stop the reflex. (Owner: "STOP
        # does not stop it".)
        self.set_agent_command(bot_id, "idle")
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
            clean.append({"name": name, "count": cnt, "done": 0,
                          "search": str(it.get("search", "")).strip(),
                          "station": str(it.get("station", "")).strip(),
                          "verified": bool(it.get("verified", False))})
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
        raw = await asyncio.get_running_loop().run_in_executor(
            None, partial(sensors.ocr_journal, g, self.cfg_profile, b.get("trade_class", "")))
        # Resolve each OCR'd objective against the scraped DB: confident same-tier matches
        # snap to the canonical recipe; everything else keeps its cleaned name (roman numerals
        # fixed) and is flagged UNVERIFIED — we never substitute a wrong recipe.
        queue, unverified, flagged = [], 0, 0
        for rawname, resolved, verified, count, warn in recipes.resolve_writ(
                raw, flavor_prefixes=self.cfg_profile.get("writ_flavor_prefixes"),
                pristine_items=self.cfg_profile.get("pristine_prefix_items")):
            station = recipes.recipe_station(resolved) if verified else ""
            item = {"name": resolved, "count": count, "done": 0,
                    "verified": verified, "station": station}
            if warn:                       # OCR left an unexpected char -> surface it to fix
                item["warn"] = warn
                flagged += 1
                self.t.push_log(bot_id, f"writ '{rawname}': OCR found unexpected char(s) "
                                        f"'{warn}' — recipes only use ' and (); fix it by hand")
            queue.append(item)
            if verified and rawname.strip() != resolved:
                self.t.push_log(bot_id, f"writ '{rawname}' -> DB '{resolved}'")
            elif not verified:
                unverified += 1
                self.t.push_log(bot_id, f"writ '{rawname}' -> '{resolved}' (NOT in DB — unverified)")
        self.t.update_bot(bot_id, mode="writ", queue=queue)
        msg = f"journal: {len(queue)} recipes" + (f", {unverified} unverified (check them)" if unverified else " (all DB-verified)")
        if flagged:
            msg += f" — ⚠ {flagged} with odd OCR char(s), fix by hand"
        self.t.push_event(bot_id, "ocr", msg)

    def _log_path(self, bot_id: str):
        """(guest, EQ2-log-path) for this bot's selected character, or (g, None)."""
        g = self.guests.get(bot_id)
        char = self._char_for(bot_id)
        if not g or not char:
            return g, None
        cfg = self.cfg_profile.get("eq2_log", {}) or {}
        log_dir = (cfg.get("dir")
                   or r"C:\Users\Public\Daybreak Game Company\Installed Games"
                      r"\EverQuest II\logs").rstrip("\\")
        server = (self.cfg_profile.get("char_select", {}) or {}).get("server", "")
        return g, "\\".join(p for p in (log_dir, server, f"eq2log_{char}.txt") if p)

    # -- scribe capture: mark the log, owner scribes a book, read ONLY the new ----
    def scribe_mark(self, bot_id: str) -> None:
        asyncio.create_task(self._scribe_mark_do(bot_id))

    async def _scribe_mark_do(self, bot_id: str) -> None:
        g, path = self._log_path(bot_id)
        if not path:
            self.t.push_log(bot_id, "scribe: pick a crafter first")
            return
        out = await asyncio.get_running_loop().run_in_executor(
            None, g.exec_ps, f"if(Test-Path '{path}'){{(Get-Content '{path}').Count}}else{{-1}}")
        try:
            n = int((out or "").strip())
        except ValueError:
            n = -1
        if n < 0:
            self.t.push_log(bot_id, f"scribe mark: log not found ({path}) — is /log on?")
            return
        self._scribe_mark[bot_id] = n
        self.t.update_bot(bot_id, scribe_marked=True)
        self.t.push_event(bot_id, "scribe", f"marked log @ {n} lines — scribe the book, then Read scribed")

    def scribe_read(self, bot_id: str) -> None:
        asyncio.create_task(self._scribe_read_do(bot_id))

    async def _scribe_read_do(self, bot_id: str) -> None:
        from .recipes import parse_scribed_recipes
        g, path = self._log_path(bot_id)
        n = self._scribe_mark.get(bot_id)
        if not path or n is None:
            self.t.push_log(bot_id, "scribe read: mark the log first")
            return
        out = await asyncio.get_running_loop().run_in_executor(
            None, g.exec_ps, f"if(Test-Path '{path}'){{Get-Content '{path}' | Select-Object -Skip {n}}}")
        names = list(parse_scribed_recipes(out or "").keys())
        # ALWAYS un-arm (clear the mark + button state) so it can't get stuck armed.
        self._scribe_mark.pop(bot_id, None)
        self.t.update_bot(bot_id, scribe_marked=False)
        if not names:
            self.t.push_event(bot_id, "scribe", "no new scribed recipes since the mark")
            self.t.push_log(bot_id, "scribe read: nothing new (scribe AFTER marking)")
            return
        queue = [{"name": nm, "count": 1, "done": 0, "search": ""} for nm in names]
        self.t.update_bot(bot_id, mode="writ", queue=queue)
        self.t.push_event(bot_id, "scribe", f"{len(queue)} newly-scribed recipes -> queue (Save as… to name it)")

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
        _g, path = self._log_path(bot_id)
        cfg = self.cfg_profile.get("eq2_log", {}) or {}
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
        if not g:
            return
        if not self._acquire(bot_id):
            return
        loop = asyncio.get_running_loop()
        char = self._char_for(bot_id)
        user, pw = self._creds(bot_id)
        if not (user and pw):
            self.t.push_log(bot_id, f"no credentials for {g.dom} (set accounts.yaml)")
            self.t.update_bot(bot_id, state="error"); return
        self.t.update_bot(bot_id, state="launching", vm_running=True)
        self.t.push_event(bot_id, "launch", f"power on {g.dom} -> direct login {char or '?'}")
        drv = LoginDriver(g, lambda m: self.t.push_log(bot_id, m))
        ok = await loop.run_in_executor(
            None, partial(drv.boot_and_login, user, pw, char, self.world))
        self.t.update_bot(bot_id, state="idle")
        if ok:
            # keep the in-guest reflex agent in lockstep with the repo (guests run a
            # persistent C:\ib\agent\craft_reflex.py — without this they silently drift)
            if await loop.run_in_executor(None, g.sync_reflex):
                self.t.push_log(bot_id, "reflex agent synced to current build")
            self.t.push_event(bot_id, "launch", f"in world as {char or '?'}")
        else:
            self.t.push_log(bot_id, f"login did not confirm in-world for {char or '?'}")

    def switch_char(self, bot_id: str) -> None:
        asyncio.create_task(self._switch_char(bot_id))

    async def _switch_char(self, bot_id: str) -> None:
        """Same-account character switch via EQ2 '/camp <name>' (no char-select). The
        target is whatever crafter is selected in the dropdown."""
        g = self.guests.get(bot_id)
        char = self._char_for(bot_id)
        if not g or not char:
            self.t.push_log(bot_id, "switch: no character selected"); return
        if not self._acquire(bot_id):
            return
        self.t.push_event(bot_id, "launch", f"/camp switch -> {char}")
        drv = LoginDriver(g, lambda m: self.t.push_log(bot_id, m))
        ok = await asyncio.get_running_loop().run_in_executor(None, partial(drv.camp_to, char))
        self.t.push_event(bot_id, "launch", f"now {char}" if ok else f"switch to {char} failed")

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

    # -- in-guest reflex agent channel ------------------------------------
    def agent_command(self, bot_id: str) -> dict:
        """What the in-guest agent should be doing right now (it polls this). Default
        idle. The worker sets {action:'react', epoch:N, ...} to hand off a running
        craft's reaction loop to the agent."""
        return self._agent_cmd.get(bot_id) or {"action": "idle", "epoch": 0}

    def set_agent_command(self, bot_id: str, action: str, **params) -> int:
        """Set the agent's command, bumping epoch so the agent runs it exactly once.
        Returns the new epoch (the worker waits for telemetry tagged with it)."""
        cur = self._agent_cmd.get(bot_id) or {"epoch": 0}
        epoch = int(cur.get("epoch", 0)) + 1
        self._agent_cmd[bot_id] = {"action": action, "epoch": epoch, **params}
        return epoch

    def agent_push(self, bot_id: str, data: dict) -> None:
        """Telemetry pushed by the in-guest agent. Stamp arrival time (heartbeat) and
        surface a few live fields to the dashboard. The worker reads agent_status() to
        learn when a handed-off craft finished."""
        rec = dict(data or {})
        rec["ts"] = time.time()
        self._agent_tele[bot_id] = rec
        live = {}
        for k in ("state", "reactions", "durability_mode", "running", "done"):
            if k in rec:
                live[k] = rec[k]
        if "reactions" in live:
            self.t.update_bot(bot_id, reactions=int(live.pop("reactions") or 0))
        if live:
            self.t.update_bot(bot_id, **{k: v for k, v in live.items()
                                         if k in ("durability_mode",)})
        self.t.update_bot(bot_id, agent_seen=rec["ts"])

    def agent_status(self, bot_id: str) -> dict:
        """Last agent telemetry + its age in seconds (alive = age small)."""
        rec = self._agent_tele.get(bot_id)
        if not rec:
            return {"alive": False, "age": None}
        return {**rec, "alive": (time.time() - rec["ts"]) < 5.0,
                "age": time.time() - rec["ts"]}

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

    async def _auto_shutdown(self, bot_id: str) -> None:
        """List finished + 'shutdown when done' set: camp out, then power off the VM.
        Triggered once by the run loop when the bot reaches 'done'."""
        g = self.guests.get(bot_id)
        if not g:
            return
        loop = asyncio.get_running_loop()
        self.t.push_event(bot_id, "control", "list complete -> camp + power off")
        # camp out cleanly (same as the Camp button) before pulling the VM down
        camp = (self.keymap.get("camp") or "Ctrl+-").strip()
        try:
            if camp.startswith("/"):
                ci = (self.cfg_profile.get("chat_input", {}) or {}).get("region")
                if ci:
                    await loop.run_in_executor(None, g.click,
                                               ci["x"] + ci["w"] // 2, ci["y"] + ci["h"] // 2)
                    await asyncio.sleep(0.4)
                await loop.run_in_executor(None, g.type_text, camp, True)
            else:
                await loop.run_in_executor(None, g.press_keys, camp)
            self.t.push_event(bot_id, "control", f"camping ({camp})")
            await asyncio.sleep(float(self.cfg_profile.get("timings", {}).get("camp_wait", 28.0)))
        except Exception as e:                       # noqa: BLE001 — never let it hang the power-off
            self.t.push_log(bot_id, f"camp before shutdown failed ({e}); powering off anyway")
        self._release(bot_id)
        self.t.update_bot(bot_id, state="off")
        self.t.push_event(bot_id, "control", "powering off VM")
        await loop.run_in_executor(None, g.exec_ps,
                                   "Stop-Process -Name EverQuest2 -Force -ErrorAction SilentlyContinue", False)
        await asyncio.sleep(2)
        ok = await loop.run_in_executor(None, g.shutdown_vm)
        self.t.update_bot(bot_id, vm_running=False)
        self.t.push_event(bot_id, "control", "VM powered off" if ok else "VM power-off FAILED")

    # -- supervisor: run worker tasks + refresh held locks ----------------
    async def run(self) -> None:
        tasks = [asyncio.create_task(w.run()) for w in self.workers.values()]
        try:
            tick = 0
            while True:
                # fast (2s): refresh each bot's agent-health flag for the dashboard
                for bid in list(self.workers):
                    b = self.t.bot(bid) or {}
                    seen = b.get("agent_seen")
                    up = bool(seen and (time.time() - seen) < 5.0)
                    if b.get("agent_up") != up:
                        self.t.update_bot(bid, agent_up=up)
                    # auto power-off: list finished + the toggle set -> camp + shut down once
                    if b.get("state") == "done" and b.get("shutdown_when_done") \
                            and bid not in self._shutting_down:
                        self._shutting_down.add(bid)
                        asyncio.create_task(self._auto_shutdown(bid))
                if tick % 15 == 0:                                    # slow (30s): account locks
                    for bid in list(self.workers):
                        acct = self._account(bid)
                        st = (self.t.bot(bid) or {}).get("state")
                        if acct and st in ("crafting", "selecting", "waiting_power", "launching"):
                            self.lock.refresh(acct, self._holder(bid))
                        if st == "done":
                            self._release(bid)
                tick += 1
                await asyncio.sleep(2)
        finally:
            for tk in tasks:
                tk.cancel()
