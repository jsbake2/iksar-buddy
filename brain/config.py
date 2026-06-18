"""Config loading with hot-reload (owner edits YAML; brain picks it up).

Profiles (added 2026-06-13): the healer keymap is now a PROFILE — a self-contained
bundle (abilities + names + select_character + maintenance role) under
config/profiles/<name>.yaml. The active profile is named in config/active_profile.
Switching profiles (dashboard dropdown) swaps the whole healer identity: Jenskin
(Defiler, wards) vs Croolst (Fury, HoTs). Falls back to the legacy ability_map.yaml
if no profiles exist, so an un-migrated deploy keeps working unchanged.
"""
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
    """Aggregates the owner-owned YAML; reloads when any tracked file changes."""

    config_dir: Path = field(default_factory=lambda: CONFIG_DIR)
    ability_map: dict[str, Any] = field(default_factory=dict)   # = the active profile
    thresholds: dict[str, Any] = field(default_factory=dict)
    calibration: dict[str, Any] = field(default_factory=dict)
    active_profile: str | None = None
    _mtimes: dict[str, float] = field(default_factory=dict)

    @property
    def _profiles_dir(self) -> Path:
        return self.config_dir / "profiles"

    @property
    def _active_marker(self) -> Path:
        return self.config_dir / "active_profile"

    @property
    def ability_map_path(self) -> Path:
        """The file ability_map was actually loaded from — the active profile if
        one is set, else the bare ability_map.yaml. Saving the keymap must target
        THIS (not always ability_map.yaml) or edits to a profile silently vanish."""
        if self.active_profile:
            return self._profiles_dir / f"{self.active_profile}.yaml"
        return self.config_dir / "ability_map.yaml"

    def list_profiles(self) -> list[str]:
        if self._profiles_dir.is_dir():
            return sorted(p.stem for p in self._profiles_dir.glob("*.yaml"))
        return []

    def _resolve_profile(self) -> Path | None:
        """The yaml file for the active profile, or None to use legacy ability_map."""
        profiles = self.list_profiles()
        if not profiles:
            return None
        want = None
        if self._active_marker.exists():
            want = self._active_marker.read_text(encoding="utf-8").strip()
        if want not in profiles:
            want = profiles[0]            # default to the first profile alphabetically
        self.active_profile = want
        return self._profiles_dir / f"{want}.yaml"

    def load(self) -> "Config":
        self.thresholds = _load_yaml(self.config_dir / "thresholds.yaml")
        self.calibration = _load_yaml(self.config_dir / "calibration.yaml")
        prof = self._resolve_profile()
        if prof is not None:
            self.ability_map = _load_yaml(prof)
        else:
            self.active_profile = None
            self.ability_map = _load_yaml(self.config_dir / "ability_map.yaml")
        self._stamp_all()
        return self

    # -- hot reload --------------------------------------------------------
    def _tracked(self) -> list[Path]:
        files = [self.config_dir / "thresholds.yaml",
                 self.config_dir / "calibration.yaml",
                 self._active_marker]
        prof = self._profiles_dir / f"{self.active_profile}.yaml" if self.active_profile else None
        files.append(prof if prof else self.config_dir / "ability_map.yaml")
        return files

    def _stamp_all(self) -> None:
        for p in self._tracked():
            self._mtimes[str(p)] = p.stat().st_mtime if p.exists() else 0.0
        # also track which profile is active (so a profile SWITCH reloads)
        self._mtimes["__active__"] = hash(self.active_profile or "")

    def reload_if_changed(self) -> bool:
        # a switch of the active-profile marker, or an edit to any tracked file
        changed = False
        marker = self._active_marker.read_text(encoding="utf-8").strip() \
            if self._active_marker.exists() else ""
        if self.active_profile and marker and marker != self.active_profile and marker in self.list_profiles():
            changed = True
        else:
            for p in self._tracked():
                mt = p.stat().st_mtime if p.exists() else 0.0
                if mt != self._mtimes.get(str(p)):
                    changed = True
                    break
        if changed:
            self.load()
        return changed

    def set_profile(self, name: str) -> bool:
        """Switch the active profile (persisted + reloaded). Returns False if unknown."""
        if name not in self.list_profiles():
            return False
        self._active_marker.write_text(name + "\n", encoding="utf-8")
        self.load()
        return True

    # -- accessors ---------------------------------------------------------
    def key_for(self, role: str) -> str:
        return (self.ability_map.get("abilities", {}).get(role, {}) or {}).get("key", "")

    def macro_key(self, name: str) -> str:
        return (self.ability_map.get("macros", {}).get(name, {}) or {}).get("key", "")

    def threshold(self, name: str, default: Any = None) -> Any:
        return self.thresholds.get(name, default)

    @property
    def names(self) -> dict:
        """slot -> character name for the active profile (keys may be int or str)."""
        return self.ability_map.get("names", {}) or {}

    @property
    def select_character(self) -> str:
        return self.ability_map.get("select_character", "")

    def peek_select_character(self, name: str) -> str:
        """The select_character of ANOTHER profile without activating it (for the
        camp-and-switch flow: we pick the target toon, then only commit the profile
        swap if char-select actually succeeds)."""
        if name not in self.list_profiles():
            return ""
        prof = _load_yaml(self._profiles_dir / f"{name}.yaml")
        return prof.get("select_character", "")

    @property
    def healer_class(self) -> str:
        return self.ability_map.get("healer", "")

    @property
    def maint_role(self) -> str:
        """The proactive-mitigation heartbeat role: 'ward' (Defiler) or 'hot' (Fury)."""
        return self.ability_map.get("maintenance_role", "ward")

    @property
    def group_maint_role(self) -> str:
        return self.ability_map.get("group_maintenance_role", "group_ward")
