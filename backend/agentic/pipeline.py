"""invoice_ingest: the code-controlled pipeline from photo to draft (proposal Figure 6).

    photo -> image clean-up -> InvoiceVisionAgent (+ output guardrail)
      invoice: -> math_audit (Python) -> save draft -> SkuMatchAgent (+ 0.82 gate) -> owner review
      receipt: -> one unconfirmed supplier payment per invoice it settles -> owner confirms

The order is fixed in code; there are no LLM handoffs. Nothing is saved when the guardrail trips.
An invoice that fails the math audit is saved as a draft with math_ok = false and is not matched
until the owner fixes it (POST /invoices/{id}/fix re-runs the audit). Receipt payments always
start unconfirmed; the owner checks them against the photo and confirms them.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from database.client import (
    DuplicateError,
    ExtractionRunLog,
    Invoice,
    InvoiceSummary,
    NewInvoice,
    NewInvoiceLine,
    NewPayment,
    NotFoundError,
    SupplierRecord,
)
from services.cash_flow_service import (
    ReceiptUpload,
    find_duplicate_receipt,
    payments_saved,
    with_receipt_links,
)
from services.invoice_service import (
    WorkflowError,
    date_issues,
    invoice_missing_fields,
    math_audit,
    parse_iso_date,
    run_sku_matching,
)

from .guardrails import missing_fields as document_missing_fields
from .schemas import ExtractedDocument, ReceiptChequeDraft, usable_lines
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


async def _store_photos(services: AppServices, business_id: str, folder: str, original: bytes,
                        content_type: str | None, images: PreparedImages, *, invoice_id: str | None = None) -> str | None:
    """Keep the original photo and the cleaned copy in the private bucket under ``folder`` (the
    invoice id, or receipts/<payment id>); returns the original's path."""
    if services.storage is None:
        return None
    extension = _EXTENSIONS.get((content_type or "").lower(), ".bin")
    original_path = f"{business_id}/{folder}/original{extension}"
    try:
        await services.storage.put(original_path, original, content_type or "application/octet-stream")
        await services.storage.put(f"{business_id}/{folder}/clahe.png", images.preview_png, "image/png")
    except Exception as exc:  # noqa: BLE001 - the invoice or receipt is still usable without its photo
        logger.exception("could not store document images")
        await services.db.log_event(business_id, invoice_id, "image_store_failed",
                                    {"error": str(exc)[:300], "folder": folder})
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


def _cheque_for(cheques: list[ReceiptChequeDraft], index: int, rows: int) -> ReceiptChequeDraft | None:
    """The cheque written for the ``index``-th invoice on a receipt: paired one-to-one when the
    receipt lists as many cheques as invoices, otherwise the first cheque."""
    if not cheques:
        return None
    return cheques[index] if len(cheques) == rows else cheques[0]


def _row_fields(low_confidence: list[str], index: int) -> list[str]:
    """The partly read fields that concern one receipt row (its own values and the receipt header)."""
    own = f"receipt.payments[{index}]."
    return [path for path in low_confidence if path.startswith(own) or not path.startswith("receipt.payments[")]


async def _receipt_supplier(services: AppServices, business_id: str, document: ExtractedDocument,
                            given: SupplierRecord | None) -> SupplierRecord | None:
    """The receipt's supplier: the one the owner chose, else the printed name, else the supplier of
    the only saved invoices carrying the numbers the receipt lists."""
    if given is not None:
        return given
    name = _clean(document.supplier.name)
    if name:
        matches = await services.db.find_suppliers_by_name(business_id, name)
        if matches:
            return matches[0]
    receipt = document.receipt
    numbers = [n for n in (_clean(p.invoice_number) for p in (receipt.payments if receipt else [])) if n]
    owners: set[str] = set()
    for number in numbers:
        for invoice in await services.db.find_invoices_by_number_any_supplier(business_id, number):
            if invoice.supplier_id and invoice.status != "rejected":
                owners.add(invoice.supplier_id)
    if len(owners) == 1:
        return await services.db.get_supplier(business_id, owners.pop())
    return None


