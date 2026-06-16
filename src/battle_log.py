"""Read the exact turn number from the battle chat log via OCR.

The chat (bottom-left, stable window position) prints "Turn N started!", giving
the exact turn when the chat is visible. The command menu and catch banner are
NOT read here — they are detected by fast template matching (BattleTextReader in
battle_reader), which works with the chat hidden too.

Chat OCR is slow (seconds), so it is a CORRECTION only — the fast menu template
drives the live turn count. AsyncChatReader runs the OCR on a background thread
so it never blocks the main loop; the result lags but only ever raises the count
(a missed turn), which is the realistic drift.
"""

from __future__ import annotations

import re
from concurrent.futures import Future, ThreadPoolExecutor

import cv2
import numpy as np

from battle_reader import ChatCalibration
from ocr_engine import run_ocr

# "Turn 2 started!" — tolerate OCR spacing/case noise.
_TURN = re.compile(r"turn\s*(\d{1,3})\s*start", re.IGNORECASE)


def parse_turn_number(texts: list[str]) -> int | None:
    """Highest "Turn N started" number among OCR text lines, or None."""
    best: int | None = None
    for line in texts:
        for m in _TURN.finditer(line):
            n = int(m.group(1))
            best = n if best is None else max(best, n)
    return best


def read_turn_number(frame_bgr: np.ndarray, cal: ChatCalibration) -> int | None:
    """Current turn number (1-based) from the chat, or None if not readable."""
    h, w = frame_bgr.shape[:2]
    x0, x1 = cal.crop_x(w)
    crop = frame_bgr[int(h * cal.top) : int(h * cal.bottom), x0:x1]
    if crop.size == 0:
        return None
    up = cv2.resize(crop, None, fx=cal.upscale, fy=cal.upscale, interpolation=cv2.INTER_CUBIC)
    return parse_turn_number(run_ocr(up))


class AsyncChatReader:
    """Runs the slow chat-turn OCR on a single background thread so it never
    blocks the loop. submit() a frame (no-op while one is in flight); poll() the
    finished turn (or None). Uses its own OCR engine to avoid contending with the
    main thread's name/location OCR."""

    def __init__(self, cal: ChatCalibration) -> None:
        self._cal = cal
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="chat-ocr")
        self._future: Future[int | None] | None = None
        self._ocr = None

    def busy(self) -> bool:
        return self._future is not None and not self._future.done()

    def submit(self, frame_bgr: np.ndarray) -> None:
        # Don't start a new read while one exists -- whether still running OR
        # finished-but-not-yet-polled. Overwriting a finished future here (it is
        # no longer "busy") would silently discard its result before poll() reads
        # it, so the turn would never come through. poll() clears _future.
        if self._future is not None:
            return
        c = self._cal
        h, w = frame_bgr.shape[:2]
        x0, x1 = c.crop_x(w)
        crop = frame_bgr[
            int(h * c.top) : int(h * c.bottom), x0:x1
        ].copy()  # copy: the worker reads it after this frame is gone
        self._future = self._pool.submit(self._read, crop)

    def poll(self) -> int | None:
        """The latest finished turn number (consumed once), or None."""
        if self._future is not None and self._future.done():
            result = self._future.result()
            self._future = None
            return result
        return None

    def reset(self) -> None:
        """Drop any in-flight result so a previous battle's turn can't leak into
        the next one. A worker already running just finishes and is ignored."""
        self._future = None

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _read(self, crop: np.ndarray) -> int | None:
        if crop.size == 0:
            return None
        if self._ocr is None:
            from rapidocr_onnxruntime import RapidOCR

            self._ocr = RapidOCR()
        up = cv2.resize(
            crop, None, fx=self._cal.upscale, fy=self._cal.upscale, interpolation=cv2.INTER_CUBIC
        )
        result, _ = self._ocr(up)
        return parse_turn_number([text for _box, text, _score in result] if result else [])
