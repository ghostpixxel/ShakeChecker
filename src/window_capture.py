"""Find the PokeMMO window and capture its client area (read-only).

Strictly passive: enumerates windows and grabs pixels via mss. Never sends
input, never touches the game process.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

import mss
import numpy as np
import win32gui

WINDOW_TITLE = "PokeMMO"

# DwmGetWindowAttribute: the VISIBLE window rectangle (what a window screenshot
# captures), excluding the invisible DWM resize borders that GetWindowRect adds.
_DWMWA_EXTENDED_FRAME_BOUNDS = 9

# The PokeMMO client presents a window title built from Cyrillic/Greek
# homoglyphs (observed: 'РokеMМO' with Cyrillic Р U+0420, е U+0435, М U+041C)
# that looks like "PokeMMO" but is not ASCII, so a naive title match fails.
# Fold the common confusable letters back to ASCII before comparing.
_CONFUSABLES = {
    # Cyrillic -> Latin
    "А": "A",
    "В": "B",
    "Е": "E",
    "Ѕ": "S",
    "І": "I",
    "Ј": "J",
    "К": "K",
    "М": "M",
    "Н": "H",
    "О": "O",
    "Р": "P",
    "С": "C",
    "Т": "T",
    "Х": "X",
    "а": "a",
    "е": "e",
    "о": "o",
    "р": "p",
    "с": "c",
    "у": "y",
    "х": "x",
    "к": "k",
    "ѕ": "s",
    "і": "i",
    "ј": "j",
    # Greek -> Latin
    "Α": "A",
    "Β": "B",
    "Ε": "E",
    "Η": "H",
    "Ι": "I",
    "Κ": "K",
    "Μ": "M",
    "Ν": "N",
    "Ο": "O",
    "Ρ": "P",
    "Τ": "T",
    "Υ": "Y",
    "Χ": "X",
    "Ζ": "Z",
    "ο": "o",
    "ρ": "p",
    "τ": "t",
}
_FOLD = str.maketrans(_CONFUSABLES)


def fold_confusables(text: str) -> str:
    """Map common Cyrillic/Greek homoglyphs to their ASCII lookalikes."""
    return text.translate(_FOLD)


def title_matches(title: str) -> bool:
    """True only if `title` is exactly the PokeMMO game window title (homoglyph-
    folded), not a browser tab that merely starts with 'PokeMMO' (e.g.
    'PokeMMO Help - Google Chrome'). The game window is titled exactly 'PokeMMO'."""
    return fold_confusables(title).strip().lower() == WINDOW_TITLE.lower()


def set_dpi_awareness() -> None:
    """Must run at startup before any coordinate work (CLAUDE.md hard rule)."""
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE


@dataclass(frozen=True)
class ClientRect:
    left: int
    top: int
    width: int
    height: int


def iter_visible_windows() -> list[tuple[int, str]]:
    """(hwnd, title) for every visible top-level window.

    The callback is exception-safe per window: a single window that errors on
    IsWindowVisible/GetWindowText (e.g. a higher-integrity or already-closing
    window) is skipped instead of aborting the whole enumeration. It always
    returns True so enumeration runs to completion regardless of Z-order.
    """
    windows: list[tuple[int, str]] = []

    def on_window(hwnd: int, _param: object) -> bool:
        try:
            if win32gui.IsWindowVisible(hwnd):
                windows.append((hwnd, win32gui.GetWindowText(hwnd)))
        except win32gui.error:
            pass
        return True

    win32gui.EnumWindows(on_window, None)
    return windows


def find_pokemmo_hwnd() -> int | None:
    """Visible window whose (homoglyph-folded) title is exactly 'PokeMMO'.
    If several match, pick the one with the largest client area (the real game
    window, not a zero-sized helper/tooltip)."""
    best: int | None = None
    best_area = -1
    for hwnd, title in iter_visible_windows():
        if not title_matches(title):
            continue
        rect = get_client_rect(hwnd)
        area = rect.width * rect.height if rect else 0
        if area > best_area:
            best, best_area = hwnd, area
    return best


def is_window_alive(hwnd: int) -> bool:
    return bool(win32gui.IsWindow(hwnd))


def get_client_rect(hwnd: int) -> ClientRect | None:
    """Client area of `hwnd` in screen coordinates, or None if not usable
    (window gone, minimized, or zero-sized). Used to dock the overlay inside the
    game (below the HUD)."""
    try:
        if win32gui.IsIconic(hwnd):
            return None
        left, top = win32gui.ClientToScreen(hwnd, (0, 0))
        _l, _t, right, bottom = win32gui.GetClientRect(hwnd)
    except win32gui.error:
        return None
    if right <= 0 or bottom <= 0:
        return None
    return ClientRect(left=left, top=top, width=right, height=bottom)


def get_window_rect(hwnd: int) -> ClientRect | None:
    """Full visible window rectangle (incl. title bar) in screen coordinates, or
    None if not usable. This is what a window screenshot captures, so the live
    frames match the fixtures the CV regions are calibrated on — unlike the client
    area, which omits the title bar and shifts every fractional region. Uses the
    DWM extended frame bounds (excludes invisible resize borders); falls back to
    GetWindowRect."""
    try:
        if win32gui.IsIconic(hwnd):
            return None
        r = wintypes.RECT()
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            wintypes.DWORD(_DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(r),
            ctypes.sizeof(r),
        )
        if hr != 0:  # DWM unavailable -> plain window rect
            r.left, r.top, r.right, r.bottom = win32gui.GetWindowRect(hwnd)
    except (OSError, win32gui.error):
        return None
    width, height = r.right - r.left, r.bottom - r.top
    if width <= 0 or height <= 0:
        return None
    return ClientRect(left=r.left, top=r.top, width=width, height=height)


class WindowCapture:
    """Grabs BGR frames of a screen rectangle. One instance per thread (mss)."""

    def __init__(self) -> None:
        self._sct = mss.mss()

    def grab(self, rect: ClientRect) -> np.ndarray:
        shot = self._sct.grab(
            {"left": rect.left, "top": rect.top, "width": rect.width, "height": rect.height}
        )
        # mss delivers BGRA; drop alpha -> BGR, contiguous for OpenCV
        return np.ascontiguousarray(np.asarray(shot)[:, :, :3])

    def close(self) -> None:
        self._sct.close()
