"""Invoice business rules and the owner's invoice workflow.

Deterministic rules (plain Python, no LLM; the proposal's "Python business rules"):
  * ``math_audit``: qty x rate - discount = line total, and lines - special discount + tax = total,
    within Rs 1. Money is computed with Decimal so float noise can never flip a result.
  * date checks and due dates (invoice date + the supplier's credit days).

Owner workflow (proposal Figure 6: PendingFix, HumanConfirm and verification):
  * type in an invoice, fix an invoice, confirm a product, verify, reject, mark paid / unpaid.

Only a verified invoice whose lines are all linked to products feeds supplier and item history
and the predictive models. Every correction, confirmation and verification goes to the audit log.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentic.schemas import InvoiceDraft, InvoiceLineDraft, usable_lines
from agentic.sku_match_agent import LineToMatch
from database.client import (
    CreditDays,
    DuplicateError,
    Invoice,
    InvoiceLineRecord,
    LineMatchUpdate,
    NewInvoice,
    NewInvoiceLine,
    NewProduct,
    NotFoundError,
    ProductRecord,
    UUIDStr,
)

from .alias_service import normalize_text, remember_alias

if TYPE_CHECKING:
    from main import AppServices

logger = logging.getLogger(__name__)

DEFAULT_TOLERANCE = 1.0  # rupees (proposal: MathAuditNode, +/- Rs 1)
EARLIEST_PLAUSIBLE_DATE = date(2015, 1, 1)  # the shop's paper archive starts in 2019


# =============================================================================================
# Errors
# =============================================================================================


class WorkflowError(Exception):
    """A request a business rule refuses, with the HTTP status and JSON body to send back."""

    def __init__(self, status_code: int, error_type: str, message: str, **extra: Any) -> None:
        """Keep the status, a machine-readable type, a readable message and any extra fields."""
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.message = message
        self.extra = extra

    def body(self) -> dict[str, Any]:
        """The JSON error body: {"error_type", "message", ...extra}."""
        return {"error_type": self.error_type, "message": self.message, **self.extra}


# =============================================================================================
# Deterministic rules
# =============================================================================================


@dataclass(frozen=True)
class AuditIssue:
    """One problem found by the math audit or the date checks."""

    code: str
    message: str
    line_index: int | None = None
    field: str | None = None
    expected: float | None = None
    actual: float | None = None

    def to_dict(self) -> dict:
        """The issue as JSON, leaving out empty keys."""
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class MathAuditResult:
    """The audit outcome: ok, the issues found, and the total the lines add up to."""

    ok: bool
    issues: list[AuditIssue]
    computed_total: float | None

    def issues_as_dicts(self) -> list[dict]:
        """All issues as JSON."""
        return [issue.to_dict() for issue in self.issues]


def _dec(value: float | int | str | None) -> Decimal | None:
    """A number as an exact Decimal (None stays None)."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _money(value: Decimal) -> float:
    """Round to cents for JSON."""
    return float(value.quantize(Decimal("0.01")))


def _fmt(value: Decimal) -> str:
    """Format rupees for messages: 5850 -> "5,850.00"."""
    return f"{value.quantize(Decimal('0.01')):,}"


