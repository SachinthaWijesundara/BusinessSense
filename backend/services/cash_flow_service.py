"""Payables: what the shop owes its suppliers, what it has paid, and when (proposal: "deterministic
rules calculate due dates, outstanding balances" and the Supplier and Payment Services).

Plain Python over saved invoices and supplier payments, no LLM and no model:
  * still owed         = an unpaid verified invoice's total minus the confirmed payments against it
  * payments due       = those falling due within 30 days, or already overdue
  * supplier totals    = the figures at the top of a Supplier Profile
  * supplier payments  = typed in by the owner or read from a receipt photo; each one stays
                         "unconfirmed" until the owner confirms it (proposal: "A payment is confirmed
                         by the user, or it stays labelled unconfirmed"). After two days the owner
                         is reminded; the payment still stays unconfirmed until they answer.
The Model 2 spend forecast and the cash-reserve projection are not built yet.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, Field

from database.client import (
    InvoiceSummary,
    NewPayment,
    NotFoundError,
    PaymentMethod,
    PaymentRecord,
    UUIDStr,
)
from services.invoice_service import WorkflowError, refresh_invoice_payment_status

if TYPE_CHECKING:
    from main import AppServices

PAYMENTS_DUE_WINDOW_DAYS = 30


# =============================================================================================
# Deterministic rules
# =============================================================================================


class SupplierTotals(BaseModel):
    """The figures at the top of a Supplier Profile."""

    invoice_count: int
    draft_count: int
    verified_total: float
    outstanding: float
    next_due_date: date | None
    paid_total: float = 0.0  # confirmed payments to this supplier
    unconfirmed_total: float = 0.0  # payments still waiting for the owner's confirmation
    unconfirmed_count: int = 0


class PaymentDue(BaseModel):
    """A verified, unpaid invoice falling due soon (overdue when days_left is below 0)."""

    invoice_id: str
    supplier_id: str | None
    supplier_name: str | None
    invoice_number: str | None
    due_date: date
    total: float | None
    amount_paid: float = 0.0
    still_owed: float | None = None
    days_left: int


def money_sum(values: list[float | None]) -> float:
    """Add rupee amounts exactly and round to cents (missing totals count as 0)."""
    total = sum((Decimal(str(value)) for value in values if value is not None), Decimal("0"))
    return float(total.quantize(Decimal("0.01")))


def still_owed(invoice: InvoiceSummary) -> float | None:
    """What is left to pay on an invoice: its total minus the confirmed payments (never below 0)."""
    if invoice.total is None:
        return None
    left = Decimal(str(invoice.total)) - Decimal(str(invoice.amount_paid or 0))
    return float(max(left, Decimal("0")).quantize(Decimal("0.01")))


def unpaid_verified(invoices: list[InvoiceSummary]) -> list[InvoiceSummary]:
    """Verified invoices that are not paid yet: what is still owed."""
    return [inv for inv in invoices if inv.status == "verified" and inv.payment_status != "paid"]


def supplier_totals(invoices: list[InvoiceSummary], payments: list[PaymentRecord] | None = None) -> SupplierTotals:
    """Totals for one supplier's invoices and payments (rejected ones are left out)."""
    kept = [inv for inv in invoices if inv.status != "rejected"]
    verified = [inv for inv in kept if inv.status == "verified"]
    owed = unpaid_verified(verified)
    due_dates = [inv.due_date for inv in owed if inv.due_date is not None]
    payments = payments or []
    waiting = [payment for payment in payments if payment.status == "unconfirmed"]
    return SupplierTotals(
        invoice_count=len(kept),
        draft_count=sum(1 for inv in kept if inv.status == "draft"),
        verified_total=money_sum([inv.total for inv in verified]),
        outstanding=money_sum([still_owed(inv) for inv in owed]),
        next_due_date=min(due_dates) if due_dates else None,
        paid_total=money_sum([payment.amount for payment in payments if payment.status == "confirmed"]),
        unconfirmed_total=money_sum([payment.amount for payment in waiting]),
        unconfirmed_count=len(waiting),
    )


def payments_due(invoices: list[InvoiceSummary], *, today: date, window_days: int = PAYMENTS_DUE_WINDOW_DAYS) -> list[PaymentDue]:
    """Unpaid verified invoices due within ``window_days`` or overdue, soonest first."""
    horizon = today + timedelta(days=window_days)
    due = [
        PaymentDue(
            invoice_id=inv.id, supplier_id=inv.supplier_id, supplier_name=inv.supplier_name,
            invoice_number=inv.invoice_number, due_date=inv.due_date, total=inv.total,
            amount_paid=inv.amount_paid, still_owed=still_owed(inv), days_left=(inv.due_date - today).days,
        )
        for inv in unpaid_verified(invoices)
        if inv.due_date is not None and inv.due_date <= horizon
    ]
    return sorted(due, key=lambda row: row.due_date)


