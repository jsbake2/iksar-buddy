"""Compatibility shim — LoginDriver was promoted to shared/login.py (REFACTOR
P0.7): brain and harvest consume it too, and the healer importing forge's
package was backwards. Import from shared.login going forward.
"""
from shared.login import WORLD, LoginDriver, load_accounts  # noqa: F401