def math_audit(invoice: InvoiceDraft, tolerance: float = DEFAULT_TOLERANCE) -> MathAuditResult:
    """Check every line (qty x rate - discount = line total) and the invoice total.

    Invoice rule: sum(line totals) - special discount + tax = total, within ``tolerance``.
    ``line_index`` counts usable lines only (blank rows removed), the order lines are stored in.
    """
    tol = Decimal(str(tolerance))
    issues: list[AuditIssue] = []
    lines = usable_lines(invoice)
    if not lines:
        issues.append(AuditIssue("no_lines", "The invoice has no item lines."))

    line_sum = Decimal("0")
    line_sum_complete = bool(lines)
    for index, line in enumerate(lines):
        label = f"Line {index + 1}"
        quantity, rate, total = _dec(line.quantity), _dec(line.unit_rate), _dec(line.line_total)
        discount = _dec(line.discount) or Decimal("0")
        missing = [name for name, value in (("quantity", quantity), ("unit rate", rate), ("line total", total)) if value is None]
        if missing:
            issues.append(AuditIssue("missing_value", f"{label}: missing {', '.join(missing)}.", line_index=index))
            if total is None:
                line_sum_complete = False
            else:
                line_sum += total
            continue
        assert quantity is not None and rate is not None and total is not None
        if quantity < 0 or rate < 0 or discount < 0 or total < 0:
            issues.append(AuditIssue("negative_value", f"{label}: a value is negative.", line_index=index))
        expected = quantity * rate - discount
        if abs(expected - total) > tol:
            issues.append(
                AuditIssue(
                    "line_total_mismatch",
                    f"{label}: {quantity.normalize():f} x {_fmt(rate)} - {_fmt(discount)} = {_fmt(expected)}, "
                    f"but the line total says {_fmt(total)}.",
                    line_index=index,
                    expected=_money(expected),
                    actual=_money(total),
                )
            )
        line_sum += total

    special_discount = _dec(invoice.special_discount) or Decimal("0")
    tax = _dec(invoice.tax) or Decimal("0")
    computed_total = line_sum - special_discount + tax if line_sum_complete else None
    invoice_total = _dec(invoice.total)
    if invoice_total is None:
        issues.append(AuditIssue("missing_total", "The invoice total is missing.", field="total"))
    elif computed_total is not None and abs(computed_total - invoice_total) > tol:
        issues.append(
            AuditIssue(
                "invoice_total_mismatch",
                f"Line totals - special discount + tax = {_fmt(computed_total)}, "
                f"but the invoice total says {_fmt(invoice_total)}.",
                field="total",
                expected=_money(computed_total),
                actual=_money(invoice_total),
            )
        )
    return MathAuditResult(
        ok=not issues,
        issues=issues,
        computed_total=None if computed_total is None else _money(computed_total),
    )


