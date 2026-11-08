"""invoice_ingest: the code-controlled pipeline from photo to draft invoice (proposal Figure 6).

    photo -> CLAHE + crops -> InvoiceVisionAgent (+ output guardrail)
          -> math_audit (Python) -> save draft -> SkuMatchAgent (+ 0.82 gate) -> owner review

The order is fixed in code; there are no LLM handoffs. Nothing is saved when the guardrail trips.
An invoice that fails the math audit is saved as a draft with math_ok = false and is not matched
until the owner fixes it (POST /invoices/{id}/fix re-runs the audit).
"""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from database.models import ExtractionRunLog, InvoiceRecord, NewInvoice, NewInvoiceLine
from database.repository import DuplicateError
from services.errors import WorkflowError
from services.invoice_service import date_issues, math_audit, parse_iso_date
from services.invoice_workflow import invoice_missing_fields, run_sku_matching

from .guardrails import missing_fields
from .preprocess import ImagePreprocessError, PreparedImages, prepare_images
from .schemas import ExtractedDocument, usable_lines
from .vision_agent import UnreadableDocument, VisionServiceError

if TYPE_CHECKING:
    from services.container import AppServices

logger = logging.getLogger(__name__)

_EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}


@dataclass
class ReadOutcome:
    ok: bool
    document: ExtractedDocument | None = None
    error_type: str | None = None
    message: str | None = None
    missing: list[str] = field(default_factory=list)
    partial: bool = False
    images: PreparedImages | None = None
    model: str | None = None
    duration_ms: int | None = None
    guardrail_passed: bool | None = None
    raw_output: dict[str, Any] | None = None
    run_error: str | None = None

    @property
    def preview_png_base64(self) -> str | None:
        if self.images is None:
            return None
        return base64.b64encode(self.images.preview_png).decode("ascii")

    @property
    def region_previews_base64(self) -> dict[str, str]:
        if self.images is None:
            return {}
        return {
            name: base64.b64encode(data).decode("ascii")
            for name, data in self.images.region_previews_png.items()
        }


def _save_debug_previews(images: PreparedImages, directory: str | None) -> None:
    if not directory:
        return
    try:
        folder = Path(directory)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        (folder / f"clahe_{stamp}.png").write_bytes(images.preview_png)
        for crop in images.regions:
            (folder / f"{crop.name}_{stamp}.jpg").write_bytes(crop.data)
    except OSError:
        logger.exception("could not write debug previews")


async def read_document(
    image_bytes: bytes, services: AppServices, *, group_id: str | None = None
) -> ReadOutcome:
    """Preprocess and read one photo. Saves nothing; callers decide what to keep."""
    if not image_bytes:
        return ReadOutcome(
            ok=False,
            error_type="reading_unsuccessful",
            message="Reading unsuccessful - empty image. Retake the photo or fill the form manually.",
            missing=["image"],
        )
    try:
        images = await asyncio.to_thread(
            prepare_images,
            image_bytes,
            region_previews=services.settings.extract_region_previews,
        )
    except ImagePreprocessError as exc:
        return ReadOutcome(
            ok=False,
            error_type="reading_unsuccessful",
            message=(
                f"Reading unsuccessful - could not process this image ({exc}). "
                "Retake the photo or fill the form manually."
            ),
            missing=["image"],
        )
    _save_debug_previews(images, services.settings.debug_previews_dir)

    vision = services.vision
    try:
        result = await vision.read(images, group_id=group_id)
    except UnreadableDocument as exc:
        document = None
        if exc.document:
            try:
                document = ExtractedDocument.model_validate(exc.document)
            except ValueError:
                document = None
        missing = list(dict.fromkeys([*exc.blocking_fields, *exc.missing_fields]))
        return ReadOutcome(
            ok=False,
            document=document,
            error_type="reading_unsuccessful",
            message=(
                "Reading unsuccessful - could not read this document "
                "(no line items or no date found). Retake the photo or fill the form manually."
            ),
            missing=missing,
            images=images,
            model=vision.model_name,
            guardrail_passed=False,
            raw_output=exc.document,
            run_error=f"output guardrail: {', '.join(exc.blocking_fields)}",
        )
    except VisionServiceError as exc:
        return ReadOutcome(
            ok=False,
            error_type="api_error",
            message=f"API error - extract service failed: {exc}",
            images=images,
            model=vision.model_name,
            run_error=str(exc),
        )

    document = result.document
    missing = missing_fields(document)
    return ReadOutcome(
        ok=True,
        document=document,
        missing=missing,
        partial=bool(missing),
        message=(
            "Partial read - some fields could not be read. Complete the highlighted fields."
            if missing
            else None
        ),
        images=images,
        model=result.model,
        duration_ms=result.duration_ms,
        guardrail_passed=True,
        raw_output=document.model_dump(by_alias=True),
    )


