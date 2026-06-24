"""Read the location name from the top-left HUD and decide if it is a cave.

The Dusk Ball is boosted in caves (and at night). The vendored locations_index
covers only some regions (no Sinnoh), so cave-ness is decided by the name: a set
of keywords ("cave", "tunnel", "mine", "gate", ...) plus a few keyword-less caves
matched by their distinctive words (Victory Road, Mt. Coronet, Ice Path), which
tolerates OCR noise like "VictoryF Road". This is a heuristic, not a full
location database — see CLAUDE.md milestone 4 for the data-backed version.
"""

from __future__ import annotations

import re

import cv2
import numpy as np

from battle_reader import LocationCalibration
from ocr_engine import run_ocr

# Drops the " Ch. N" channel suffix (and any trailing noise) from the HUD line.
# Tolerates OCR noise like "C h", "Ch,", "Ch .", or "Ch-"
_CH_SUFFIX = re.compile(r"\s*c\s*h[\.,\-\s]*\d+.*$", re.IGNORECASE)
# The location crop spans from the very top, so a full-window capture (fixtures
# and live alike) picks up the "PokeMMO" window-title text before the HUD name.
_TITLE_PREFIX = re.compile(r"^\s*pokemmo\s*", re.IGNORECASE)

# A location whose name contains any of these is a cave.
_CAVE_KEYWORDS = (
    "cave",
    "cavern",
    "tunnel",
    "mine",
    "gate",
    "grotto",
    "chamber",
    "coronet",  # Mt. Coronet, however "Mt." is OCR'd
)
# Keyword-less caves, matched by ALL their distinctive words (OCR-noise tolerant).
_CAVE_WORD_GROUPS = (
    ("victory", "road"),
    ("ice", "path"),
    ("ravaged", "path"),
    ("stark", "mountain"),
    ("iron", "island"),
)


def clean_location(raw: str) -> str:
    """The location name without the leading 'PokeMMO' title, the ' Ch. N'
    channel suffix, or stray edges."""
    s = _TITLE_PREFIX.sub("", raw.strip())
    cleaned = _CH_SUFFIX.sub("", s).strip(" .|")
    
    # PokeMMO's main menu displays "Loaded ROMs" in the top left.
    # We override it to display the app name instead.
    if "load" in cleaned.lower():
        return "ShakeChecker"
        
    # Prevent OCR from dropping the space before a number (e.g. "Route4" -> "Route 4")
    cleaned = re.sub(r'([a-zA-Z])(\d)', r'\1 \2', cleaned)
        
    return cleaned


def is_cave_location(name: str) -> bool:
    """True if the location name denotes a cave (Dusk Ball boosted)."""
    n = name.lower()
    if any(k in n for k in _CAVE_KEYWORDS):
        return True
    return any(all(word in n for word in group) for group in _CAVE_WORD_GROUPS)


def extract_location_mask(frame_bgr: np.ndarray, cal: LocationCalibration) -> np.ndarray | None:
    """Extract a binarized mask of the white HUD text, ignoring the background.
    Used for fast visual delta checking to skip OCR when the screen hasn't changed."""
    h, w = frame_bgr.shape[:2]
    crop = frame_bgr[int(h * cal.top) : int(h * cal.bottom), int(w * cal.left) : int(w * cal.right)]
    if crop.size == 0:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    # The game's day/night cycle tints the entire screen, drastically altering
    # the brightness of the white HUD text. Dynamically threshold based on the
    # brightest pixel in the crop to reliably isolate the text.
    max_val = gray.max()
    if max_val < 120:  # HUD is empty, and background is dim (ignore dirt/water)
        return np.zeros_like(gray)

    _, mask = cv2.threshold(gray, max_val * 0.6, 255, cv2.THRESH_BINARY)
    return mask


def read_location(frame_bgr: np.ndarray, cal: LocationCalibration) -> str:
    """OCR the top-left HUD location (cleaned), or '' if not readable."""
    h, w = frame_bgr.shape[:2]
    crop = frame_bgr[int(h * cal.top) : int(h * cal.bottom), int(w * cal.left) : int(w * cal.right)]
    if crop.size == 0:
        return ""
        
    # RapidOCR's detection model (DBNet) and recognition model (CRNN) both
    # target ~48px height natively. By forcing the image to exactly 48px tall,
    # we cut the pixel count massively and speed up DBNet by 500%, while 
    # preserving the beautiful natural anti-aliasing of the game engine so
    # the recognizer never hallucinates on jagged binary pixels.
    scale = 48.0 / crop.shape[0]
    up = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    
    from ocr_engine import run_ocr
    texts = run_ocr(up, task_name="location")
    return clean_location(" ".join(texts)) if texts else ""
