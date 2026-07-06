"""One-shot loader for the owner-tunable YAML config (REFACTOR P1).

brain/config.py keeps its own hot-reloading Config (profiles, mtime watching);
everything ELSE on the host — agent/host_agent, agent/host_sensor, harvest,
charswitch — reads startup tunables through this. Same directory resolution as
the brain (IB_CONFIG_DIR overrides the repo's config/), no hot reload: these
consumers are restart-cheap daemons/CLIs and a stale value beats a mid-cycle
mutation.

Missing file / bad YAML degrade to {} so the callers' baked-in fallback
constants stay in effect — config here can only override, never break startup.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(os.environ.get(
    "IB_CONFIG_DIR", Path(__file__).resolve().parent.parent / "config"))


def load(name: str) -> dict[str, Any]:
    try:
        return yaml.safe_load((CONFIG_DIR / name).read_text()) or {}
    except Exception:                  # noqa: BLE001 — absent/broken config = {}
        return {}


def thresholds() -> dict[str, Any]:
    return load("thresholds.yaml")


def calibration() -> dict[str, Any]:
    return load("calibration.yaml")


def harvest() -> dict[str, Any]:
    return load("harvest.yaml")
