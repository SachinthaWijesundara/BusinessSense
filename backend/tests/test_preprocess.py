from __future__ import annotations

import cv2
import numpy as np
import pytest

from agentic.preprocess import (
    MAX_MODEL_SIDE,
    ImagePreprocessError,
    apply_clahe,
    decode_image,
    prepare_images,
)
from agentic.regions import REGION_NAMES
from tests.helpers import invoice_image


def _decode(data: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def test_prepare_images_outputs():
    prepared = prepare_images(invoice_image())
    assert prepared.full.mime == "image/jpeg"
    assert [crop.name for crop in prepared.regions] == list(REGION_NAMES)
    assert all(crop.mime == "image/jpeg" for crop in prepared.regions)
    assert prepared.preview_png.startswith(b"\x89PNG")
    assert set(prepared.region_previews_png) == set(REGION_NAMES)


def test_region_previews_can_be_skipped():
    assert prepare_images(invoice_image(), region_previews=False).region_previews_png == {}


def test_small_crops_are_zoomed():
    prepared = prepare_images(invoice_image(800, 1100))
    supplier_panel = _decode(prepared.regions[0].data)
    # 0.48 x 800 px wide band zoomed 2.5x
    assert supplier_panel.shape[1] == pytest.approx(800 * 0.48 * 2.5, abs=2)


def test_large_phone_photo_is_capped_at_2048():
    prepared = prepare_images(invoice_image(3000, 4000))
    full = _decode(prepared.full.data)
    assert max(full.shape[:2]) == MAX_MODEL_SIDE
    for crop in prepared.regions:
        assert max(_decode(crop.data).shape[:2]) <= MAX_MODEL_SIDE
    assert max(_decode(prepared.preview_png).shape[:2]) == MAX_MODEL_SIDE
    assert len(prepared.full.data) < 2_000_000


@pytest.mark.parametrize("ext", [".png", ".jpg", ".webp"])
def test_common_formats_decode(ext):
    assert prepare_images(invoice_image(600, 800, ext=ext)).full.data


def test_grayscale_and_alpha_images():
    gray = np.full((400, 300), 200, np.uint8)
    rgba = np.zeros((400, 300, 4), np.uint8)
    rgba[..., 3] = 255
    for image in (gray, rgba):
        ok, encoded = cv2.imencode(".png", image)
        assert ok
        assert decode_image(encoded.tobytes()).shape == (400, 300, 3)


def test_apply_clahe_compat_returns_png():
    data, mime = apply_clahe(invoice_image(600, 800))
    assert mime == "image/png" and data.startswith(b"\x89PNG")


def test_contrast_is_enhanced():
    faint = np.full((300, 300, 3), 200, np.uint8)
    cv2.putText(faint, "faint", (20, 150), cv2.FONT_HERSHEY_SIMPLEX, 2, (185, 185, 185), 3)
    ok, encoded = cv2.imencode(".png", faint)
    enhanced = _decode(apply_clahe(encoded.tobytes())[0])
    assert enhanced.std() > faint.std()


@pytest.mark.parametrize("data", [b"", b"not an image", b"\x89PNG\r\n\x1a\n broken"])
def test_bad_input_raises(data):
    with pytest.raises(ImagePreprocessError):
        prepare_images(data)
