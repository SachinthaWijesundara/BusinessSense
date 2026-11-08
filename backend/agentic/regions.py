"""Zoomed crops of the invoice layout, so the model reads small print at higher resolution.

The geometry fits the common Sri Lankan supplier layout: supplier letterhead top-left, invoice
number and purchaser top-right, the line-item table in the middle, totals at the bottom.
"""

from __future__ import annotations

import cv2
import numpy as np

# (name, y0, y1, x0, x1, zoom) as fractions of the page height/width.
REGION_SPECS: tuple[tuple[str, float, float, float, float, float], ...] = (
    ("supplier_panel", 0.0, 0.35, 0.0, 0.48, 2.5),
    ("header_right", 0.0, 0.35, 0.48, 1.0, 2.0),
    ("body", 0.32, 0.72, 0.0, 1.0, 1.5),
    ("footer", 0.68, 1.0, 0.0, 1.0, 1.5),
)

REGION_NAMES = tuple(spec[0] for spec in REGION_SPECS)


def crop_regions(image: np.ndarray, *, max_side: int = 2048) -> list[tuple[str, np.ndarray]]:
    """Cut and zoom each region. The zoom never pushes a crop past ``max_side`` pixels, and a
    crop already larger than that is shrunk to it (the model would downscale it anyway)."""
    height, width = image.shape[:2]
    crops: list[tuple[str, np.ndarray]] = []
    for name, y0f, y1f, x0f, x1f, zoom in REGION_SPECS:
        y0 = max(0, min(height - 1, int(height * y0f)))
        y1 = max(y0 + 1, min(height, int(height * y1f)))
        x0 = max(0, min(width - 1, int(width * x0f)))
        x1 = max(x0 + 1, min(width, int(width * x1f)))
        band = image[y0:y1, x0:x1]
        if band.size == 0:
            continue
        band_h, band_w = band.shape[:2]
        scale = min(zoom, max_side / max(band_h, band_w))
        if abs(scale - 1.0) < 1e-6:
            crops.append((name, band.copy()))
            continue
        size = (max(1, round(band_w * scale)), max(1, round(band_h * scale)))
        interpolation = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
        crops.append((name, cv2.resize(band, size, interpolation=interpolation)))
    return crops
