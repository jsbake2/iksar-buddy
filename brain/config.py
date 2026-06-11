"""Config loading with hot-reload (owner edits YAML; brain picks it up)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(os.environ.get("IB_CONFIG_DIR", Path(__file__).resolve().parent.parent / "config"))


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass
class Config:
    """Aggregates the owner-owned YAML files; reloads when any file changes."""

    config_dir: Path = field(default_factory=lambda: CONFIG_DIR)
    ability_map: dict[str, Any] = field(default_factory=dict)
    thresholds: dict[str, Any] = field(default_factory=dict)
    calibration: dict[str, Any] = field(default_factory=dict)
    _mtimes: dict[str, float] = field(default_factory=dict)

    _FILES = {
        "ability_map": "ability_map.yaml",
        "thresholds": "thresholds.yaml",
        "calibration": "calibration.yaml",
    }

    def load(self) -> "Config":
        for attr, fname in self._FILES.items():
            setattr(self, attr, _load_yaml(self.config_dir / fname))
            self._stamp(fname)
        return self

    def _stamp(self, fname: str) -> None:
        p = self.config_dir / fname
        self._mtimes[fname] = p.stat().st_mtime if p.exists() else 0.0

    def reload_if_changed(self) -> bool:
        """Return True if any config file changed and was reloaded."""
        changed = False
        for attr, fname in self._FILES.items():
            p = self.config_dir / fname
            mtime = p.stat().st_mtime if p.exists() else 0.0
            if mtime != self._mtimes.get(fname):
                setattr(self, attr, _load_yaml(p))
                self._mtimes[fname] = mtime
                changed = True
        return changed

    # convenience accessors
    def key_for(self, role: str) -> str:
        return (self.ability_map.get("abilities", {}).get(role, {}) or {}).get("key", "")

    def macro_key(self, name: str) -> str:
        return (self.ability_map.get("macros", {}).get(name, {}) or {}).get("key", "")

    def threshold(self, name: str, default: Any = None) -> Any:
        return self.thresholds.get(name, default)
