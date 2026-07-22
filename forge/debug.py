"""Per-bot OCR debug capture — screenshot + log on demand, ring-buffered.

Toggled per bot from the dashboard (the recipe-picker OCR mis-matches are the pain
point, and reproducing them blind is slow). When a bot's debug is ON, `capture()`
copies the current guest frame to a timestamped PNG and appends a log line that points
to it, so a failure ('Master of the Hunt II not matched') is paired with the exact frame
the matcher saw. Both the PNGs and the log roll off at KEEP so the disk never fills.

Shared module state: the forge web routes and the crafting workers run in ONE process,
so the `_enabled` set and the on-disk dir are the single source of truth for both. The
enabled set persists to enabled.json so a forge restart keeps the toggles.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

log = logging.getLogger("ib.forge.debug")

KEEP = 200          # max screenshots retained PER BOT (oldest rolled off)

_DIR = Path(os.environ.get("IB_FORGE_DEBUG_DIR",
            Path(os.environ.get("IB_FORGE_DIR", Path.home() / "ib-data" / "forge")) / "debug"))
_STATE = _DIR / "enabled.json"
_enabled: set[str] = set()


def _load_enabled() -> None:
    try:
        _enabled.update(json.loads(_STATE.read_text()))
    except (OSError, ValueError):
        pass


def _save_enabled() -> None:
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        _STATE.write_text(json.dumps(sorted(_enabled)))
    except OSError as e:
        log.warning("debug: could not persist enabled state: %s", e)


_load_enabled()


def is_enabled(bot_id: str) -> bool:
    return bot_id in _enabled


def set_enabled(bot_id: str, on: bool) -> bool:
    if on:
        _enabled.add(bot_id)
    else:
        _enabled.discard(bot_id)
    _save_enabled()
    log.info("debug %s for bot %s", "ON" if on else "OFF", bot_id)
    return bot_id in _enabled


def _stamp() -> str:
    t = time.time()
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(t)) + f".{int(t * 1000) % 1000:03d}"


def _logfile(bot_id: str) -> Path:
    return _DIR / f"{bot_id}.log"


def capture(bot_id: str, guest, tag: str, detail: str = "") -> str | None:
    """Save the current guest frame + a log line pointing to it. No-op unless the bot's
    debug is ON. Best-effort — never raises into the craft loop. Returns the PNG name."""
    if bot_id not in _enabled:
        return None
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        stamp = _stamp()
        safe_tag = re.sub(r"[^a-z0-9]+", "-", tag.lower()).strip("-") or "grab"
        name = f"{bot_id}_{stamp}_{safe_tag}.png"
        ppm = getattr(guest, "ppm", None)
        if ppm and Path(ppm).exists():
            # ppm (full frame from the last grab) -> browser-viewable png
            subprocess.run(["magick", ppm, str(_DIR / name)],
                           capture_output=True, timeout=10)
        else:
            name += "  (no-frame)"
        with _logfile(bot_id).open("a", encoding="utf-8") as f:
            f.write(f"{stamp} [{tag}] {detail} -> {name}\n")
        _rolloff(bot_id)
        return name
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("debug capture failed (%s): %s", tag, e)
        return None


def _rolloff(bot_id: str) -> None:
    """Keep only the newest KEEP screenshots + trim the log to match."""
    shots = sorted(_DIR.glob(f"{bot_id}_*.png"))
    for old in shots[:-KEEP]:
        try:
            old.unlink()
        except OSError:
            pass
    lf = _logfile(bot_id)
    try:
        lines = lf.read_text(encoding="utf-8").splitlines()
        if len(lines) > KEEP:
            lf.write_text("\n".join(lines[-KEEP:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def status(bot_id: str, tail: int = 40) -> dict:
    """Toggle state + the most recent log lines and screenshot names (for the UI)."""
    lines: list[str] = []
    try:
        lines = _logfile(bot_id).read_text(encoding="utf-8").splitlines()[-tail:]
    except OSError:
        pass
    shots = [p.name for p in sorted(_DIR.glob(f"{bot_id}_*.png"))[-tail:]]
    return {"enabled": bot_id in _enabled, "log": lines, "shots": shots, "keep": KEEP}


def shot_path(name: str) -> Path | None:
    """Resolve a screenshot name to a path INSIDE the debug dir (path-traversal safe)."""
    if not re.fullmatch(r"[A-Za-z0-9._-]+\.png", name or ""):
        return None
    p = (_DIR / name).resolve()
    try:
        p.relative_to(_DIR.resolve())
    except ValueError:
        return None
    return p if p.exists() else None
