"""Read the battle chat log (OCR) for the current turn number.

PokeMMO prints "Turn N started!" in the battle chat, which sits at a stable
window position (bottom-left), unlike the letterboxed battle viewport. This is
the most reliable turn signal when the chat is visible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import cv2
import numpy as np

from battle_reader import ChatCalibration
from ocr_engine import run_ocr

# "Turn 2 started!" — tolerate OCR spacing/case noise.
_TURN = re.compile(r"turn\s*(\d{1,3})\s*start", re.IGNORECASE)
# "Gotcha! X was caught!" — the catch confirmation line.
_CAUGHT = re.compile(r"gotcha|was\s*caught", re.IGNORECASE)


@dataclass(frozen=True)
class ChatEvents:
    turn_number: int | None
    caught: bool


def parse_turn_number(texts: list[str]) -> int | None:
    """Highest "Turn N started" number among OCR text lines, or None."""
    best: int | None = None
    for line in texts:
        for m in _TURN.finditer(line):
            n = int(m.group(1))
            best = n if best is None else max(best, n)
    return best


def parse_chat(texts: list[str]) -> ChatEvents:
    caught = any(_CAUGHT.search(line) for line in texts)
    return ChatEvents(turn_number=parse_turn_number(texts), caught=caught)


def read_chat(frame_bgr: np.ndarray, cal: ChatCalibration) -> ChatEvents:
    """OCR the chat region for the current turn number and a catch event."""
    h, w = frame_bgr.shape[:2]
    crop = frame_bgr[int(h * cal.top) : int(h * cal.bottom), int(w * cal.left) : int(w * cal.right)]
    if crop.size == 0:
        return ChatEvents(turn_number=None, caught=False)
    up = cv2.resize(crop, None, fx=cal.upscale, fy=cal.upscale, interpolation=cv2.INTER_CUBIC)
    return parse_chat(run_ocr(up))


def read_turn_number(frame_bgr: np.ndarray, cal: ChatCalibration) -> int | None:
    """Current turn number (1-based) from the chat, or None if not readable."""
    return read_chat(frame_bgr, cal).turn_number
