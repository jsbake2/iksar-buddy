"""Best-effort phone push via ntfy (https://ntfy.sh — free, no account).

Config lives OUTSIDE the repo at ~/ib-data/push.yaml (gitignored data dir), so the
secret topic never lands in git:

    ntfy:
      server: https://ntfy.sh      # or a self-hosted ntfy behind the tunnel
      topic:  ib-<random-secret>   # the topic you subscribe to in the ntfy phone app
      enabled: true                # runtime on/off (the dashboard toggle flips this)
      min_level: info              # info | good | warn | error  (floor to push)

Every ib app (forge, brain, harvest) reads this same file per-push, so ONE toggle
silences all three. push() never raises and never blocks the caller — a push failure
must not affect the bot. The topic name is effectively a shared secret (anyone who
knows it can read/send on the public server); keep it long + random.
"""
from __future__ import annotations

import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

import yaml

_LEVELS = {"info": 0, "good": 1, "warn": 2, "error": 3}
_TAGS = {"info": "information_source", "good": "white_check_mark",
         "warn": "warning", "error": "rotating_light"}
_PRIO = {"info": "default", "good": "default", "warn": "high", "error": "urgent"}


def _cfg_path() -> Path:
    return Path(os.environ.get("IB_DATA_DIR", str(Path.home() / "ib-data"))) / "push.yaml"


def config() -> dict:
    try:
        return (yaml.safe_load(_cfg_path().read_text("utf-8")) or {}).get("ntfy") or {}
    except (OSError, yaml.YAMLError):
        return {}


def status() -> dict:
    """UI state: is a topic configured, and are pushes currently enabled?"""
    c = config()
    return {"configured": bool(c.get("topic")),
            "enabled": bool(c.get("topic")) and bool(c.get("enabled", True)),
            "server": c.get("server") or "https://ntfy.sh"}


def set_enabled(on: bool) -> dict:
    """Flip the enabled flag in push.yaml (shared across all ib apps)."""
    p = _cfg_path()
    try:
        data = yaml.safe_load(p.read_text("utf-8")) or {}
    except (OSError, yaml.YAMLError):
        data = {}
    data.setdefault("ntfy", {})["enabled"] = bool(on)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data, sort_keys=False), "utf-8")
    return status()


def _post(server: str, topic: str, title: str, detail: str, level: str) -> None:
    url = f"{server.rstrip('/')}/{topic}"
    req = urllib.request.Request(url, data=(detail or title).encode("utf-8"), method="POST")
    # header values must be latin-1/ASCII-safe; our titles are, and the body is UTF-8.
    req.add_header("Title", (title or "ib")[:200].encode("ascii", "ignore").decode() or "ib")
    req.add_header("Priority", _PRIO.get(level, "default"))
    req.add_header("Tags", _TAGS.get(level, "bell"))
    try:
        urllib.request.urlopen(req, timeout=6).read()
    except (urllib.error.URLError, OSError, ValueError):
        pass  # best-effort; never surface a push failure to the bot


def push(title: str, detail: str = "", level: str = "info") -> None:
    """Fire a phone push if enabled AND level >= min_level. Non-blocking, never raises."""
    c = config()
    topic = c.get("topic")
    if not topic or not c.get("enabled", True):
        return
    if _LEVELS.get(level, 0) < _LEVELS.get(c.get("min_level", "info"), 0):
        return
    server = c.get("server") or "https://ntfy.sh"
    threading.Thread(target=_post, args=(server, topic, title, detail, level),
                     daemon=True).start()
