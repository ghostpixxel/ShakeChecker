"""Read the enemy species name via OCR and resolve it to a species entry.

OCR output is never trusted directly: the level marker and trailing icons are
stripped and the remainder is fuzzy-matched (rapidfuzz) against the English
species list from species_core.json. The OCR engine is loaded lazily so this
module (and the pure matching logic) imports cheaply for tests.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import cv2
import numpy as np
from rapidfuzz import fuzz, process

from battle_reader import BarReading, NameCalibration
from ocr_engine import run_ocr

# Cut the OCR string at the level marker ("Lv", "Lu", "Iv" misreads) so only
# the name remains; everything after (level number, gender, caught ball) is noise.
_LEVEL_MARKER = re.compile(r"\b[li][vu]\b", re.IGNORECASE)
# The level number directly follows the marker: "Lv. 43".
_LEVEL_NUMBER = re.compile(r"\b[li][vu]\b\.?\s*(\d{1,3})", re.IGNORECASE)

FEMALE = "♀"  # ♀
MALE = "♂"  # ♂


def clean_ocr_text(raw: str) -> str:
    return _LEVEL_MARKER.split(raw, maxsplit=1)[0].strip()


def _strip_gender(name: str) -> str:
    return name[:-1] if name and name[-1] in (FEMALE, MALE) else name


def detect_gender(crop_bgr: np.ndarray, cal: NameCalibration) -> str:
    """Classify the banner's colored gender icon as ♀ or ♂ from the name crop.

    OCR cannot read the ♂/♀ glyph, so this is the only way to tell the two
    Nidoran entries apart. The banner draws a pink ♀ / blue ♂ icon after the
    level; we measure the pink fraction of the crop (a fraction, so resolution
    independent) and call it ♀ above the threshold, otherwise ♂. Only invoked
    for gender-split species, so a non-pink crop unambiguously means ♂.
    """
    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    pink = (
        (h >= cal.gender_pink_h_lo)
        & (h <= cal.gender_pink_h_hi)
        & (s >= cal.gender_pink_sat_min)
        & (v >= cal.gender_pink_val_min)
    )
    frac = float(pink.sum()) / pink.size if pink.size else 0.0
    return FEMALE if frac >= cal.gender_pink_frac else MALE


def parse_level(raw: str) -> int | None:
    m = _LEVEL_NUMBER.search(raw)
    return int(m.group(1)) if m else None


def match_species_name(raw_text: str, names: list[str], min_score: float) -> str | None:
    """Best species name for an OCR string, or None below `min_score`."""
    candidate = clean_ocr_text(raw_text)
    if not candidate:
        return None
    match = process.extractOne(candidate, names, scorer=fuzz.WRatio, processor=str.lower)
    if match is None:
        return None
    name, score, _ = match
    return name if score >= min_score else None


class NameReader:
    """Crops the name region, OCRs it and resolves it to a species dict
    ({id, name, catch_rate, ...} from species_core.json)."""

    def __init__(self, cal: NameCalibration, species_path: Path | str) -> None:
        self._cal = cal
        species = json.loads(Path(species_path).read_text("utf-8"))
        self._names = [s["name"] for s in species]
        self._by_name = {s["name"]: s for s in species}

    def read(self, frame_bgr: np.ndarray, bar: BarReading) -> dict | None:
        """Species dict (plus the encounter's "level") for the enemy whose bar
        is `bar`, or None if the name could not be read/matched. The returned
        dict is a copy; the vendored species data is not mutated."""
        c = self._cal
        y0, y1 = bar.y + c.dy0, bar.y + c.dy1
        x0, x1 = bar.x + c.dx0, bar.x + c.dx1
        if y0 < 0 or x0 < 0 or y1 > frame_bgr.shape[0] or x1 > frame_bgr.shape[1]:
            return None
        crop = frame_bgr[y0:y1, x0:x1]
        if crop.size == 0:
            return None
        up = cv2.resize(crop, None, fx=c.upscale, fy=c.upscale, interpolation=cv2.INTER_CUBIC)
        raw = " ".join(run_ocr(up))
        name = match_species_name(raw, self._names, c.min_match_score)
        if not name:
            return None
        # OCR loses the ♂/♀ glyph, so a gender-split species (Nidoran) always
        # ties to one variant. Re-decide it from the banner's colored gender icon.
        base = _strip_gender(name)
        if base != name:
            variant = base + detect_gender(crop, c)
            if variant in self._by_name:
                name = variant
        return {**self._by_name[name], "level": parse_level(raw)}
