"""Filtered-chat OCR — safety net / event catch (PROJECT.md §4, item 4).

Target is purpose-built: a dedicated chat window, filtered to relevant channels,
large font / black bg / high-contrast text -> near-zero noise for tesseract.
Polls 2-4 Hz. Catches: follow-drop lines, group-invite, rez offers, /loc fixes.

Degrades to a no-op (empty text, 0 confidence) when pytesseract/Pillow absent.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("ib.agent.ocr")

try:
    import pytesseract  # type: ignore
    from PIL import Image  # type: ignore
    _HAVE = True
except Exception:  # pragma: no cover
    _HAVE = False

# Lines we care about (compiled once). Owner can extend.
PATTERNS = {
    "follow_drop": re.compile(r"stopped following", re.I),
    "invite": re.compile(r"invites you to (?:join|a group)", re.I),
    "rez_offer": re.compile(r"(?:offers|wishes) to (?:revive|resurrect)", re.I),
    "loc": re.compile(r"-?\d+\.\d+,\s*-?\d+\.\d+,\s*-?\d+\.\d+"),
}


class Ocr:
    def read(self, frame_box) -> tuple[str, float]:
        """Return (text, confidence 0..1). frame_box is a PIL Image or ndarray."""
        if not _HAVE or frame_box is None:
            return "", 0.0
        img = frame_box if isinstance(frame_box, Image.Image) else Image.fromarray(frame_box)
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        words = [w for w in data["text"] if w.strip()]
        confs = [int(c) for c in data["conf"] if c not in ("-1", -1)]
        text = " ".join(words)
        conf = (sum(confs) / len(confs) / 100.0) if confs else 0.0
        return text, round(conf, 3)

    def classify(self, text: str) -> list[str]:
        return [name for name, pat in PATTERNS.items() if pat.search(text)]
