"""Shared lazy RapidOCR engine so name and chat readers reuse one ONNX model."""

from __future__ import annotations

import numpy as np

# Cap the onnxruntime CPU threads per OCR inference. By default onnxruntime runs
# on ALL logical cores, so each OCR (location every ~2.5s, name once per battle,
# chat ~5x/s in battle) briefly pegs the whole CPU. On weak laptops that starves
# the audio thread and stutters the entire system (reported in the wild). Two
# threads keep OCR responsive while leaving cores free for the game and audio.
OCR_THREADS = 2

_ocr = None
_threads_capped = False


def _cap_ort_threads() -> None:
    """Force RapidOCR's onnxruntime sessions to a small thread count.

    The bundled OrtInferSession never sets intra_op_num_threads, and this RapidOCR
    version exposes no config key for it, so we subclass the SessionOptions it
    instantiates and set the limit there before the engine is built."""
    global _threads_capped
    if _threads_capped:
        return
    import rapidocr_onnxruntime.utils as ort_utils

    base = ort_utils.SessionOptions

    class _CappedSessionOptions(base):  # type: ignore[misc, valid-type]
        def __init__(self) -> None:
            super().__init__()
            self.intra_op_num_threads = OCR_THREADS
            self.inter_op_num_threads = 1

    ort_utils.SessionOptions = _CappedSessionOptions
    _threads_capped = True


def _engine():
    global _ocr
    if _ocr is None:
        _cap_ort_threads()
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
