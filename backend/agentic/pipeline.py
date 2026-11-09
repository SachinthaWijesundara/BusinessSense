"""invoice_ingest: the code-controlled pipeline from photo to draft invoice (proposal Figure 6).

    photo -> image clean-up -> InvoiceVisionAgent (+ output guardrail)
          -> math_audit (Python) -> save draft -> SkuMatchAgent (+ 0.82 gate) -> owner review

The order is fixed in code; there are no LLM handoffs. Nothing is saved when the guardrail trips.
An invoice that fails the math audit is saved as a draft with math_ok = false and is not matched
until the owner fixes it (POST /invoices/{id}/fix re-runs the audit).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from database.client import DuplicateError, ExtractionRunLog, Invoice, NewInvoice, NewInvoiceLine
from services.invoice_service import (
    WorkflowError,
    date_issues,
    invoice_missing_fields,
    math_audit,
    parse_iso_date,
    run_sku_matching,
)

from .schemas import ExtractedDocument, usable_lines
from .vision_agent import (
    ImagePreprocessError,
    PreparedImages,
    UnreadableDocument,
    VisionResult,
    VisionServiceError,
    prepare_images,
)

if TYPE_CHECKING:
    from main import AppServices

logger = logging.getLogger(__name__)

_EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}


def _clean(value: str | None) -> str | None:
    """Trim text; empty text becomes None."""
    if value is None:
        return None
    return value.strip() or None


async def _log_run(services: AppServices, business_id: str, *, invoice_id: str | None = None,
                   guardrail_passed: bool | None, raw_output: dict | None, error: str | None,
                   duration_ms: int | None = None) -> None:
    """Keep the raw agent output (proposal: store every raw agent output so nothing is re-sent)."""
    try:
        await services.db.log_extraction_run(
            business_id,
            ExtractionRunLog(invoice_id=invoice_id, agent="InvoiceVisionAgent", model=services.vision.model_name,
                             guardrail_passed=guardrail_passed, raw_output=raw_output, error=error,
                             duration_ms=duration_ms),
        )
    except Exception:  # noqa: BLE001 - logging must never fail the upload
        logger.exception("could not log extraction run")


async def _read_photo(services: AppServices, business_id: str, image_bytes: bytes) -> tuple[PreparedImages, VisionResult]:
    """Clean the photo and let InvoiceVisionAgent read it. Refuses unreadable photos (422) and
    failed model calls (502); in both cases nothing is saved except the extraction log."""
    if not image_bytes:
        raise WorkflowError(422, "reading_unsuccessful", "Reading unsuccessful - empty image.", missing_fields=["image"])
    try:
        images = await asyncio.to_thread(prepare_images, image_bytes)
    except ImagePreprocessError as exc:
        raise WorkflowError(
            422, "reading_unsuccessful",
            f"Reading unsuccessful - could not process this image ({exc}). Retake the photo or type the invoice in.",
            missing_fields=["image"],
        ) from exc
    try:
        result = await services.vision.read(images)
    except UnreadableDocument as exc:
        await _log_run(services, business_id, guardrail_passed=False, raw_output=exc.document,
                       error=f"output guardrail: {', '.join(exc.blocking_fields)}")
        raise WorkflowError(
            422, "reading_unsuccessful",
            "Reading unsuccessful - no line items or no date found. Retake the photo or type the invoice in.",
            missing_fields=list(dict.fromkeys([*exc.blocking_fields, *exc.missing_fields])),
        ) from exc
    except VisionServiceError as exc:
        await _log_run(services, business_id, guardrail_passed=None, raw_output=None, error=str(exc))
        raise WorkflowError(502, "api_error", f"API error - extract service failed: {exc}") from exc
    return images, result


async def _store_photos(services: AppServices, business_id: str, invoice_id: str, original: bytes,
                        content_type: str | None, images: PreparedImages) -> str | None:
    """Keep the original photo and the cleaned copy in the private bucket; returns the original's path."""
    if services.storage is None:
        return None
    extension = _EXTENSIONS.get((content_type or "").lower(), ".bin")
    original_path = f"{business_id}/{invoice_id}/original{extension}"
    try:
        await services.storage.put(original_path, original, content_type or "application/octet-stream")
        await services.storage.put(f"{business_id}/{invoice_id}/clahe.png", images.preview_png, "image/png")
    except Exception as exc:  # noqa: BLE001 - the invoice is still usable without its photo
        logger.exception("could not store invoice images")
        await services.db.log_event(business_id, invoice_id, "image_store_failed", {"error": str(exc)[:300]})
        return None
    return original_path


