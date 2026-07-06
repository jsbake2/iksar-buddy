"""Equivalence tests for the VECTORIZED sensing math (REFACTOR P2.2).

The vectorized _power_row_scan/_fill/_detriments must produce byte-identical
results to the original per-pixel loops (kept here as reference implementations)
— these ran live at 12 Hz, so the semantics are proven; the rewrite must not
drift. Synthetic frames only, no mss/Windows."""
from __future__ import annotations

import numpy as np

from agent.guest_sense import (CELL_XC, INSET, SEARCH, FramePix, _detriments,
                               _fill, _power_row, _power_row_scan, is_blue,
                               is_bright, is_icon, is_ignored)

RNG = np.random.default_rng(42)


def _frame(h=200, w=200):
    """Random noise frame (uint8 RGB) — worst case for equivalence checks."""
    return RNG.integers(0, 255, size=(h, w, 3), dtype=np.uint8)


# ---- reference implementations (the original per-pixel loops, verbatim) -----
def _ref_power_row_scan(pix, track, y0, y1):
    tx0, tx1 = track
    best_y, best_n = None, 0
    for y in range(y0, y1):
        n = sum(1 for x in range(tx0, tx1) if is_blue(pix.get((x, y))))
        if n > best_n:
            best_y, best_n = y, n
    return best_y if best_n >= 12 else None


def _ref_fill(pix, track, y):
    tx0, tx1 = track
    filled = sum(1 for x in range(tx0, tx1) if is_bright(pix.get((x, y))))
    return round(100 * filled / (tx1 - tx0))


def _ref_detriments(pix, row_y):
    cells = []
    for ci, xc in enumerate(CELL_XC):
        box = [pix.get((x, y))
               for x in range(xc - INSET, xc + INSET + 1)
               for y in range(row_y - INSET, row_y + INSET + 1)]
        lit = [c for c in box if is_icon(c)]
        if len(lit) > 0.4 * len(box):
            avg = tuple(sum(c[i] for c in lit) // len(lit) for i in range(3))
            cells.append({"cell": ci, "rgb": list(avg), "ignored": is_ignored(avg)})
    cure = any(c["ignored"] is None for c in cells)
    return cells, cure


# ---- equivalence on random noise --------------------------------------------
def test_power_row_scan_matches_reference():
    pix = FramePix(_frame())
    for track in ((19, 128), (33, 139)):
        for y0, y1 in ((0, 100), (30, 100), (150, 200)):
            assert _power_row_scan(pix, track, y0, y1) == \
                _ref_power_row_scan(pix, track, y0, y1), (track, y0, y1)


def test_power_row_matches_reference_incl_bounds():
    pix = FramePix(_frame())
    for y_hint in (0, 5, 100, 195, 199):     # incl. hints whose window clips the frame
        got = _power_row(pix, (33, 139), y_hint)
        ref = _ref_power_row_scan(pix, (33, 139), y_hint - SEARCH, y_hint + SEARCH + 1)
        assert got == ref, y_hint


def test_power_row_finds_planted_blue_bar():
    f = _frame()
    f[77, 33:139] = (10, 20, 200)            # a real power bar: blue row
    pix = FramePix(f)
    assert _power_row_scan(pix, (33, 139), 70, 85) == 77


def test_fill_matches_reference():
    pix = FramePix(_frame())
    for y in (0, 50, 128, 199):
        assert _fill(pix, (33, 139), y) == _ref_fill(pix, (33, 139), y), y


def test_fill_out_of_bounds_is_zero():
    pix = FramePix(_frame())
    assert _fill(pix, (33, 139), -1) == 0
    assert _fill(pix, (33, 139), 1000) == 0


def test_fill_planted_half_bar():
    f = np.zeros((200, 200, 3), dtype=np.uint8)
    f[50, 33:86] = (200, 200, 200)           # bright first half of a (33,139) track
    assert _fill(FramePix(f), (33, 139), 50) == 50


def test_detriments_match_reference():
    for _ in range(3):
        pix = FramePix(_frame())
        for row_y in (10, 160, 190):         # incl. a row whose box clips the frame
            assert _detriments(pix, row_y) == _ref_detriments(pix, row_y), row_y


def test_detriments_planted_icon():
    f = np.zeros((200, 200, 3), dtype=np.uint8)
    xc = CELL_XC[2]
    f[160 - INSET:160 + INSET + 1, xc - INSET:xc + INSET + 1] = (90, 90, 90)
    cells, cure = _detriments(FramePix(f), 160)
    assert [c["cell"] for c in cells] == [2]
    assert cure is True                       # not in IGNORE_SIGNATURES -> curable
