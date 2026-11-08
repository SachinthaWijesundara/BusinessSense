from __future__ import annotations

import cv2
import numpy as np


class ImagePreprocessError(ValueError):
    """Raised when the uploaded image cannot be decoded or enhanced."""


def apply_clahe(image_bytes: bytes) -> tuple[bytes, str]:
    """Enhance contrast with CLAHE and return PNG bytes + mime type."""
    if not image_bytes:
        raise ImagePreprocessError("empty image")

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ImagePreprocessError("could not decode image")

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

    if len(image.shape) == 2:
        enhanced = clahe.apply(image)
    elif image.shape[2] == 4:
        bgr = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
        enhanced = _clahe_lab(bgr, clahe)
    elif image.shape[2] == 3:
        enhanced = _clahe_lab(image, clahe)
    else:
        raise ImagePreprocessError("unsupported image channels")

    ok, encoded = cv2.imencode(".png", enhanced)
    if not ok:
        raise ImagePreprocessError("could not encode enhanced image")
    return encoded.tobytes(), "image/png"


def _clahe_lab(bgr: np.ndarray, clahe) -> np.ndarray:
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l_chan, a_chan, b_chan = cv2.split(lab)
    l_enhanced = clahe.apply(l_chan)
    merged = cv2.merge((l_enhanced, a_chan, b_chan))
    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)
