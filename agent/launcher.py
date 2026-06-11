"""Launcher automation (PROJECT.md §6.5): one owner action -> bot in group.

Fingerprint-gated, NEVER blind sleeps: each step waits for the expected screen's
pixel signature before acting. Credentials load from an UNTRACKED file
(config/secrets.yaml, gitignored) — never commit them.

This is a skeleton: the step sequence + gating are real; the actual waits hook
into Capture fingerprints and Injector once resolution is locked + fingerprints
calibrated. Account robskin2004 / character Jenskin on Woushi (owner SME).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import yaml

log = logging.getLogger("ib.agent.launcher")

SECRETS = Path(__file__).resolve().parent.parent / "config" / "secrets.yaml"


def load_secrets() -> dict:
    if SECRETS.exists():
        return yaml.safe_load(SECRETS.read_text()) or {}
    return {}


class Launcher:
    def __init__(self, capture, injector, calibration: dict) -> None:
        self.cap = capture
        self.inj = injector
        self.calib = calibration
        self.fp = (calibration or {}).get("fingerprints", {})

    def _wait_fingerprint(self, name: str, timeout: float = 60.0) -> bool:
        """Poll until the named screen fingerprint matches (or timeout)."""
        sig = self.fp.get(name)
        if not sig or not sig.get("rgb"):
            log.warning("fingerprint %r not calibrated; cannot gate", name)
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.cap.grab():
                rgb = self.cap.sample_region(sig["x"], sig["y"], sig["x"] + 1, sig["y"] + 1)
                if rgb and all(abs(a - b) <= sig.get("tol", 12) for a, b in zip(rgb, sig["rgb"])):
                    return True
            time.sleep(0.25)
        log.warning("timed out waiting for fingerprint %r", name)
        return False

    def boot_to_in_group(self) -> bool:
        """host: virsh start -> agent: client up -> login -> char -> in-world -> accept invite.

        Returns True when in-world. Each arrow is a fingerprint gate.
        """
        steps = [
            ("login_screen", self._do_login),
            ("char_select", self._do_char_select),
            ("in_world", self._do_in_world),
        ]
        for fp_name, action in steps:
            if not self._wait_fingerprint(fp_name):
                return False
            action()
        log.info("in-world; watching for group invite")
        return True

    # --- step actions (filled against calibrated coords/keys) -------------
    def _do_login(self) -> None:
        log.info("login screen: inject creds + ENTER (TODO: type into fields)")

    def _do_char_select(self) -> None:
        slot = (self.calib or {}).get("char_slot", {})
        log.info("char select: pick Jenskin @ %s + ENTER", slot)

    def _do_in_world(self) -> None:
        log.info("in world")

    def watch_for_invite(self, sampler) -> None:
        """On invite-dialog fingerprint, fire the bound accept macro (never typed)."""
        if self._wait_fingerprint("invite_dialog", timeout=300):
            log.info("invite dialog: firing accept-invite macro")