async def _receipt_payments(services: AppServices, business_id: str, document: ExtractedDocument,
                            supplier: SupplierRecord | None) -> list[NewPayment]:
    """One unconfirmed payment per invoice the receipt settles, linked to the saved invoice with
    that number when there is one (a receipt with no readable line still gives one row to fill in)."""
    receipt = document.receipt
    assert receipt is not None
    paid_on = parse_iso_date(receipt.receipt_date)
    lines = [p for p in receipt.payments if _clean(p.invoice_number) or p.amount is not None] or [None]
    rows = []
    for index, line in enumerate(lines):
        number = _clean(line.invoice_number) if line else None
        invoice: InvoiceSummary | None = None
        if supplier is not None and number:
            found = await services.db.find_invoice_by_number(business_id, supplier.id, number)
            invoice = found if found is not None and found.status != "rejected" else None
        cheque = _cheque_for(receipt.cheques, index, len(lines))
        method = receipt.payment_method or ("cheque" if cheque and _clean(cheque.cheque_number) else None)
        amount = line.amount if line and line.amount is not None and 0 < line.amount < 1e10 else None
        rows.append(NewPayment(
            supplier_id=supplier.id if supplier else None,
            supplier_name_raw=_clean(document.supplier.name),
            invoice_id=invoice.id if invoice else None,
            invoice_number_raw=None if invoice else number,
            amount=round(amount, 2) if amount is not None else None,
            paid_on=paid_on,
            method=method,
            receipt_number=_clean(receipt.receipt_number),
            cheque_number=_clean(cheque.cheque_number) if cheque else None,
            bank_name=_clean(cheque.bank_name) if cheque else None,
            cheque_date=parse_iso_date(cheque.cheque_date) if cheque else None,
            status="unconfirmed",
            source="agent",
            low_confidence_fields=_row_fields(document.low_confidence_fields, index),
        ))
    return rows


async def receipt_ingest(images: PreparedImages, document: ExtractedDocument, *, services: AppServices,
                         business_id: str, supplier: SupplierRecord | None, image_bytes: bytes,
                         content_type: str | None, raw_output: dict, duration_ms: int) -> ReceiptUpload:
    """Save what InvoiceVisionAgent read from a payment receipt as unconfirmed supplier payments.

    A receipt already saved for the same supplier is refused (409). The receipt photo is kept so
    the owner can check every payment against it before confirming.
    """
    db = services.db
    supplier = await _receipt_supplier(services, business_id, document, supplier)
    receipt_number = _clean(document.receipt.receipt_number) if document.receipt else None
    clash = await find_duplicate_receipt(services, business_id, supplier.id if supplier else None,
                                         receipt_number, any_invoice=True)
    if clash:
        await _log_run(services, business_id, guardrail_passed=True, raw_output=raw_output,
                       error="duplicate receipt", duration_ms=duration_ms)
        raise WorkflowError(409, "duplicate_receipt", "That receipt is already saved for this supplier.",
                            payment_id=clash.id)

    rows = await _receipt_payments(services, business_id, document, supplier)
    try:
        payments = await db.create_payments(business_id, rows)
    except NotFoundError as exc:
        raise WorkflowError(404, "not_found", str(exc)) from exc
    image_path = await _store_photos(services, business_id, f"receipts/{payments[0].id}", image_bytes,
                                     content_type, images)
    if image_path:
        payments = [await db.update_payment(business_id, payment.id, {"image_path": image_path}) for payment in payments]
    await _log_run(services, business_id, guardrail_passed=True, raw_output=raw_output, error=None,
                   duration_ms=duration_ms)
    await payments_saved(services, business_id, payments, "receipt_extracted",
                         {"supplier_matched": supplier is not None})
    payments = await with_receipt_links(services, payments)
    return ReceiptUpload(
        receipt_number=receipt_number,
        supplier_id=supplier.id if supplier else None,
        supplier_name_raw=_clean(document.supplier.name),
        missing_fields=document_missing_fields(document),
        image_url=payments[0].image_url,
        payments=payments,
    )


async def invoice_ingest(image_bytes: bytes, *, services: AppServices, business_id: str,
                         supplier_id: str | None = None, content_type: str | None = None) -> Invoice | ReceiptUpload:
    """Run the whole pipeline for one uploaded photo: the saved draft invoice, or the unconfirmed
    payments saved from a payment receipt."""
    db = services.db
    supplier = None
    if supplier_id:
        supplier = await db.get_supplier(business_id, supplier_id)
        if supplier is None:
            raise WorkflowError(404, "not_found", "Supplier not found.")

    images, result = await _read_photo(services, business_id, image_bytes)
    document = result.document
    raw_output = document.model_dump(by_alias=True)
    if document.doc_type == "receipt" and document.receipt is not None:
        return await receipt_ingest(images, document, services=services, business_id=business_id, supplier=supplier,
                                    image_bytes=image_bytes, content_type=content_type, raw_output=raw_output,
                                    duration_ms=result.duration_ms)
    if document.invoice is None:
        await _log_run(services, business_id, guardrail_passed=False, raw_output=raw_output,
                       error="no invoice in the answer", duration_ms=result.duration_ms)
        raise WorkflowError(422, "reading_unsuccessful",
                            "Reading unsuccessful - nothing usable was read. Retake the photo or type it in.",
                            missing_fields=["invoice.lines", "invoice.invoice_date"])

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

    image_path = await _store_photos(services, business_id, invoice.id, image_bytes, content_type, images,
                                     invoice_id=invoice.id)
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
