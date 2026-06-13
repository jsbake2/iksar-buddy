"""Account interlock — shared by the healer brain and Forge (FORGE.md §3).

An EQ2 account can't be logged in twice at once. The healer (e.g. Croolst) and a
crafter (e.g. Croolst the Sage) can be the SAME account, so both tools must agree
who holds an account before logging it in. This is a tiny host-side lock registry:
one lockfile per account under IB_DATA_DIR/locks. Both tools run on the same server,
so a shared directory + atomic O_EXCL create is enough — no daemon.

Usage:
    lk = AccountLock()
    ok, who = lk.acquire("account_a", "forge:iksar_buddy2:Croolst")
    if not ok: refuse("account_a held by " + who)
    ...
    lk.refresh("account_a", holder)   # call periodically while logged in
    lk.release("account_a", holder)   # on camp/logout

Stdlib-only (shared/ stays dependency-free)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("IB_DATA_DIR", Path.home() / "ib-data"))
DEFAULT_LOCK_DIR = DATA_DIR / "locks"
STALE_S = 1800.0          # a lock un-refreshed this long is reclaimable (crash safety)


class AccountLock:
    def __init__(self, lock_dir: str | os.PathLike | None = None) -> None:
        self.dir = Path(lock_dir or DEFAULT_LOCK_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, account: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in account)
        return self.dir / f"{safe}.lock"

    def _read(self, account: str) -> dict | None:
        p = self._path(account)
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return None

    def _write(self, account: str, holder: str) -> None:
        tmp = self._path(account).with_suffix(".tmp")
        tmp.write_text(json.dumps({"holder": holder, "ts": time.time()}), encoding="utf-8")
        tmp.replace(self._path(account))

    def acquire(self, account: str, holder: str, ttl: float = STALE_S) -> tuple[bool, str | None]:
        """Try to take `account` for `holder`. Returns (ok, current_holder_if_failed).
        Idempotent for the same holder (refreshes). Reclaims a stale lock."""
        if not account:
            return True, None                      # unmapped account = no lock needed
        p = self._path(account)
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            cur = self._read(account)
            if cur is None:                        # corrupt/empty -> reclaim
                self._write(account, holder); return True, None
            if cur.get("holder") == holder:        # we already hold it -> refresh
                self._write(account, holder); return True, None
            if time.time() - float(cur.get("ts", 0)) > ttl:   # stale -> reclaim
                self._write(account, holder); return True, None
            return False, cur.get("holder")
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps({"holder": holder, "ts": time.time()}))
        return True, None

    def refresh(self, account: str, holder: str) -> bool:
        if not account:
            return True
        cur = self._read(account)
        if cur and cur.get("holder") == holder:
            self._write(account, holder)
            return True
        return False

    def release(self, account: str, holder: str) -> None:
        if not account:
            return
        cur = self._read(account)
        if cur is None or cur.get("holder") == holder:
            self._path(account).unlink(missing_ok=True)

    def holder(self, account: str) -> str | None:
        cur = self._read(account)
        if not cur:
            return None
        if time.time() - float(cur.get("ts", 0)) > STALE_S:
            return None                            # stale = effectively free
        return cur.get("holder")

    def all_held(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for p in self.dir.glob("*.lock"):
            try:
                cur = json.loads(p.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if time.time() - float(cur.get("ts", 0)) <= STALE_S:
                out[p.stem] = cur.get("holder", "")
        return out