class PayableDayInvoice(BaseModel):
    """One invoice contributing to a day's expected cash out."""

    invoice_id: str
    invoice_number: str | None = None
    supplier_id: str | None = None
    supplier_name: str | None = None
    still_owed: float
    due_date: date
    days_left: int


class PayableDay(BaseModel):
    """Cash expected to leave on one calendar day (from invoice due dates / credit)."""

    day: date
    total_owed: float
    invoices: list[PayableDayInvoice] = Field(default_factory=list)


def payable_cash_series(
    invoices: list[InvoiceSummary], *, today: date, window_days: int = PAYMENTS_DUE_WINDOW_DAYS
) -> list[PayableDay]:
    """Daily cash-out series for ``window_days`` from ``today``.

    Unpaid verified invoices due within the window are bucketed by due date. Overdue amounts
    (due before today) are rolled onto today so the forward-looking chart still shows them.
    Every day in the window is present, even when the total is 0.
    """
    window_days = max(1, int(window_days))
    days = [today + timedelta(days=offset) for offset in range(window_days)]
    buckets: dict[date, list[PayableDayInvoice]] = {day: [] for day in days}
    horizon = days[-1]
    for inv in unpaid_verified(invoices):
        if inv.due_date is None or inv.due_date > horizon:
            continue
        owed = still_owed(inv)
        if owed is None or owed <= 0:
            continue
        bucket_day = inv.due_date if inv.due_date >= today else today
        buckets[bucket_day].append(
            PayableDayInvoice(
                invoice_id=inv.id,
                invoice_number=inv.invoice_number,
                supplier_id=inv.supplier_id,
                supplier_name=inv.supplier_name,
                still_owed=owed,
                due_date=inv.due_date,
                days_left=(inv.due_date - today).days,
            )
        )
    series: list[PayableDay] = []
    for day in days:
        rows = sorted(buckets[day], key=lambda row: (row.due_date, row.supplier_name or "", row.invoice_id))
        series.append(
            PayableDay(
                day=day,
                total_owed=money_sum([row.still_owed for row in rows]),
                invoices=rows,
            )
        )
    return series


class OverdueInvoice(BaseModel):
    """One unpaid verified invoice whose due date is already past."""

    invoice_id: str
    invoice_number: str | None = None
    supplier_id: str | None = None
    supplier_name: str | None = None
    still_owed: float
    due_date: date
    days_overdue: int


class OverdueBalances(BaseModel):
    """Total overdue still owed and the invoices that make it up."""

    total_owed: float
    invoice_count: int
    invoices: list[OverdueInvoice] = Field(default_factory=list)


def overdue_balances(invoices: list[InvoiceSummary], *, today: date) -> OverdueBalances:
    """Unpaid verified invoices with due_date before today, most overdue first."""
    rows: list[OverdueInvoice] = []
    for inv in unpaid_verified(invoices):
        if inv.due_date is None or inv.due_date >= today:
            continue
        owed = still_owed(inv)
        if owed is None or owed <= 0:
            continue
        rows.append(
            OverdueInvoice(
                invoice_id=inv.id,
                invoice_number=inv.invoice_number,
                supplier_id=inv.supplier_id,
                supplier_name=inv.supplier_name,
                still_owed=owed,
                due_date=inv.due_date,
                days_overdue=(today - inv.due_date).days,
            )
        )
    rows.sort(key=lambda row: (-row.days_overdue, row.supplier_name or "", row.invoice_id))
    return OverdueBalances(
        total_owed=money_sum([row.still_owed for row in rows]),
        invoice_count=len(rows),
        invoices=rows,
    )


def _rupees(value: Decimal) -> str:
    """Format rupees for messages: 5850 -> "Rs 5,850.00"."""
    return f"Rs {value.quantize(Decimal('0.01')):,}"


