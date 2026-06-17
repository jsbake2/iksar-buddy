"""Mock crafting backend (FORGE.md: "we will work out the back end another time").

Drives each enabled bot through a believable craft cycle so the dashboard is fully
live to design against, and turns dashboard actions (start/stop/launch/ocr/...) into
telemetry changes. When the real CraftWorkers land, this module is replaced — the
web app talks to the same telemetry, so the frontend doesn't change.
"""
from __future__ import annotations

import asyncio
import time

from .telemetry import ForgeTelemetry

# Sample data so OCR / log-read / recipe lookups look real in the mock.
_SAMPLE_RECIPES = {
    "weaponsmith": ["Pristine Ash Round Shield", "Pristine Feyiron Kris",
                    "Pristine Iron Short Sword", "Pristine Carbonite Falchion"],
    "armorer": ["Pristine Feyiron Brigandine Coat", "Pristine Iron Vanguard Greaves",
                "Pristine Carbonite Plate Helm"],
    "tailor": ["Pristine Woven Leather Tunic", "Pristine Spun Cloak",
               "Pristine Rawhide Leggings"],
    "sage": ["Apprentice IV: Minor Healing", "Apprentice IV: Tap Veins",
             "Apprentice IV: Ward of the Untamed"],
    "carpenter": ["Pristine Elm Chair", "Pristine Teak Table"],
    "jeweler": ["Pristine Malachite Ring", "Pristine Lapis Choker"],
    "provisioner": ["Pristine Roasted Fish Fillet", "Pristine Spiced Tea"],
    "woodworker": ["Pristine Elm Long Bow", "Pristine Ash Round Buckler"],
    "alchemist": ["Pristine Lesser Adept's Potion", "Pristine Tox Wort Salve"],
}
_DEFAULT_RECIPES = ["Pristine Widget", "Pristine Doohickey", "Pristine Gadget"]


def _recipes_for(tc: str) -> list[str]:
    return _SAMPLE_RECIPES.get(tc, _DEFAULT_RECIPES)


