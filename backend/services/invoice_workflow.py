"""Owner steps after extraction: fix (PendingFix), confirm a product (HumanConfirm), verify.

Only a verified invoice whose lines are all linked to products may feed supplier and item
histories, Model 1 and Model 2. Every correction, confirmation and verification is logged.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentic.schemas import InvoiceDraft, InvoiceLineDraft
from agentic.sku_match_agent import LineToMatch
from database.models import (
    InvoiceLineRecord,
    InvoiceRecord,
    LineMatchUpdate,
    NewInvoice,
    NewInvoiceLine,
    NewProduct,
    ProductRecord,
    UUIDStr,
)
from database.repository import DuplicateError, NotFoundError

from .alias_service import remember_alias
from .errors import WorkflowError
from .invoice_service import compute_due_date, date_issues, math_audit
from .text import normalize_text

if TYPE_CHECKING:
    from .container import AppServices

logger = logging.getLogger(__name__)


# Request bodies ------------------------------------------------------------------------------
class LineInput(BaseModel):
    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True, serialize_by_alias=True)

    id: UUIDStr | None = Field(None, description="Existing line id, to keep its product match.")
    sku: str | None = None
    item_name: str = Field(min_length=1)
    quantity: float | None = None
    unit_rate: float | None = Field(None, alias="mro", description="Unit rate in rupees.")
    discount: float | None = 0.0
    line_total: float | None = None


class InvoiceFix(BaseModel):
    """Fields the owner corrects. Only the fields sent are changed; ``lines`` replaces all lines."""

    supplier_id: UUIDStr | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    delivery_date: date | None = None
    payment_type: Literal["cash", "credit"] | None = None
    credit_days: int | None = Field(None, ge=0)
    special_discount: float | None = None
    tax: float | None = None
    total: float | None = None
    lines: list[LineInput] | None = None


class ConfirmSku(BaseModel):
    line_id: UUIDStr
    product_id: UUIDStr | None = None
    new_product: NewProduct | None = None
    remember_alias: bool = True

    @model_validator(mode="after")
    def _one_target(self) -> ConfirmSku:
        if (self.product_id is None) == (self.new_product is None):
            raise ValueError("send exactly one of product_id or new_product")
        return self


# Helpers -------------------------------------------------------------------------------------
def invoice_missing_fields(invoice: NewInvoice | InvoiceRecord, *, has_lines: bool) -> list[str]:
    missing = []
    if not invoice.supplier_id:
        missing.append("supplier_id")
    if not (invoice.invoice_number or "").strip():
        missing.append("invoice_number")
    if invoice.invoice_date is None:
        missing.append("invoice_date")
    if not has_lines:
        missing.append("lines")
    if invoice.total is None:
        missing.append("total")
    return missing


def _as_draft(header: dict[str, Any], lines: list[NewInvoiceLine] | list[InvoiceLineRecord]) -> InvoiceDraft:
    return InvoiceDraft(
        invoice_number=header.get("invoice_number"),
        invoice_date=header["invoice_date"].isoformat() if header.get("invoice_date") else None,
        delivery_date=header["delivery_date"].isoformat() if header.get("delivery_date") else None,
        special_discount=header.get("special_discount"),
        tax=header.get("tax"),
        total=header.get("total"),
        lines=[
            InvoiceLineDraft(
                sku=line.sku,
                item_name=line.item_name,
                quantity=line.quantity,
                mro=line.unit_rate,
                discount=line.discount,
                line_total=line.line_total,
            )
            for line in lines
        ],
    )


async def _get_invoice(services: AppServices, business_id: str, invoice_id: str) -> InvoiceRecord:
    invoice = await services.repo.get_invoice(business_id, invoice_id)
    if invoice is None:
        raise WorkflowError(404, "not_found", "Invoice not found.")
    return invoice


async def run_sku_matching(
    services: AppServices, business_id: str, invoice: InvoiceRecord
) -> InvoiceRecord:
    """Match every line that is not linked yet, then store results line by line."""
    repo = services.repo
    targets = [line for line in invoice.lines if line.match_status != "linked"]
    if not targets:
        return invoice
    to_match = [
        LineToMatch(line_index=index, raw_text=line.item_name, sku=line.sku)
        for index, line in enumerate(targets)
    ]
    try:
        results = await services.sku_matcher.match(business_id, to_match, group_id=invoice.id)
    except Exception as exc:  # noqa: BLE001 - matching is retried on the next fix
        logger.exception("SKU matching failed")
        for line in targets:
            await repo.update_line_match(
                business_id,
                invoice.id,
                line.id,
                LineMatchUpdate(
                    match_status="pending",
                    match_reason=f"Product matching is unavailable ({exc}); pick the product.",
                ),
            )
        await repo.log_event(business_id, invoice.id, "sku_match_failed", {"error": str(exc)[:300]})
        return await _get_invoice(services, business_id, invoice.id)

    counts = {"linked": 0, "pending": 0, "new_product": 0}
    for line, result in zip(targets, results, strict=True):
        counts[result.status] += 1
        await repo.update_line_match(
            business_id,
            invoice.id,
            line.id,
            LineMatchUpdate(
                product_id=result.product_id,
                suggested_product_id=result.suggested_product_id,
                match_status=result.status,
                match_similarity=None if result.similarity is None else round(result.similarity, 4),
                match_candidates=result.candidates_as_dicts(),
                match_reason=result.reason or None,
            ),
        )
    await repo.log_event(
        business_id,
        invoice.id,
        "sku_matched",
        {**counts, "threshold": services.sku_matcher.threshold},
    )
    return await _get_invoice(services, business_id, invoice.id)


# Owner steps ---------------------------------------------------------------------------------
async def fix_invoice(
    services: AppServices, business_id: str, invoice_id: str, fix: InvoiceFix
) -> InvoiceRecord:
    repo = services.repo
    current = await _get_invoice(services, business_id, invoice_id)
    sent = fix.model_dump(exclude_unset=True)
    line_inputs = sent.pop("lines", None)

    if sent.get("supplier_id") and await repo.get_supplier(business_id, sent["supplier_id"]) is None:
        raise WorkflowError(404, "not_found", "Supplier not found.")
    if "invoice_number" in sent:
        sent["invoice_number"] = (sent["invoice_number"] or "").strip() or None
    for money_field in ("special_discount", "tax"):
        if money_field in sent and sent[money_field] is None:
            sent[money_field] = 0.0

    new_lines: list[NewInvoiceLine] | None = None
    if line_inputs is not None:
        previous = {line.id: line for line in current.lines}
        new_lines = []
        for index, raw in enumerate(fix.lines or []):
            kept = previous.get(raw.id or "")
            same_item = kept is not None and normalize_text(kept.item_name) == normalize_text(raw.item_name)
            match = (
                {
                    "product_id": kept.product_id,
                    "suggested_product_id": kept.suggested_product_id,
                    "match_status": kept.match_status,
                    "match_similarity": kept.match_similarity,
                    "match_candidates": kept.match_candidates,
                    "match_reason": kept.match_reason,
                }
                if same_item and kept is not None
                else {}
            )
            new_lines.append(
                NewInvoiceLine(
                    sort_order=index,
                    sku=(raw.sku or "").strip() or None,
                    item_name=raw.item_name.strip(),
                    quantity=raw.quantity,
                    mro=raw.unit_rate,
                    discount=raw.discount or 0.0,
                    line_total=raw.line_total,
                    **match,
                )
            )

    merged = {**current.model_dump(), **sent}
    effective_lines = new_lines if new_lines is not None else current.lines
    draft = _as_draft(merged, effective_lines)
    audit = math_audit(draft, services.settings.math_tolerance)
    field_issues = date_issues(draft)
    merged_invoice = NewInvoice.model_validate({**merged, "lines": None})

    supplier_id = merged.get("supplier_id")
    number = merged.get("invoice_number")
    if supplier_id and number:
        clash = await repo.find_invoice_by_number(business_id, supplier_id, number, exclude_id=invoice_id)
        if clash:
            raise WorkflowError(
                409,
                "duplicate_invoice",
                "That invoice number is already saved for this supplier.",
                invoice_id=clash.id,
            )

    changes: dict[str, Any] = {
        **sent,
        "math_ok": audit.ok,
        "math_issues": audit.issues_as_dicts(),
        "field_issues": [issue.to_dict() for issue in field_issues],
        "missing_fields": invoice_missing_fields(merged_invoice, has_lines=bool(effective_lines)),
        "status": "draft",
        "verified_at": None,
    }
    try:
        updated = await repo.update_invoice(business_id, invoice_id, changes, new_lines)
    except DuplicateError as exc:
        raise WorkflowError(409, "duplicate_invoice", str(exc)) from exc

    await repo.log_event(
        business_id,
        invoice_id,
        "invoice_corrected",
        {
            "fields": sorted(sent) + (["lines"] if line_inputs is not None else []),
            "previous_status": current.status,
            "math_ok": audit.ok,
        },
    )
    if audit.ok:
        updated = await run_sku_matching(services, business_id, updated)
    return updated


async def create_product_with_alias(
    services: AppServices, business_id: str, data: NewProduct
) -> ProductRecord:
    try:
        product = await services.repo.create_product(business_id, data)
    except DuplicateError as exc:
        raise WorkflowError(409, "duplicate_product", f"A product named '{data.name}' already exists.") from exc
    await _remember(services, business_id, product.id, product.name, source="catalogue")
    return product


async def _remember(
    services: AppServices, business_id: str, product_id: str, raw_text: str, *, source: str
) -> None:
    try:
        await remember_alias(
            services.repo, services.embedder, business_id, product_id=product_id, raw_text=raw_text, source=source
        )
    except Exception:  # noqa: BLE001 - keep the alias; its embedding is filled in later
        logger.exception("could not embed alias; saving it without an embedding")
        normalized = normalize_text(raw_text)
        if normalized:
            await services.repo.add_alias(
                business_id,
                product_id=product_id,
                alias_text=raw_text.strip(),
                normalized_text=normalized,
                embedding=None,
                source=source,
                confirmed=True,
            )


async def confirm_sku(
    services: AppServices, business_id: str, invoice_id: str, body: ConfirmSku
) -> InvoiceRecord:
    repo = services.repo
    invoice = await _get_invoice(services, business_id, invoice_id)
    line = next((item for item in invoice.lines if item.id == body.line_id), None)
    if line is None:
        raise WorkflowError(404, "not_found", "Invoice line not found.")

    if body.new_product is not None:
        product = await create_product_with_alias(services, business_id, body.new_product)
        created = True
    else:
        found = await repo.get_product(business_id, body.product_id or "")
        if found is None:
            raise WorkflowError(404, "not_found", "Product not found.")
        product, created = found, False

    try:
        await repo.update_line_match(
            business_id,
            invoice_id,
            line.id,
            LineMatchUpdate(
                product_id=product.id,
                suggested_product_id=None,
                match_status="linked",
                match_similarity=line.match_similarity,
                match_candidates=line.match_candidates,
                match_reason="Confirmed by the owner.",
            ),
        )
    except NotFoundError as exc:
        raise WorkflowError(404, "not_found", str(exc)) from exc
    if body.remember_alias:
        await _remember(services, business_id, product.id, line.item_name, source="owner_confirmed")
    await repo.log_event(
        business_id,
        invoice_id,
        "sku_confirmed",
        {
            "line_id": line.id,
            "item_name": line.item_name,
            "product_id": product.id,
            "product_created": created,
            "previous_status": line.match_status,
            "suggested_product_id": line.suggested_product_id,
        },
    )
    return await _get_invoice(services, business_id, invoice_id)


async def verify_invoice(services: AppServices, business_id: str, invoice_id: str) -> InvoiceRecord:
    repo = services.repo
    invoice = await _get_invoice(services, business_id, invoice_id)
    reasons = []
    if not invoice.supplier_id:
        reasons.append("Pick or create the supplier.")
    if not invoice.invoice_number:
        reasons.append("Enter the invoice number.")
    if invoice.invoice_date is None:
        reasons.append("Enter the invoice date.")
    if not invoice.lines:
        reasons.append("Add at least one item line.")
    if invoice.math_ok is not True:
        reasons.append("The math audit has not passed. Fix the highlighted values.")
    if invoice.field_issues:
        reasons.append("Check the invoice dates.")
    unlinked = [line for line in invoice.lines if line.match_status != "linked"]
    if unlinked:
        reasons.append(f"Confirm the product for {len(unlinked)} line(s).")
    if reasons:
        raise WorkflowError(409, "not_verifiable", "This invoice cannot be verified yet.", reasons=reasons)

    assert invoice.supplier_id is not None
    supplier = await repo.get_supplier(business_id, invoice.supplier_id)
    credit_days = supplier.credit_days if supplier and supplier.credit_days is not None else invoice.credit_days
    due_date = compute_due_date(invoice.invoice_date, payment_type=invoice.payment_type, credit_days=credit_days)
    updated = await repo.update_invoice(
        business_id,
        invoice_id,
        {"status": "verified", "verified_at": datetime.now(timezone.utc), "due_date": due_date},
    )
    await repo.log_event(
        business_id,
        invoice_id,
        "invoice_verified",
        {"due_date": due_date.isoformat() if due_date else None, "credit_days": credit_days},
    )
    return updated
