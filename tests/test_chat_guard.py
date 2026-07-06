"""Tests for THE invariant: keystrokes never land in the chat bar (PROJECT.md §6.2).

ChatGuard must fail CLOSED — any doubt (uncalibrated, sampler missing, sampler
error, unreadable frame) means unsafe, no injection. Injector.guarded_press is
the only path that touches the keyboard: safe -> press; unsafe -> abort + ESC +
one re-verify; still unsafe -> alarm, no press.
"""
from agent.chat_guard import ChatGuard
from agent.inject import Backend, Injector

CAL = {"chat_input": {"x0": 50, "y0": 1019, "x1": 258, "y1": 1041,
                      "active_rgb": [30, 30, 30], "tol": 10}}

ACTIVE = [30, 30, 30]          # matches active_rgb -> chat input OPEN
INACTIVE = [200, 200, 200]     # far from active_rgb -> chat closed


def sampler_of(rgb):
    return lambda x0, y0, x1, y1: rgb


def failing_sampler(*a):
    raise RuntimeError("grab failed")


# ---------------------------------------------------------------- chat_active

def test_chat_active_true_when_fingerprint_matches():
    assert ChatGuard(CAL).chat_active(sampler_of(ACTIVE)) is True


def test_chat_active_true_within_tolerance():
    assert ChatGuard(CAL).chat_active(sampler_of([39, 21, 30])) is True


def test_chat_active_false_when_region_differs():
    assert ChatGuard(CAL).chat_active(sampler_of(INACTIVE)) is False


def test_chat_active_none_when_uncalibrated():
    assert ChatGuard({}).chat_active(sampler_of(INACTIVE)) is None
    assert ChatGuard(None).chat_active(sampler_of(INACTIVE)) is None
    # calibration present but empty region (x1 unset) = not calibrated yet
    assert ChatGuard({"chat_input": {"x1": 0}}).chat_active(sampler_of(INACTIVE)) is None


def test_chat_active_none_when_no_sampler():
    assert ChatGuard(CAL).chat_active(None) is None


def test_chat_active_none_when_sampler_raises():
    assert ChatGuard(CAL).chat_active(failing_sampler) is None


def test_chat_active_none_when_sampler_returns_none():
    assert ChatGuard(CAL).chat_active(sampler_of(None)) is None


# -------------------------------------------------------- is_safe: fail-closed

def test_is_safe_only_when_provably_inactive():
    assert ChatGuard(CAL).is_safe(sampler_of(INACTIVE)) is True


def test_is_safe_false_when_chat_open():
    assert ChatGuard(CAL).is_safe(sampler_of(ACTIVE)) is False


def test_unknown_is_unsafe():
    """Every 'can't tell' path must gate injection: this is the invariant."""
    assert ChatGuard({}).is_safe(sampler_of(INACTIVE)) is False       # uncalibrated
    assert ChatGuard(CAL).is_safe(None) is False                      # no sampler
    assert ChatGuard(CAL).is_safe(failing_sampler) is False           # sensor error
    assert ChatGuard(CAL).is_safe(sampler_of(None)) is False          # unreadable frame


# ------------------------------------------------------------- guarded_press

class SpyBackend(Backend):
    def __init__(self):
        self.taps = []

    def tap(self, key):
        self.taps.append(key)


def test_guarded_press_sends_when_safe():
    be = SpyBackend()
    inj = Injector(ChatGuard(CAL), be)
    assert inj.guarded_press("f2", sampler_of(INACTIVE)) is True
    assert be.taps == ["f2"]
    assert inj.guard.aborted_injections == 0


def test_guarded_press_abort_esc_recover():
    """Unsafe first look -> abort + ESC; re-verify passes -> key goes through."""
    be = SpyBackend()
    inj = Injector(ChatGuard(CAL), be)
    looks = iter([ACTIVE, INACTIVE])              # open, then closed after ESC

    def sampler(*a):
        return next(looks)

    assert inj.guarded_press("f2", sampler) is True
    assert be.taps == ["Escape", "f2"]
    assert inj.guard.aborted_injections == 1
    assert inj.guard.alarms == 0


def test_guarded_press_never_sends_when_still_unsafe():
    """Chat stays open after ESC -> no keypress, alarm raised. No blind retry."""
    be = SpyBackend()
    inj = Injector(ChatGuard(CAL), be)
    assert inj.guarded_press("f2", sampler_of(ACTIVE)) is False
    assert be.taps == ["Escape"]                  # ESC only — the key never went out
    assert inj.guard.aborted_injections == 1
    assert inj.guard.alarms == 1


def test_guarded_press_never_sends_when_unknown():
    """Uncalibrated/unreadable = unknown = no injection, ever."""
    be = SpyBackend()
    inj = Injector(ChatGuard({}), be)             # no calibration at all
    assert inj.guarded_press("f2", sampler_of(INACTIVE)) is False
    assert "f2" not in be.taps


def test_guarded_press_empty_key_is_noop():
    be = SpyBackend()
    inj = Injector(ChatGuard(CAL), be)
    assert inj.guarded_press("", sampler_of(INACTIVE)) is False
    assert be.taps == []