class ForgeSim:
    """One per process. Holds telemetry; web app calls the action methods."""

    def __init__(self, tele: ForgeTelemetry) -> None:
        self.t = tele
        self._tick = 0

    # ---- dashboard actions ----------------------------------------------
    def enable(self, bot_id: str, on: bool) -> None:
        b = self.t.bot(bot_id)
        if not b:
            return
        self.t.update_bot(bot_id, enabled=on,
                          state=("idle" if on else "off"))
        self.t.push_event(bot_id, "control", "enabled" if on else "disabled")

    def configure(self, bot_id: str, **fields) -> None:
        """Persist UI selections (trade_class, mode, recipe, count) without
        starting — so the panel remembers them between renders."""
        clean = {k: v for k, v in fields.items()
                 if k in ("trade_class", "mode", "recipe", "character", "search",
                          "shutdown_when_done")}
        if "count" in fields:
            try:
                clean["count"] = {"done": 0, "total": max(1, int(fields["count"]))}
            except (TypeError, ValueError):
                pass
        if clean:
            self.t.update_bot(bot_id, **clean)

    def start(self, bot_id: str, mode: str, trade_class: str,
              recipe: str = "", count: int = 1, search: str = "", station: str = "") -> None:
        b = self.t.bot(bot_id)
        if not b or not b["enabled"]:
            return
        count = max(1, int(count or 1))
        if mode == "writ" and b.get("queue"):
            queue = [dict(it, done=0) for it in b["queue"]]
            first = queue[0]
            self.t.update_bot(bot_id, mode="writ", trade_class=trade_class,
                              queue=queue, recipe=first["name"],
                              item={"idx": 1, "total": len(queue)},
                              count={"done": 0, "total": first["count"]},
                              state="selecting", durability_mode=None,
                              reactions=0, crafts_done=0, started_at=time.time())
            self.t.push_event(bot_id, "craft",
                              f"writ: {len(queue)} recipes, {sum(i['count'] for i in queue)} crafts")
        else:
            recipe = recipe or _recipes_for(trade_class)[0]
            self.t.update_bot(bot_id, mode="single", trade_class=trade_class,
                              recipe=recipe, queue=[],
                              item={"idx": 0, "total": 0},
                              count={"done": 0, "total": count},
                              state="selecting", durability_mode=None,
                              reactions=0, crafts_done=0, started_at=time.time())
            self.t.push_event(bot_id, "craft", f"single: {recipe} x{count}")

    def stop(self, bot_id: str) -> None:
        b = self.t.bot(bot_id)
        if not b:
            return
        self.t.update_bot(bot_id, state="idle", durability_mode=None,
                          power_gated=False)
        self.t.push_event(bot_id, "control", "stopped")

    def pause(self, bot_id: str) -> None:
        b = self.t.bot(bot_id)
        if not b:
            return
        new = "paused" if b["state"] != "paused" else "crafting"
        self.t.update_bot(bot_id, state=new)
        self.t.push_event(bot_id, "control", new)

    def launch(self, bot_id: str) -> None:
        """Mock the power-on + login loop (FORGE.md §5.5)."""
        b = self.t.bot(bot_id)
        if not b:
            return
        self.t.update_bot(bot_id, state="launching", vm_running=True)
        self.t.push_event(bot_id, "launch", f"power on {b['dom']} -> login {b['character']}")
        asyncio.get_event_loop().call_later(
            6.0, lambda: (self.t.update_bot(bot_id, state="idle"),
                          self.t.push_event(bot_id, "launch",
                                            f"in-world as {b['character']}")))

    def switch_char(self, bot_id: str) -> None:
        b = self.t.bot(bot_id)
        if not b:
            return
        self.t.push_event(bot_id, "launch", "camp -> switch crafter (other toon)")

    def camp(self, bot_id: str) -> None:
        if not self.t.bot(bot_id):
            return
        self.t.update_bot(bot_id, state="idle")
        self.t.push_event(bot_id, "control", "camp (/camp)")

    def camp_all(self) -> None:
        for bid in self.t.snapshot["order"]:
            self.camp(bid)

    def set_keymap(self, km: dict) -> None:
        self._keymap = km or {}

    def shutdown(self, bot_id: str) -> None:
        if not self.t.bot(bot_id):
            return
        self.t.update_bot(bot_id, state="off", vm_running=False)
        self.t.push_event(bot_id, "control", "shutdown (EQ2 quit + VM off)")

    def shutdown_all(self) -> None:
        for bid in self.t.snapshot["order"]:
            self.shutdown(bid)

    def vm_off(self, bot_id: str) -> bool:
        b = self.t.bot(bot_id)
        return bool(b and not b.get("vm_running", False))

    def ocr_journal(self, bot_id: str) -> None:
        """Mock 'Read quest journal (OCR)' -> populate the writ queue."""
        b = self.t.bot(bot_id)
        if not b:
            return
        tc = b.get("trade_class") or "weaponsmith"
        recs = _recipes_for(tc)
        queue = [{"name": r, "count": (i % 3) + 2, "done": 0}
                 for i, r in enumerate(recs)]
        self.t.update_bot(bot_id, mode="writ", queue=queue)
        self.t.push_event(bot_id, "ocr", f"journal: detected {len(queue)} recipes")

    def read_log(self, bot_id: str) -> None:
        """Mock 'Read scribed recipes from log' — merges into the existing queue."""
        b = self.t.bot(bot_id)
        if not b:
            return
        tc = b.get("trade_class") or "sage"
        queue = list(b.get("queue", []) or [])
        have = {str(q.get("name", "")).strip().lower() for q in queue}
        added = 0
        for r in _recipes_for(tc):
            if r.lower() not in have:
                queue.append({"name": r, "count": 1, "done": 0})
                have.add(r.lower())
                added += 1
        self.t.update_bot(bot_id, mode="writ", queue=queue)
        self.t.push_event(bot_id, "log", f"log: +{added} scribed recipe(s) ({len(queue)} queued)")

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
                          "search": str(it.get("search", "")).strip()})
        self.t.update_bot(bot_id, mode="writ", queue=clean)
        self.t.push_event(bot_id, "queue", f"{len(clean)} recipes queued")

    # ---- background cycle ------------------------------------------------
    async def run(self, period: float = 0.85) -> None:
        while True:
            self._tick += 1
            for bid in self.t.snapshot["order"]:
                self._advance(bid)
            self.t.tick()
            await asyncio.sleep(period)

    def _advance(self, bot_id: str) -> None:
        b = self.t.bot(bot_id)
        if not b:
            return
        st = b["state"]
        if st == "selecting":
            # brief recipe-select, then start crafting
            b["state"] = "crafting"
            b["durability_mode"] = "progress"
            self.t.push_event(bot_id, "craft", f"selected {b['recipe']}")
            return
        if st != "crafting":
            # idle/off/done/paused/launching: power slowly regens when in-world
            if b.get("vm_running") and b["power"] < 1.0:
                b["power"] = min(1.0, b["power"] + 0.03)
            return

        # --- crafting tick: power, durability mode flips, reactions, progress ---
        # power drains while crafting; gate + recover when low (mock power ability)
        if b["power_gated"]:
            b["power"] = min(1.0, b["power"] + 0.18)
            if b["power"] >= 0.6:
                b["power_gated"] = False
                b["state"] = "crafting"
                self.t.push_event(bot_id, "power", "power recovered")
            else:
                b["state"] = "waiting_power"
                return
        b["power"] = max(0.0, b["power"] - 0.05)
        if b["power"] <= 0.12:
            b["power_gated"] = True
            b["state"] = "waiting_power"
            self.t.push_event(bot_id, "power", "low power -> waiting")
            return

        # durability/progress mode flips occasionally (which art-set runs)
        if self._tick % 7 == 0:
            b["durability_mode"] = ("durability" if b["durability_mode"] == "progress"
                                    else "progress")
        # reaction events fire intermittently
        if self._tick % 4 == 0:
            b["reactions"] += 1
            self.t.push_log(bot_id, f"countered reaction ({(b['reactions'] % 3) + 1})")

        # advance the craft itself every few ticks
        if self._tick % 5 == 0:
            b["count"]["done"] += 1
            b["crafts_done"] += 1
            self.t.push_event(bot_id, "craft",
                              f"{b['recipe']} {b['count']['done']}/{b['count']['total']}")
            elapsed = max(1.0, time.time() - (b["started_at"] or time.time()))
            b["crafts_per_hr"] = round(b["crafts_done"] / elapsed * 3600)
            if b["count"]["done"] >= b["count"]["total"]:
                self._next_item(b, bot_id)

    def _next_item(self, b: dict, bot_id: str) -> None:
        if b["mode"] == "writ" and b["queue"]:
            idx = b["item"]["idx"]                 # 1-based, just finished
            if idx < len(b["queue"]):
                b["queue"][idx - 1]["done"] = b["queue"][idx - 1]["count"]
                nxt = b["queue"][idx]
                b["item"] = {"idx": idx + 1, "total": len(b["queue"])}
                b["recipe"] = nxt["name"]
                b["count"] = {"done": 0, "total": nxt["count"]}
                b["state"] = "selecting"
                return
            b["queue"][idx - 1]["done"] = b["queue"][idx - 1]["count"]
        b["state"] = "done"
        b["durability_mode"] = None
        self.t.push_event(bot_id, "craft", "batch complete" if b["mode"] == "writ" else "done")
