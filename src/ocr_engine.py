"""Shared lazy RapidOCR engine so name and chat readers reuse one ONNX model."""

from __future__ import annotations

import numpy as np

_ocr = None


def _engine():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR

        _ocr = RapidOCR()
    return _ocr


def sorted_ocr_lines(result) -> list[str]:
    """Text lines from a RapidOCR result ordered TOP-TO-BOTTOM by box position.
    RapidOCR's native detection order is not guaranteed vertical, so callers that
    care about reading order (e.g. the chat: newest line is at the bottom) must
    sort explicitly. Each result item is (box, text, score); box is 4 [x, y]
    points, so the line's top is the smallest y among them."""
    if not result:
        return []
    return [t for _b, t, _s in sorted(result, key=lambda r: min(float(p[1]) for p in r[0]))]


def run_ocr(image: np.ndarray) -> list[str]:
    """OCR an image, returning the detected text lines (empty if none)."""
    result, _ = _engine()(image)
    return [text for _box, text, _score in result] if result else []


def run_ocr_lines(image: np.ndarray) -> list[str]:
    """OCR an image, returning the text lines ordered top-to-bottom by position."""
    result, _ = _engine()(image)
    return sorted_ocr_lines(result)
