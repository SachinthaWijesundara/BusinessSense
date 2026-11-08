"""Image clean-up before the vision agent reads a photo.

CLAHE (contrast-limited adaptive histogram equalisation) lifts faint dot-matrix print. The model
receives the full page plus four zoomed crops (see ``regions.py``) as JPEGs capped at 2048 px,
which is the largest size OpenAI uses for high-detail images, so nothing is lost while uploads
stay small. A PNG preview of the CLAHE page goes back to the UI and to Storage.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .regions import crop_regions

MAX_MODEL_SIDE = 2048
JPEG_QUALITY = 92


class ImagePreprocessError(ValueError):
    """Raised when the uploaded image cannot be decoded or enhanced."""


@dataclass(frozen=True)
class EncodedImage:
    name: str
    data: bytes
    mime: str


@dataclass(frozen=True)
class PreparedImages:
    full: EncodedImage
    regions: list[EncodedImage]
    preview_png: bytes
    region_previews_png: dict[str, bytes] = field(default_factory=dict)


def decode_image(image_bytes: bytes) -> np.ndarray:
    """Decode to 3-channel BGR. IMREAD_COLOR also applies the EXIF rotation of phone photos."""
    if not image_bytes:
        raise ImagePreprocessError("empty image")
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise ImagePreprocessError("could not decode image")
    return image


def apply_clahe_array(image: np.ndarray) -> np.ndarray:
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l_chan, a_chan, b_chan = cv2.split(lab)
    merged = cv2.merge((clahe.apply(l_chan), a_chan, b_chan))
    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)


def limit_longest_side(image: np.ndarray, max_side: int = MAX_MODEL_SIDE) -> np.ndarray:
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= max_side:
        return image
    scale = max_side / longest
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return cv2.resize(image, size, interpolation=cv2.INTER_AREA)


def encode_png(image: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise ImagePreprocessError("could not encode PNG")
    return encoded.tobytes()


def encode_jpeg(image: np.ndarray, quality: int = JPEG_QUALITY) -> bytes:
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise ImagePreprocessError("could not encode JPEG")
    return encoded.tobytes()


def apply_clahe(image_bytes: bytes) -> tuple[bytes, str]:
    """CLAHE-enhance an encoded image; returns PNG bytes (capped at 2048 px) and the mime type."""
    enhanced = apply_clahe_array(decode_image(image_bytes))
    return encode_png(limit_longest_side(enhanced)), "image/png"


def prepare_images(image_bytes: bytes, *, region_previews: bool = True) -> PreparedImages:
    """Decode, enhance and cut the images InvoiceVisionAgent reads."""
    enhanced = apply_clahe_array(decode_image(image_bytes))
    page = limit_longest_side(enhanced)
    crops = crop_regions(enhanced, max_side=MAX_MODEL_SIDE)
    return PreparedImages(
        full=EncodedImage("full", encode_jpeg(page), "image/jpeg"),
        regions=[EncodedImage(name, encode_jpeg(crop), "image/jpeg") for name, crop in crops],
        preview_png=encode_png(page),
        region_previews_png=(
            {name: encode_png(crop) for name, crop in crops} if region_previews else {}
        ),
    )
