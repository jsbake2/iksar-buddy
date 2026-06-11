"""State machine (PROJECT.md §6.1).

States: OOC, IN_COMBAT, WIPE_RECOVERY, REZ_LOOP.
Transitions come from: log signal, manual override, optional pixel heuristic.
Manual override ALWAYS wins and latches until the next clean transition.
"""
from __future__ import annotations

import time
from enum import Enum


class State(str, Enum):
    OOC = "OOC"
    IN_COMBAT = "IN_COMBAT"
    WIPE_RECOVERY = "WIPE_RECOVERY"
    REZ_LOOP = "REZ_LOOP"


# Manual overrides the host-side hotkeys can force.
class Override(str, Enum):
    FORCE_COMBAT = "force_combat"
    FORCE_OOC = "force_ooc"
    FORCE_FOLLOW = "force_follow"
    FORCE_REZ = "force_rez"


class StateMachine:
    def __init__(self) -> None:
        self.state: State = State.OOC
        self.override: Override | None = None
        self.changed_at: float = time.time()

    def _set(self, new: State) -> bool:
        if new != self.state:
            self.state = new
            self.changed_at = time.time()
            return True
        return False

    # --- inputs -----------------------------------------------------------
    def set_override(self, ov: Override | None) -> bool:
        """Latch a manual override. Suppresses auto-detection until cleared."""
        self.override = ov
        if ov == Override.FORCE_COMBAT:
            return self._set(State.IN_COMBAT)
        if ov == Override.FORCE_OOC:
            return self._set(State.OOC)
        if ov == Override.FORCE_REZ:
            return self._set(State.REZ_LOOP)
        return False

    def clear_override(self) -> None:
        self.override = None

    def on_combat_signal(self, in_combat: bool) -> bool:
        """Coarse combat start/end from logs (never the sole critical trigger)."""
        if self.override in (Override.FORCE_COMBAT, Override.FORCE_OOC, Override.FORCE_REZ):
            return False  # override latched; ignore auto-detection
        return self._set(State.IN_COMBAT if in_combat else State.OOC)

    def on_wipe(self) -> bool:
        if self.override == Override.FORCE_OOC:
            return False
        return self._set(State.WIPE_RECOVERY)

    def enter_rez_loop(self) -> bool:
        return self._set(State.REZ_LOOP)
