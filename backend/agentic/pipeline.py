from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .guardrails import missing_fields
from .preprocess import ImagePreprocessError, apply_clahe
from .regions import RegionCrop, build_region_crops
from .schemas import ExtractedDocument
from .vision_agent import extract_with_vision

_PREVIEW_DIR = Path(__file__).resolve().parent.parent / "debug_previews"


@dataclass
class ExtractResult:
    ok: bool
    document: ExtractedDocument | None = None
    error_type: str | None = None
    message: str | None = None
    missing: list[str] | None = None
    partial: bool = False
    preview_png_base64: str | None = None
    region_previews: dict[str, str] = field(default_factory=dict)


def _usable_document(doc: ExtractedDocument) -> bool:
    """True when there is something the UI can prefill."""
    if doc.doc_type == "invoice":
        inv = doc.invoice
        if inv is None:
            return bool(doc.supplier and doc.supplier.name and str(doc.supplier.name).strip())
        if inv.invoice_number and str(inv.invoice_number).strip():
            return True
        if inv.invoice_date and str(inv.invoice_date).strip():
            return True
        if any(line.item_name and str(line.item_name).strip() for line in inv.lines):
            return True
        return bool(doc.supplier and doc.supplier.name and str(doc.supplier.name).strip())

    rec = doc.receipt
    if rec is None:
        return bool(doc.supplier and doc.supplier.name and str(doc.supplier.name).strip())
    if rec.receipt_number and str(rec.receipt_number).strip():
        return True
    if any(
        (p.invoice_number and str(p.invoice_number).strip()) or (p.amount is not None)
        for p in rec.payments
    ):
        return True
    return bool(doc.supplier and doc.supplier.name and str(doc.supplier.name).strip())


def _save_previews(
    enhanced_bytes: bytes,
    regions: list[RegionCrop],
) -> tuple[str, dict[str, str]]:
    """Persist full CLAHE + crop PNGs; return full b64 and crop name→b64 map."""
    _PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")

    full_path = _PREVIEW_DIR / f"clahe_{stamp}.png"
    full_path.write_bytes(enhanced_bytes)

    region_b64: dict[str, str] = {}
    for crop in regions:
        crop_path = _PREVIEW_DIR / f"{crop.name}_{stamp}.png"
        crop_path.write_bytes(crop.png_bytes)
        region_b64[crop.name] = base64.b64encode(crop.png_bytes).decode("ascii")

    # Keep folder tidy: leave only the newest 20 of each prefix
    for pattern in ("clahe_*.png", "supplier_panel_*.png", "header_right_*.png", "body_*.png", "footer_*.png"):
        files = sorted(_PREVIEW_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[20:]:
            try:
                old.unlink()
            except OSError:
                pass

    return base64.b64encode(enhanced_bytes).decode("ascii"), region_b64


def extract_document(
    image_bytes: bytes,
    *,
    filename: str | None = None,
    content_type: str | None = None,
) -> ExtractResult:
    del filename, content_type  # original mime unused after CLAHE → PNG
    if not image_bytes:
        return ExtractResult(
            ok=False,
            error_type="reading_unsuccessful",
            message="Reading unsuccessful — empty image. Retake the photo or fill the form manually.",
            missing=["image"],
        )

    try:
        enhanced_bytes, mime = apply_clahe(image_bytes)
        regions = build_region_crops(enhanced_bytes)
    except ImagePreprocessError as exc:
        return ExtractResult(
            ok=False,
            error_type="reading_unsuccessful",
            message=(
                "Reading unsuccessful — could not process this image "
                f"({exc}). Retake the photo or fill the form manually."
            ),
            missing=["image"],
        )

    preview_b64, region_previews = _save_previews(enhanced_bytes, regions)

    try:
        doc = extract_with_vision(enhanced_bytes, mime=mime, regions=regions)
    except Exception as exc:  # noqa: BLE001 — surface as API error to the client
        return ExtractResult(
            ok=False,
            error_type="api_error",
            message=f"API error — extract service failed: {exc}",
            missing=[],
            preview_png_base64=preview_b64,
            region_previews=region_previews,
        )

    if not _usable_document(doc):
        return ExtractResult(
            ok=False,
            document=doc,
            error_type="reading_unsuccessful",
            message=(
                "Reading unsuccessful — could not read this document. "
                "Retake the photo or fill the form manually."
            ),
            missing=missing_fields(doc) or ["document"],
            preview_png_base64=preview_b64,
            region_previews=region_previews,
        )

    missing = missing_fields(doc)
    return ExtractResult(
        ok=True,
        document=doc,
        missing=missing,
        partial=len(missing) > 0,
        message=(
            "Partial read — some fields could not be read. Complete the highlighted fields."
            if missing
            else None
        ),
        preview_png_base64=preview_b64,
        region_previews=region_previews,
    )