def confirm_blockers(payment: PaymentRecord, invoice: InvoiceSummary | None, paid_by_others: float, *,
                     tolerance: float, today: date) -> list[str]:
    """Why a payment cannot be confirmed yet (empty when it can).

    A confirmed payment must name its supplier, amount and date. When it is for an invoice, that
    invoice must be verified and the payment may not be more than is still owed on it; a receipt
    line whose invoice number is not saved must be linked (or the number cleared) first.
    """
    reasons = []
    if payment.status == "rejected":
        reasons.append("This payment was rejected. Correct it first to bring it back.")
    if not payment.supplier_id:
        reasons.append("Pick the supplier.")
    if payment.amount is None:
        reasons.append("Enter the amount paid.")
    if payment.paid_on is None:
        reasons.append("Enter the payment date.")
    elif payment.paid_on > today + timedelta(days=1):
        reasons.append(f"The payment date {payment.paid_on} is in the future.")
    if invoice is None and payment.invoice_number_raw:
        reasons.append(
            f"The receipt names invoice {payment.invoice_number_raw}, which is not saved for this supplier. "
            "Save that invoice first or pick the invoice this payment is for."
        )
    if invoice is not None:
        label = invoice.invoice_number or "this invoice"
        if invoice.status != "verified":
            reasons.append(f"Verify invoice {label} first.")
        elif payment.amount is not None and invoice.total is not None:
            left = Decimal(str(invoice.total)) - Decimal(str(paid_by_others))
            if Decimal(str(payment.amount)) > left + Decimal(str(tolerance)):
                reasons.append(
                    f"{_rupees(Decimal(str(payment.amount)))} is more than the {_rupees(max(left, Decimal('0')))} "
                    f"still owed on invoice {label}."
                )
    return reasons


# =============================================================================================
# Request and reply bodies
# =============================================================================================

# A payment amount: more than zero, finite and small enough for numeric(12,2).
PaymentAmount = Annotated[float, Field(gt=0, lt=1e10, allow_inf_nan=False)]


class PaymentFields(BaseModel):
    """Payment fields the owner types or corrects."""

    supplier_id: UUIDStr | None = None
    invoice_id: UUIDStr | None = None
    invoice_number_raw: str | None = None
    amount: PaymentAmount | None = None
    paid_on: date | None = None
    method: PaymentMethod | None = None
    receipt_number: str | None = None
    cheque_number: str | None = None
    bank_name: str | None = None
    cheque_date: date | None = None


class PaymentCreate(PaymentFields):
    """A payment typed in by hand (POST /payments). ``confirm`` saves and confirms it in one go."""

    supplier_id: UUIDStr
    amount: PaymentAmount
    paid_on: date
    confirm: bool = False


class PaymentFix(PaymentFields):
    """Owner corrections (POST /payments/{id}/fix). Only the fields sent change; the payment goes
    back to unconfirmed so the owner confirms the corrected values."""


class PaymentReject(BaseModel):
    """Why the owner rejects a payment that was read or typed wrongly."""

    reason: str = Field(min_length=1)


class ReceiptUpload(BaseModel):
    """What POST /invoices/upload returns when the photo is a payment receipt: the unconfirmed
    payments saved from it (one per invoice it settles)."""

    document_type: Literal["receipt"] = "receipt"
    receipt_number: str | None = None
    supplier_id: str | None = None
    supplier_name_raw: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    image_url: str | None = None
    payments: list[PaymentRecord]


# =============================================================================================
# Owner workflow
# =============================================================================================

_TEXT_FIELDS = ("invoice_number_raw", "receipt_number", "cheque_number", "bank_name")


def _clean_text(fields: dict[str, Any]) -> dict[str, Any]:
    """Trim the text fields that were sent; empty text becomes None."""
    for name in _TEXT_FIELDS:
        if name in fields and fields[name] is not None:
            fields[name] = str(fields[name]).strip() or None
    return fields


async def get_payment_or_404(services: AppServices, business_id: str, payment_id: str) -> PaymentRecord:
    """Load a payment, or refuse with 404."""
    payment = await services.db.get_payment(business_id, payment_id)
    if payment is None:
        raise WorkflowError(404, "not_found", "Payment not found.")
    return payment


async def with_receipt_links(services: AppServices, payments: list[PaymentRecord]) -> list[PaymentRecord]:
    """Add a short-lived link to each payment's receipt photo, when one is stored."""
    if services.storage is None:
        return payments
    links: dict[str, str | None] = {}
    for payment in payments:
        if payment.image_path:
            if payment.image_path not in links:
                try:
                    links[payment.image_path] = await services.storage.signed_url(payment.image_path)
                except Exception:  # noqa: BLE001 - the payment is still useful without its photo link
                    links[payment.image_path] = None
            payment.image_url = links[payment.image_path]
    return payments