def parse_iso_date(value: str | date | None) -> date | None:
    """Read a YYYY-MM-DD date; anything else gives None."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def date_issues(invoice: InvoiceDraft, *, today: date | None = None) -> list[AuditIssue]:
    """Flag dates that cannot be right (not a date, in the future, older than the archive)."""
    today = today or date.today()
    issues: list[AuditIssue] = []
    for field_name in ("invoice_date", "delivery_date"):
        raw = getattr(invoice, field_name)
        if raw is None or not str(raw).strip():
            continue
        parsed = parse_iso_date(raw)
        label = field_name.replace("_", " ")
        if parsed is None:
            issues.append(AuditIssue("invalid_date", f"The {label} '{raw}' is not a date.", field=field_name))
        elif parsed > today + timedelta(days=1):
            issues.append(AuditIssue("future_date", f"The {label} {parsed} is in the future.", field=field_name))
        elif parsed < EARLIEST_PLAUSIBLE_DATE:
            issues.append(AuditIssue("implausible_date", f"The {label} {parsed} is older than the shop's archive.", field=field_name))
    return issues


def compute_due_date(invoice_date: date | None, *, payment_type: str | None = None, credit_days: int | None = None) -> date | None:
    """Due date = invoice date + credit days; cash invoices fall due on the invoice date."""
    if invoice_date is None:
        return None
    if payment_type == "cash":
        return invoice_date
    if credit_days is None:
        return None
    return invoice_date + timedelta(days=credit_days)


def invoice_missing_fields(invoice: NewInvoice, *, has_lines: bool) -> list[str]:
    """Required fields still empty on a saved invoice (highlighted on the review screen)."""
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


# =============================================================================================
# Request bodies
# =============================================================================================


# Money and quantities as typed by the owner: finite and small enough for the database columns
# (numeric(12,2) and numeric(12,3)). Negative values pass; the math audit flags them.
Money = Annotated[float, Field(gt=-1e10, lt=1e10, allow_inf_nan=False)]
Quantity = Annotated[float, Field(gt=-1e9, lt=1e9, allow_inf_nan=False)]


class LineInput(BaseModel):
    """One line typed or corrected by the owner (values as printed)."""

    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True, serialize_by_alias=True)

    id: UUIDStr | None = Field(None, description="Existing line id, to keep its product link.")
    sku: str | None = None
    item_name: str = Field(min_length=1)
    quantity: Quantity | None = None
    unit_rate: Money | None = Field(None, alias="mro", description="Unit rate in rupees.")
    discount: Money | None = 0.0
    line_total: Money | None = None


class InvoiceHeader(BaseModel):
    """Header fields shared by typed-in invoices and corrections."""

    supplier_id: UUIDStr | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    delivery_date: date | None = None
    payment_type: Literal["cash", "credit"] | None = None
    credit_days: CreditDays | None = None
    special_discount: Money | None = None
    tax: Money | None = None
    total: Money | None = None


class InvoiceCreate(InvoiceHeader):
    """An invoice typed in by hand (POST /invoices)."""

    supplier_id: UUIDStr
    lines: list[LineInput] = Field(min_length=1)


class InvoiceFix(InvoiceHeader):
    """Owner corrections (POST /invoices/{id}/fix). Only sent fields change; ``lines`` replaces all lines."""

    lines: list[LineInput] | None = None


class ConfirmSku(BaseModel):
    """'Is this the same as [product]?' answered: link to a product, or to a new one."""

    line_id: UUIDStr
    product_id: UUIDStr | None = None
    new_product: NewProduct | None = None
    remember_alias: bool = True

    @model_validator(mode="after")
    def _one_target(self) -> ConfirmSku:
        """Exactly one of product_id or new_product must be sent."""
        if (self.product_id is None) == (self.new_product is None):
            raise ValueError("send exactly one of product_id or new_product")
        return self


class RejectRequest(BaseModel):
    """Why the owner rejects a draft invoice."""

    reason: str = Field(min_length=1)


class PaymentRequest(BaseModel):
    """The owner marks a verified invoice paid or not paid."""

    payment_status: Literal["paid", "unpaid"]


# =============================================================================================
# Owner workflow
# =============================================================================================


def _new_lines(inputs: list[LineInput], previous: dict[str, InvoiceLineRecord] | None = None) -> list[NewInvoiceLine]:
    """Turn owner-entered lines into lines to save; an unchanged existing line keeps its product link."""
    previous = previous or {}
    lines = []
    for index, raw in enumerate(inputs):
        kept = previous.get(raw.id or "")
        keep_match = kept is not None and normalize_text(kept.item_name) == normalize_text(raw.item_name)
        match = (
            {
                "product_id": kept.product_id,
                "suggested_product_id": kept.suggested_product_id,
                "match_status": kept.match_status,
                "match_similarity": kept.match_similarity,
                "match_candidates": kept.match_candidates,
                "match_reason": kept.match_reason,
            }
            if keep_match and kept is not None
            else {}
        )
        lines.append(
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
    return lines


def _as_draft(header: dict[str, Any], lines: list[NewInvoiceLine] | list[InvoiceLineRecord]) -> InvoiceDraft:
    """Shape saved values like an agent draft so the same audit runs on both."""
    return InvoiceDraft(
        invoice_number=header.get("invoice_number"),
        invoice_date=header["invoice_date"].isoformat() if header.get("invoice_date") else None,
        delivery_date=header["delivery_date"].isoformat() if header.get("delivery_date") else None,
        special_discount=header.get("special_discount"),
        tax=header.get("tax"),
        total=header.get("total"),
        lines=[
            InvoiceLineDraft(sku=l.sku, item_name=l.item_name, quantity=l.quantity, mro=l.unit_rate,
                             discount=l.discount, line_total=l.line_total)
            for l in lines
        ],
    )


async def get_invoice_or_404(services: AppServices, business_id: str, invoice_id: str) -> Invoice:
    """Load an invoice, or refuse with 404."""
    invoice = await services.db.get_invoice(business_id, invoice_id)
    if invoice is None:
        raise WorkflowError(404, "not_found", "Invoice not found.")
    return invoice


async def _check_duplicate(services: AppServices, business_id: str, supplier_id: str | None,
                           number: str | None, exclude_id: str | None = None) -> None:
    """Refuse (409) a second invoice with the same number from the same supplier."""
    if not supplier_id or not number:
        return
    clash = await services.db.find_invoice_by_number(business_id, supplier_id, number, exclude_id=exclude_id)
    if clash:
        raise WorkflowError(409, "duplicate_invoice", "That invoice number is already saved for this supplier.",
                            invoice_id=clash.id)


async def run_sku_matching(services: AppServices, business_id: str, invoice: Invoice) -> Invoice:
    """Match every line that is not linked yet with SkuMatchAgent and store the results."""
    db = services.db
    targets = [line for line in invoice.lines if line.match_status != "linked"]
    if not targets:
        return invoice
    to_match = [LineToMatch(line_index=i, raw_text=line.item_name, sku=line.sku) for i, line in enumerate(targets)]
    try:
        results = await services.sku_matcher.match(business_id, to_match, group_id=invoice.id)
    except Exception as exc:  # noqa: BLE001 - matching is retried on the next fix
        logger.exception("SKU matching failed")
        for line in targets:
            await db.update_line_match(
                business_id, invoice.id, line.id,
                LineMatchUpdate(match_status="pending",
                                match_reason="Automatic product matching is unavailable right now; pick the product."),
            )
        await db.log_event(business_id, invoice.id, "sku_match_failed", {"error": str(exc)[:300]})
        return await get_invoice_or_404(services, business_id, invoice.id)

    counts = {"linked": 0, "pending": 0, "new_product": 0}
    for line, result in zip(targets, results, strict=True):
        counts[result.status] += 1
        await db.update_line_match(
            business_id, invoice.id, line.id,
            LineMatchUpdate(
                product_id=result.product_id,
                suggested_product_id=result.suggested_product_id,
                match_status=result.status,
                match_similarity=None if result.similarity is None else round(result.similarity, 4),
                match_candidates=result.candidates_as_dicts(),
                match_reason=result.reason or None,
            ),
        )
    await db.log_event(business_id, invoice.id, "sku_matched", {**counts, "threshold": services.sku_matcher.threshold})
    return await get_invoice_or_404(services, business_id, invoice.id)


async def create_manual_invoice(services: AppServices, business_id: str, body: InvoiceCreate) -> Invoice:
    """Save an invoice typed in by hand: audit it, save the draft, then match products."""
    db = services.db
    if await db.get_supplier(business_id, body.supplier_id) is None:
        raise WorkflowError(404, "not_found", "Supplier not found.")
    header = body.model_dump(exclude={"lines"})
    header["invoice_number"] = (header.get("invoice_number") or "").strip() or None
    await _check_duplicate(services, business_id, body.supplier_id, header["invoice_number"])

    lines = _new_lines(body.lines)
    draft = _as_draft(header, lines)
    audit = math_audit(draft, services.settings.math_tolerance)
    new_invoice = NewInvoice(
        **{key: value for key, value in header.items() if value is not None},
        status="draft",
        source="manual",
        math_ok=audit.ok,
        math_issues=audit.issues_as_dicts(),
        field_issues=[issue.to_dict() for issue in date_issues(draft)],
    )
    new_invoice.missing_fields = invoice_missing_fields(new_invoice, has_lines=bool(lines))
    try:
        invoice = await db.create_invoice(business_id, new_invoice, lines)
    except DuplicateError as exc:
        raise WorkflowError(409, "duplicate_invoice", "That invoice number is already saved for this supplier.") from exc
    await db.log_event(business_id, invoice.id, "invoice_created", {"math_ok": audit.ok, "lines": len(lines)})
    if audit.ok:
        invoice = await run_sku_matching(services, business_id, invoice)
    return invoice


async def fix_invoice(services: AppServices, business_id: str, invoice_id: str, fix: InvoiceFix) -> Invoice:
    """Apply owner corrections, re-run the audit, and match products once the math passes."""
    db = services.db
    current = await get_invoice_or_404(services, business_id, invoice_id)
    sent = fix.model_dump(exclude_unset=True)
    line_inputs = sent.pop("lines", None)
    if sent.get("supplier_id") and await db.get_supplier(business_id, sent["supplier_id"]) is None:
        raise WorkflowError(404, "not_found", "Supplier not found.")
    if "invoice_number" in sent:
        sent["invoice_number"] = (sent["invoice_number"] or "").strip() or None
    for money_field in ("special_discount", "tax"):
        if money_field in sent and sent[money_field] is None:
            sent[money_field] = 0.0

    new_lines = None
    if line_inputs is not None:
        new_lines = _new_lines(fix.lines or [], {line.id: line for line in current.lines})
    merged = {**current.model_dump(), **sent}
    effective_lines = new_lines if new_lines is not None else current.lines
    draft = _as_draft(merged, effective_lines)
    audit = math_audit(draft, services.settings.math_tolerance)
    await _check_duplicate(services, business_id, merged.get("supplier_id"), merged.get("invoice_number"), invoice_id)

    changes: dict[str, Any] = {
        **sent,
        "math_ok": audit.ok,
        "math_issues": audit.issues_as_dicts(),
        "field_issues": [issue.to_dict() for issue in date_issues(draft)],
        "missing_fields": invoice_missing_fields(NewInvoice.model_validate(merged), has_lines=bool(effective_lines)),
        "status": "draft",
        "verified_at": None,
    }
    try:
        updated = await db.update_invoice(business_id, invoice_id, changes, new_lines)
    except DuplicateError as exc:
        raise WorkflowError(409, "duplicate_invoice", str(exc)) from exc
    await db.log_event(
        business_id, invoice_id, "invoice_corrected",
        {"fields": sorted(sent) + (["lines"] if line_inputs is not None else []),
         "previous_status": current.status, "math_ok": audit.ok},
    )
    if audit.ok:
        updated = await run_sku_matching(services, business_id, updated)
    return updated


async def _remember(services: AppServices, business_id: str, product_id: str, raw_text: str, *, source: str) -> None:
    """Keep an alias; if embedding fails it is saved without one and embedded on the next match."""
    try:
        await remember_alias(services.db, services.embedder, business_id, product_id=product_id, raw_text=raw_text, source=source)
    except Exception:  # noqa: BLE001
        logger.exception("could not embed alias; saving it without an embedding")
        normalized = normalize_text(raw_text)
        if normalized:
            await services.db.add_alias(
                business_id, product_id=product_id, alias_text=raw_text.strip(), normalized_text=normalized,
                embedding=None, source=source, confirmed=True,
            )


async def create_product_with_alias(services: AppServices, business_id: str, data: NewProduct) -> ProductRecord:
    """Add a product to the catalogue together with its own name as an alias."""
    try:
        product = await services.db.create_product(business_id, data)
    except DuplicateError as exc:
        raise WorkflowError(409, "duplicate_product", f"A product named '{data.name}' already exists.") from exc
    await _remember(services, business_id, product.id, product.name, source="catalogue")
    return product


async def confirm_sku(services: AppServices, business_id: str, invoice_id: str, body: ConfirmSku) -> Invoice:
    """Link a line to the product the owner picked (or a new one) and remember the printed text."""
    db = services.db
    invoice = await get_invoice_or_404(services, business_id, invoice_id)
    line = next((item for item in invoice.lines if item.id == body.line_id), None)
    if line is None:
        raise WorkflowError(404, "not_found", "Invoice line not found.")
    if invoice.status != "draft":
        raise WorkflowError(409, "not_draft", "Only a draft invoice can change its product links. Fix it first.")
    if body.new_product is not None:
        product = await create_product_with_alias(services, business_id, body.new_product)
        created = True
    else:
        found = await db.get_product(business_id, body.product_id or "")
        if found is None:
            raise WorkflowError(404, "not_found", "Product not found.")
        product, created = found, False
    try:
        await db.update_line_match(
            business_id, invoice_id, line.id,
            LineMatchUpdate(product_id=product.id, suggested_product_id=None, match_status="linked",
                            match_similarity=line.match_similarity, match_candidates=line.match_candidates,
                            match_reason="Confirmed by the owner."),
        )
    except NotFoundError as exc:
        raise WorkflowError(404, "not_found", str(exc)) from exc
    if body.remember_alias:
        await _remember(services, business_id, product.id, line.item_name, source="owner_confirmed")
        # The owner's latest answer wins: this printed text no longer points at any other product.
        await db.retire_other_aliases(business_id, normalize_text(line.item_name), product.id)
    await db.log_event(
        business_id, invoice_id, "sku_confirmed",
        {"line_id": line.id, "item_name": line.item_name, "product_id": product.id, "product_created": created,
         "previous_status": line.match_status, "suggested_product_id": line.suggested_product_id},
    )
    return await get_invoice_or_404(services, business_id, invoice_id)


async def verify_invoice(services: AppServices, business_id: str, invoice_id: str) -> Invoice:
    """Mark an invoice verified and set its due date. Refused (409, reasons) until everything is done."""
    db = services.db
    invoice = await get_invoice_or_404(services, business_id, invoice_id)
    if invoice.status == "verified":
        raise WorkflowError(409, "already_verified", "This invoice is already verified.")
    reasons = []
    if invoice.status == "rejected":
        reasons.append("This invoice was rejected. Fix it first to bring it back to draft.")
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
    supplier = await db.get_supplier(business_id, invoice.supplier_id)
    credit_days = supplier.credit_days if supplier and supplier.credit_days is not None else invoice.credit_days
    due_date = compute_due_date(invoice.invoice_date, payment_type=invoice.payment_type, credit_days=credit_days)
    updated = await db.update_invoice(
        business_id, invoice_id,
        {"status": "verified", "verified_at": datetime.now(timezone.utc), "due_date": due_date},
    )
    await db.log_event(business_id, invoice_id, "invoice_verified",
                       {"due_date": due_date.isoformat() if due_date else None, "credit_days": credit_days})
    return updated


async def reject_invoice(services: AppServices, business_id: str, invoice_id: str, body: RejectRequest) -> Invoice:
    """Exclude a draft invoice from the ledger (kept and logged, never deleted)."""
    invoice = await get_invoice_or_404(services, business_id, invoice_id)
    if invoice.status == "verified":
        raise WorkflowError(409, "already_verified", "A verified invoice cannot be rejected. Fix it first.")
    updated = await services.db.update_invoice(business_id, invoice_id, {"status": "rejected"})
    await services.db.log_event(business_id, invoice_id, "invoice_rejected",
                                {"reason": body.reason, "previous_status": invoice.status})
    return updated


async def set_payment_status(services: AppServices, business_id: str, invoice_id: str, body: PaymentRequest) -> Invoice:
    """Mark a verified invoice paid or not paid (the owner confirms payments)."""
    invoice = await get_invoice_or_404(services, business_id, invoice_id)
    if invoice.status != "verified":
        raise WorkflowError(409, "not_verified", "Only a verified invoice can be marked paid or unpaid.")
    updated = await services.db.update_invoice(business_id, invoice_id, {"payment_status": body.payment_status})
    await services.db.log_event(business_id, invoice_id, "payment_marked",
                                {"payment_status": body.payment_status, "previous": invoice.payment_status})
    return updated
