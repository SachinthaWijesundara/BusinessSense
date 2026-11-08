from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .preprocess import ImagePreprocessError


@dataclass(frozen=True)
class RegionCrop:
    name: str
    png_bytes: bytes
    mime: str = "image/png"


# Geometry: fractions of (y0, y1, x0, x1) on the CLAHE image, then scale factor.
_REGION_SPECS: tuple[tuple[str, float, float, float, float, float], ...] = (
    ("supplier_panel", 0.0, 0.35, 0.0, 0.48, 2.5),
    ("header_right", 0.0, 0.35, 0.48, 1.0, 2.0),
    ("body", 0.32, 0.72, 0.0, 1.0, 1.5),
    ("footer", 0.68, 1.0, 0.0, 1.0, 1.5),
)


def build_region_crops(clahe_png_bytes: bytes) -> list[RegionCrop]:
    """Cut labeled header/body/footer bands from CLAHE PNG and upscale each."""
    if not clahe_png_bytes:
        raise ImagePreprocessError("empty image for region crops")

    arr = np.frombuffer(clahe_png_bytes, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None:
        raise ImagePreprocessError("could not decode CLAHE image for region crops")

    height, width = image.shape[:2]
    crops: list[RegionCrop] = []
    for name, y0f, y1f, x0f, x1f, scale in _REGION_SPECS:
        y0 = max(0, min(height - 1, int(height * y0f)))
        y1 = max(y0 + 1, min(height, int(height * y1f)))
        x0 = max(0, min(width - 1, int(width * x0f)))
        x1 = max(x0 + 1, min(width, int(width * x1f)))
        band = image[y0:y1, x0:x1]
        if band.size == 0:
            continue
        zoomed = cv2.resize(
            band,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_CUBIC,
        )
        ok, encoded = cv2.imencode(".png", zoomed)
        if not ok:
            raise ImagePreprocessError(f"could not encode region crop {name}")
        crops.append(RegionCrop(name=name, png_bytes=encoded.tobytes()))
    return crops