def _draft_to_new_invoice(document: ExtractedDocument, supplier_id: str | None, tolerance: float) -> tuple[NewInvoice, list[NewInvoiceLine], bool]:
    """Audit the agent's draft and turn it into an invoice and lines to save (plus whether the math passed)."""
    draft = document.invoice
    assert draft is not None
    audit = math_audit(draft, tolerance)
    field_issues = date_issues(draft)
    invalid = {issue.field for issue in field_issues if issue.code == "invalid_date"}
    lines = [
        NewInvoiceLine(sort_order=i, sku=_clean(line.sku), item_name=(line.item_name or "").strip(),
                       quantity=line.quantity, mro=line.unit_rate, discount=line.discount or 0.0,
                       line_total=line.line_total)
        for i, line in enumerate(usable_lines(draft))
    ]
    invoice = NewInvoice(
        supplier_id=supplier_id,
        supplier_name_raw=_clean(document.supplier.name),
        invoice_number=_clean(draft.invoice_number),
        invoice_date=None if "invoice_date" in invalid else parse_iso_date(draft.invoice_date),
        delivery_date=None if "delivery_date" in invalid else parse_iso_date(draft.delivery_date),
        payment_type=draft.payment_type,
        credit_days=draft.credit_days if draft.credit_days is None or draft.credit_days >= 0 else None,
        special_discount=draft.special_discount or 0.0,
        tax=draft.tax or 0.0,
        total=draft.total,
        status="draft",
        source="agent",
        math_ok=audit.ok,
        math_issues=audit.issues_as_dicts(),
        field_issues=[issue.to_dict() for issue in field_issues],
        low_confidence_fields=document.low_confidence_fields,
    )
    invoice.missing_fields = invoice_missing_fields(invoice, has_lines=bool(lines))
    return invoice, lines, audit.ok


async def invoice_ingest(image_bytes: bytes, *, services: AppServices, business_id: str,
                         supplier_id: str | None = None, content_type: str | None = None) -> Invoice:
    """Run the whole pipeline for one uploaded photo and return the saved draft invoice."""
    db = services.db
    supplier = None
    if supplier_id:
        supplier = await db.get_supplier(business_id, supplier_id)
        if supplier is None:
            raise WorkflowError(404, "not_found", "Supplier not found.")

    images, result = await _read_photo(services, business_id, image_bytes)
    document = result.document
    raw_output = document.model_dump(by_alias=True)
    if document.doc_type != "invoice" or document.invoice is None:
        await _log_run(services, business_id, guardrail_passed=True, raw_output=raw_output, error="receipt upload",
                       duration_ms=result.duration_ms)
        raise WorkflowError(422, "unsupported_document",
                            "This photo looks like a payment receipt. Receipt upload is not supported yet.")

    if supplier is None and _clean(document.supplier.name):
        matches = await db.find_suppliers_by_name(business_id, document.supplier.name or "")
        supplier = matches[0] if matches else None

    number = _clean(document.invoice.invoice_number)
    if supplier and number:
        existing = await db.find_invoice_by_number(business_id, supplier.id, number)
        if existing:
            await _log_run(services, business_id, invoice_id=existing.id, guardrail_passed=True,
                           raw_output=raw_output, error="duplicate invoice", duration_ms=result.duration_ms)
            raise WorkflowError(409, "duplicate_invoice", "That invoice number is already saved for this supplier.",
                                invoice_id=existing.id)

    new_invoice, lines, math_ok = _draft_to_new_invoice(document, supplier.id if supplier else None,
                                                        services.settings.math_tolerance)
    try:
        invoice = await db.create_invoice(business_id, new_invoice, lines)
    except DuplicateError as exc:
        raise WorkflowError(409, "duplicate_invoice", "That invoice number is already saved for this supplier.") from exc

    image_path = await _store_photos(services, business_id, invoice.id, image_bytes, content_type, images)
    if image_path:
        invoice = await db.update_invoice(business_id, invoice.id, {"image_path": image_path})
    await _log_run(services, business_id, invoice_id=invoice.id, guardrail_passed=True, raw_output=raw_output,
                   error=None, duration_ms=result.duration_ms)
    await db.log_event(business_id, invoice.id, "invoice_extracted",
                       {"math_ok": math_ok, "missing_fields": invoice.missing_fields,
                        "supplier_matched": supplier is not None, "lines": len(lines)})
    if math_ok:
        invoice = await run_sku_matching(services, business_id, invoice)
    return invoice