async def check_link(services: AppServices, business_id: str, supplier_id: str | None,
                     invoice_id: str | None) -> tuple[str | None, InvoiceSummary | None]:
    """Check the supplier and invoice a payment is for; the invoice's supplier fills an empty one.

    Refuses an unknown supplier or invoice (404), a rejected invoice and an invoice of another
    supplier (409).
    """
    invoice = None
    if invoice_id:
        invoice = await services.db.get_invoice(business_id, invoice_id)
        if invoice is None:
            raise WorkflowError(404, "not_found", "Invoice not found.")
        if invoice.status == "rejected":
            raise WorkflowError(409, "invoice_rejected", "That invoice was rejected; a payment cannot be linked to it.")
        if supplier_id and invoice.supplier_id and invoice.supplier_id != supplier_id:
            raise WorkflowError(409, "supplier_mismatch", "That invoice belongs to another supplier.")
        supplier_id = supplier_id or invoice.supplier_id
    if supplier_id and await services.db.get_supplier(business_id, supplier_id) is None:
        raise WorkflowError(404, "not_found", "Supplier not found.")
    return supplier_id, invoice


async def _paid_by_others(services: AppServices, business_id: str, invoice_id: str, payment_id: str | None) -> float:
    """Confirmed payments already made against an invoice, leaving out one payment."""
    payments = await services.db.list_payments(business_id, invoice_id=invoice_id, status="confirmed")
    return money_sum([payment.amount for payment in payments if payment.id != payment_id])


async def _blockers(services: AppServices, business_id: str, payment: PaymentRecord) -> list[str]:
    """Why ``payment`` cannot be confirmed yet, from its invoice and the other payments against it."""
    invoice = await services.db.get_invoice(business_id, payment.invoice_id) if payment.invoice_id else None
    paid = await _paid_by_others(services, business_id, invoice.id, payment.id) if invoice else 0.0
    return confirm_blockers(payment, invoice, paid, tolerance=services.settings.math_tolerance, today=date.today())


async def payments_saved(services: AppServices, business_id: str, payments: list[PaymentRecord], event: str,
                         extra: dict[str, Any] | None = None) -> None:
    """Log newly saved payments and update the payment status of the invoices they are for."""
    for payment in payments:
        await services.db.log_event(business_id, payment.invoice_id, event,
                                    {"payment_id": payment.id, "amount": payment.amount,
                                     "receipt_number": payment.receipt_number, **(extra or {})})
    for invoice_id in dict.fromkeys(payment.invoice_id for payment in payments if payment.invoice_id):
        await refresh_invoice_payment_status(services, business_id, invoice_id)


async def find_duplicate_receipt(services: AppServices, business_id: str, supplier_id: str | None,
                                 receipt_number: str | None, invoice_id: str | None = None,
                                 *, any_invoice: bool = False) -> PaymentRecord | None:
    """A payment (not rejected) already saved from the same receipt of the same supplier: for the
    same invoice, or for any invoice when a whole receipt is uploaded again."""
    if not supplier_id or not receipt_number:
        return None
    saved = await services.db.list_payments(business_id, supplier_id=supplier_id, receipt_number=receipt_number)
    for payment in saved:
        if payment.status != "rejected" and (any_invoice or payment.invoice_id == invoice_id):
            return payment
    return None


async def record_payment(services: AppServices, business_id: str, body: PaymentCreate) -> PaymentRecord:
    """Save a payment typed in by hand (unconfirmed), or save and confirm it when ``confirm`` is set.

    With ``confirm`` the same checks as POST /payments/{id}/confirm run first; if any fails, nothing
    is saved and the reasons come back (409).
    """
    fields = _clean_text(body.model_dump(exclude={"confirm"}))
    supplier_id, invoice = await check_link(services, business_id, fields["supplier_id"], fields["invoice_id"])
    fields["supplier_id"] = supplier_id
    if invoice is not None:
        fields["invoice_number_raw"] = None
    clash = await find_duplicate_receipt(services, business_id, supplier_id, fields["receipt_number"], fields["invoice_id"])
    if clash:
        raise WorkflowError(409, "duplicate_payment", "This receipt is already recorded for that invoice.",
                            payment_id=clash.id)
    new_payment = NewPayment(**fields, status="unconfirmed", source="manual")
    if body.confirm:
        draft = PaymentRecord(**new_payment.model_dump(), id="", business_id=business_id)
        reasons = await _blockers(services, business_id, draft)
        if reasons:
            raise WorkflowError(409, "not_confirmable", "This payment cannot be confirmed yet.", reasons=reasons)
        new_payment.status = "confirmed"
        new_payment.confirmed_at = datetime.now(timezone.utc)
    try:
        [payment] = await services.db.create_payments(business_id, [new_payment])
    except NotFoundError as exc:
        raise WorkflowError(404, "not_found", str(exc)) from exc
    await payments_saved(services, business_id, [payment], "payment_recorded", {"confirmed": body.confirm})
    return (await with_receipt_links(services, [payment]))[0]


