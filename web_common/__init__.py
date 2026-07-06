"""web_common — the ONE copy of static assets shared by every dashboard
(REFACTOR P0.5).

brain, forge, and harvest each shipped their own byte-identical spice-html5
tree (~2.3 MB) and toast.js; fixes landed in one and silently missed the
others. Now the shared copies live in web_common/static/ and each app mounts
its statics through `app_statics()`, a StaticFiles whose lookup FALLS THROUGH
to this directory: the app's own static dir wins, so per-app overrides (each
app keeps its own spice/console.html — harvest scales the canvas client-side,
brain/forge rely on the guest's spice-agent being disabled) keep working with
zero URL changes.

Deliberately NOT shared: themes.css — the three copies have genuinely diverged
(conflicting layout rules grew into them, e.g. .control-panel spans). Unifying
needs a visual pass with the owner; until then they stay per-app.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.staticfiles import StaticFiles

STATIC = Path(__file__).resolve().parent / "static"


def app_statics(app_static: Path | str, *, html: bool = True) -> StaticFiles:
    """StaticFiles serving `app_static` first, web_common/static as fallback.

    Starlette's lookup_path walks `all_directories` in order, so appending the
    shared dir gives exactly the override semantics we want (app file wins)."""
    sf = StaticFiles(directory=str(app_static), html=html)
    sf.all_directories.append(str(STATIC))
    return sf