async def log_read(
    services: AppServices, business_id: str, outcome: ReadOutcome, *, invoice_id: str | None = None
) -> None:
    """Keep every raw agent output (proposal: 'store every raw agent output')."""
    if outcome.model is None:
        return  # the image never reached the agent
    try:
        await services.repo.log_extraction_run(
            business_id,
            ExtractionRunLog(
                invoice_id=invoice_id,
                agent="InvoiceVisionAgent",
                model=outcome.model,
                guardrail_passed=outcome.guardrail_passed,
                raw_output=outcome.raw_output,
                error=outcome.run_error,
                duration_ms=outcome.duration_ms,
            ),
        )
    except Exception:  # noqa: BLE001 - logging must never fail the request
        logger.exception("could not log extraction run")


async def _store_images(
    services: AppServices,
    business_id: str,
    invoice_id: str,
    original: bytes,
    content_type: str | None,
    images: PreparedImages,
) -> str | None:
    store = services.images
    if not store.enabled:
        return None
    extension = _EXTENSIONS.get((content_type or "").lower(), ".bin")
    original_path = f"{business_id}/{invoice_id}/original{extension}"
    try:
        await store.put(original_path, original, content_type or "application/octet-stream")
        await store.put(f"{business_id}/{invoice_id}/clahe.png", images.preview_png, "image/png")
    except Exception as exc:  # noqa: BLE001 - the invoice is still usable without its photo
        logger.exception("could not store invoice images")
        await services.repo.log_event(
            business_id, invoice_id, "image_store_failed", {"error": str(exc)[:300]}
        )
        return None
    return original_path


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


async def invoice_ingest(
    image_bytes: bytes,
    *,
    services: AppServices,
    business_id: str,
    supplier_id: str | None = None,
    content_type: str | None = None,
) -> InvoiceRecord:
    repo = services.repo
    supplier = None
    if supplier_id:
        supplier = await repo.get_supplier(business_id, supplier_id)
        if supplier is None:
            raise WorkflowError(404, "not_found", "Supplier not found.")

    outcome = await read_document(image_bytes, services)
    if not outcome.ok:
        await log_read(services, business_id, outcome)
        raise WorkflowError(
            422 if outcome.error_type == "reading_unsuccessful" else 502,
            outcome.error_type or "api_error",
            outcome.message or "Extraction failed.",
            missing_fields=outcome.missing,
        )
    document = outcome.document
    assert document is not None and outcome.images is not None
    if document.doc_type != "invoice" or document.invoice is None:
        await log_read(services, business_id, outcome)
        raise WorkflowError(
            422,
            "unsupported_document",
            "This photo looks like a payment receipt. Receipt upload is not supported yet.",
            document=document.model_dump(by_alias=True),
        )

    draft = document.invoice
    if supplier is None and _clean(document.supplier.name):
        matches = await repo.find_suppliers_by_name(business_id, document.supplier.name or "")
        supplier = matches[0] if matches else None

    invoice_number = _clean(draft.invoice_number)
    if supplier and invoice_number:
        existing = await repo.find_invoice_by_number(business_id, supplier.id, invoice_number)
        if existing:
            await log_read(services, business_id, outcome, invoice_id=existing.id)
            raise WorkflowError(
                409,
                "duplicate_invoice",
                "That invoice number is already saved for this supplier.",
                invoice_id=existing.id,
            )

    audit = math_audit(draft, services.settings.math_tolerance)
    field_issues = date_issues(draft)
    invalid_dates = {issue.field for issue in field_issues if issue.code == "invalid_date"}
    lines = [
        NewInvoiceLine(
            sort_order=index,
            sku=_clean(line.sku),
            item_name=(line.item_name or "").strip(),
            quantity=line.quantity,
            mro=line.unit_rate,
            discount=line.discount or 0.0,
            line_total=line.line_total,
        )
        for index, line in enumerate(usable_lines(draft))
    ]
    new_invoice = NewInvoice(
        supplier_id=supplier.id if supplier else None,
        supplier_name_raw=_clean(document.supplier.name),
        invoice_number=invoice_number,
        invoice_date=None if "invoice_date" in invalid_dates else parse_iso_date(draft.invoice_date),
        delivery_date=None if "delivery_date" in invalid_dates else parse_iso_date(draft.delivery_date),
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
    new_invoice.missing_fields = invoice_missing_fields(new_invoice, has_lines=bool(lines))
    try:
        record = await repo.create_invoice(business_id, new_invoice, lines)
    except DuplicateError as exc:
        raise WorkflowError(
            409, "duplicate_invoice", "That invoice number is already saved for this supplier."
        ) from exc

    image_path = await _store_images(
        services, business_id, record.id, image_bytes, content_type, outcome.images
    )
    if image_path:
        record = await repo.update_invoice(business_id, record.id, {"image_path": image_path})
    await log_read(services, business_id, outcome, invoice_id=record.id)
    await repo.log_event(
        business_id,
        record.id,
        "invoice_extracted",
        {
            "math_ok": audit.ok,
            "missing_fields": record.missing_fields,
            "supplier_matched": supplier is not None,
            "lines": len(lines),
        },
    )
    if audit.ok:
        record = await run_sku_matching(services, business_id, record)
    return record