async def fix_payment(services: AppServices, business_id: str, payment_id: str, body: PaymentFix) -> PaymentRecord:
    """Apply owner corrections. The payment goes back to unconfirmed, so the corrected values are
    confirmed again; the invoices it was and is for get their payment status updated."""
    current = await get_payment_or_404(services, business_id, payment_id)
    sent = _clean_text(body.model_dump(exclude_unset=True))
    supplier_id = sent["supplier_id"] if "supplier_id" in sent else current.supplier_id
    invoice_id = sent["invoice_id"] if "invoice_id" in sent else current.invoice_id
    supplier_id, invoice = await check_link(services, business_id, supplier_id, invoice_id)
    if "supplier_id" in sent or supplier_id != current.supplier_id:
        sent["supplier_id"] = supplier_id  # the invoice's supplier fills an empty one
    if invoice is not None and "invoice_id" in sent and "invoice_number_raw" not in sent:
        sent["invoice_number_raw"] = None  # linked to a saved invoice now
    clash = await find_duplicate_receipt(services, business_id, supplier_id,
                                         sent.get("receipt_number", current.receipt_number), invoice_id)
    if clash and clash.id != payment_id:
        raise WorkflowError(409, "duplicate_payment", "This receipt is already recorded for that invoice.",
                            payment_id=clash.id)
    changes = {**sent, "status": "unconfirmed", "confirmed_at": None}
    try:
        updated = await services.db.update_payment(business_id, payment_id, changes)
    except NotFoundError as exc:
        raise WorkflowError(404, "not_found", str(exc)) from exc
    await services.db.log_event(business_id, updated.invoice_id, "payment_corrected",
                                {"payment_id": payment_id, "fields": sorted(sent), "previous_status": current.status})
    for affected in dict.fromkeys(i for i in (current.invoice_id, updated.invoice_id) if i):
        await refresh_invoice_payment_status(services, business_id, affected)
    return (await with_receipt_links(services, [updated]))[0]


async def confirm_payment(services: AppServices, business_id: str, payment_id: str) -> PaymentRecord:
    """The owner confirms a payment. Refused (409, reasons) until it is complete and adds up; the
    invoice it is for becomes paid once confirmed payments cover its total."""
    payment = await get_payment_or_404(services, business_id, payment_id)
    if payment.status == "confirmed":
        raise WorkflowError(409, "already_confirmed", "This payment is already confirmed.")
    reasons = await _blockers(services, business_id, payment)
    if reasons:
        raise WorkflowError(409, "not_confirmable", "This payment cannot be confirmed yet.", reasons=reasons)
    updated = await services.db.update_payment(business_id, payment_id,
                                               {"status": "confirmed", "confirmed_at": datetime.now(timezone.utc)})
    await services.db.log_event(business_id, updated.invoice_id, "payment_confirmed",
                                {"payment_id": payment_id, "amount": updated.amount})
    await refresh_invoice_payment_status(services, business_id, updated.invoice_id)
    return (await with_receipt_links(services, [updated]))[0]


async def reject_payment(services: AppServices, business_id: str, payment_id: str, body: PaymentReject) -> PaymentRecord:
    """Keep a wrongly read or typed payment out of the ledger (kept and logged, never deleted)."""
    payment = await get_payment_or_404(services, business_id, payment_id)
    if payment.status == "confirmed":
        raise WorkflowError(409, "already_confirmed", "A confirmed payment cannot be rejected. Correct it first.")
    updated = await services.db.update_payment(business_id, payment_id, {"status": "rejected"})
    await services.db.log_event(business_id, updated.invoice_id, "payment_rejected",
                                {"payment_id": payment_id, "reason": body.reason, "previous_status": payment.status})
    await refresh_invoice_payment_status(services, business_id, updated.invoice_id)
    return (await with_receipt_links(services, [updated]))[0]


def reminders(payments: list[PaymentRecord]) -> list[PaymentRecord]:
    """Unconfirmed payments that have waited more than two days for the owner (oldest first)."""
    due = [payment for payment in payments if payment.status == "unconfirmed" and payment.reminder_due]
    return sorted(due, key=lambda payment: payment.created_at or datetime.min.replace(tzinfo=timezone.utc))

