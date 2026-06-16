"""One-off in-guest capture diagnostic. Runs in the interactive session (mss needs
a desktop). Lists every monitor mss sees, grabs each, saves a PNG + mean brightness
(0 = black frame), and dumps it to C:\\ib\\app\\captest\\ for the host to pull."""
import json
from pathlib import Path

import cv2
import mss
import numpy as np

out = Path(r"C:\ib\app\captest")
out.mkdir(parents=True, exist_ok=True)
info = {}
try:
    with mss.mss() as sct:
        info["monitors"] = sct.monitors
        for i, mon in enumerate(sct.monitors):
            try:
                arr = np.asarray(sct.grab(mon))[:, :, :3]      # BGRA->BGR
                cv2.imwrite(str(out / f"mon{i}.png"), arr)
                info[f"mon{i}_shape"] = list(arr.shape)
                info[f"mon{i}_mean"] = round(float(arr.mean()), 2)   # ~0 = black
            except Exception as e:                              # noqa: BLE001
                info[f"mon{i}_err"] = repr(e)
except Exception as e:                                          # noqa: BLE001
    info["fatal"] = repr(e)
(out / "info.json").write_text(json.dumps(info, indent=2))
