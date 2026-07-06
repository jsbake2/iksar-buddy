"""Pixel capture — PRIMARY sensor (PROJECT.md §4). Ground truth for state.

Backend: dxcam preferred on the guest (DXGI), mss as fallback. On non-Windows /
no backend this degrades to a null capture that returns None, which the chat
guard correctly treats as UNSAFE (fail-closed).

Reads:
  - sample_region(): mean RGB of a box (used by the chat-safety guard).
  - read_hp_bars(): per-member HP fill ratio from a horizontal scanline.
  - power / cast-bar presence.
"""
from __future__ import annotations

import logging

log = logging.getLogger("ib.agent.capture")

try:
    import mss  # type: ignore
    import numpy as np  # type: ignore
    _HAVE = True
except Exception:  # pragma: no cover
    _HAVE = False


class Capture:
    def __init__(self) -> None:
        self._sct = mss.mss() if _HAVE else None
        self._frame = None

    def grab(self) -> bool:
        """Capture one full frame. Returns False if no backend (fail-closed)."""
        if not _HAVE:
            return False
        mon = self._sct.monitors[1]
        self._frame = np.asarray(self._sct.grab(mon))[:, :, :3][:, :, ::-1]  # BGRA->RGB
        return True

    def sample_region(self, x0: int, y0: int, x1: int, y1: int):
        """Mean RGB over a box, or None if no frame (-> guard treats as unsafe)."""
        if self._frame is None:
            return None
        box = self._frame[y0:y1, x0:x1]
        if box.size == 0:
            return None
        return [int(c) for c in box.reshape(-1, 3).mean(axis=0)]

    def fill_ratio(self, x0: int, x1: int, y: int, full_rgb, empty_rgb, tol: int = 40) -> float:
        """Fraction of a horizontal scanline matching the 'full' color = HP%.
        Vectorized (REFACTOR P2.2) — one ndarray compare, no per-pixel Python."""
        if self._frame is None or x1 <= x0:
            return 0.0
        line = self._frame[y, x0:x1].astype(np.int32)
        full = int(np.all(np.abs(line - np.asarray(full_rgb[:3])) <= tol, axis=1).sum())
        return full / max(1, (x1 - x0))

    def read_hp_bars(self, calibration: dict) -> list[dict]:
        out = []
        for bar in (calibration or {}).get("hp_bars", []) or []:
            hp = self.fill_ratio(bar["x0"], bar["x1"], bar["y"],
                                 bar.get("full_rgb", [200, 0, 0]),
                                 bar.get("empty_rgb", [40, 0, 0]))
            out.append({"slot": bar["slot"], "hp": round(hp, 3), "ward": True})
        return out
