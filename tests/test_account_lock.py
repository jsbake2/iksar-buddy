"""Tests for shared/account_lock.py — one live client per game account."""
import json
import os
import time

from shared.account_lock import AccountLock


def lk(tmp_path):
    return AccountLock(tmp_path / "locks")


def test_acquire_and_conflict(tmp_path):
    a = lk(tmp_path)
    ok, who = a.acquire("account_a", "brain:healer:Croolst")
    assert ok and who is None
    ok, who = a.acquire("account_a", "forge:vm2:Sage")
    assert not ok and who == "brain:healer:Croolst"


def test_acquire_idempotent_for_same_holder(tmp_path):
    a = lk(tmp_path)
    assert a.acquire("acct", "me")[0]
    assert a.acquire("acct", "me")[0]          # re-acquire = refresh, not conflict


def test_release_then_reacquire(tmp_path):
    a = lk(tmp_path)
    a.acquire("acct", "me")
    a.release("acct", "me")
    ok, _ = a.acquire("acct", "other")
    assert ok


def test_release_by_non_holder_is_ignored(tmp_path):
    a = lk(tmp_path)
    a.acquire("acct", "me")
    a.release("acct", "not-me")
    assert a.holder("acct") == "me"


def test_stale_lock_is_reclaimed(tmp_path):
    a = lk(tmp_path)
    a.acquire("acct", "crashed-proc")
    # backdate the lock beyond the ttl
    p = a._path("acct")
    p.write_text(json.dumps({"holder": "crashed-proc", "ts": time.time() - 9999}))
    ok, _ = a.acquire("acct", "new-proc", ttl=1800)
    assert ok
    assert a.holder("acct") == "new-proc"


def test_corrupt_lockfile_is_reclaimed(tmp_path):
    a = lk(tmp_path)
    a.acquire("acct", "me")
    a._path("acct").write_text("{not json")
    ok, _ = a.acquire("acct", "other")
    assert ok and a.holder("acct") == "other"


def test_refresh_only_by_holder(tmp_path):
    a = lk(tmp_path)
    a.acquire("acct", "me")
    assert a.refresh("acct", "me")
    assert not a.refresh("acct", "not-me")


def test_empty_account_needs_no_lock(tmp_path):
    a = lk(tmp_path)
    assert a.acquire("", "me") == (True, None)
    assert a.refresh("", "me")
    a.release("", "me")                        # no-op, no crash


def test_account_name_sanitized_to_filename(tmp_path):
    a = lk(tmp_path)
    a.acquire("weird/acct name!", "me")
    files = os.listdir(a.dir)
    assert files == ["weird_acct_name_.lock"]


def test_all_held_lists_live_locks(tmp_path):
    a = lk(tmp_path)
    a.acquire("one", "h1")
    a.acquire("two", "h2")
    held = a.all_held()
    assert held == {"one": "h1", "two": "h2"}
