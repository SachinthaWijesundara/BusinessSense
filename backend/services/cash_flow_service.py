"""Payables: what the shop owes its suppliers and when (proposal: "deterministic rules calculate
due dates, outstanding balances" and the Purchasing Cash Planner's dated supplier obligations).

Plain Python over saved invoices, no LLM and no model:
  * outstanding balance  = verified invoices not marked paid
  * payments due         = those falling due within 30 days, or already overdue
  * supplier totals      = the figures at the top of a Supplier Profile
The Model 2 spend forecast and the cash-reserve projection are not built yet.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from pydantic import BaseModel

from database.client import InvoiceSummary

PAYMENTS_DUE_WINDOW_DAYS = 30


class SupplierTotals(BaseModel):
    """The figures at the top of a Supplier Profile."""

    invoice_count: int
    draft_count: int
    verified_total: float
    outstanding: float
    next_due_date: date | None


class PaymentDue(BaseModel):
    """A verified, unpaid invoice falling due soon (overdue when days_left is below 0)."""

    invoice_id: str
    supplier_id: str | None
    supplier_name: str | None
    invoice_number: str | None
    due_date: date
    total: float | None
    days_left: int


def money_sum(values: list[float | None]) -> float:
    """Add rupee amounts exactly and round to cents (missing totals count as 0)."""
    total = sum((Decimal(str(value)) for value in values if value is not None), Decimal("0"))
    return float(total.quantize(Decimal("0.01")))


def unpaid_verified(invoices: list[InvoiceSummary]) -> list[InvoiceSummary]:
    """Verified invoices the owner has not marked paid: what is still owed."""
    return [inv for inv in invoices if inv.status == "verified" and inv.payment_status != "paid"]


def supplier_totals(invoices: list[InvoiceSummary]) -> SupplierTotals:
    """Totals for one supplier's invoices (rejected invoices are left out)."""
    kept = [inv for inv in invoices if inv.status != "rejected"]
    verified = [inv for inv in kept if inv.status == "verified"]
    owed = unpaid_verified(verified)
    due_dates = [inv.due_date for inv in owed if inv.due_date is not None]
    return SupplierTotals(
        invoice_count=len(kept),
        draft_count=sum(1 for inv in kept if inv.status == "draft"),
        verified_total=money_sum([inv.total for inv in verified]),
        outstanding=money_sum([inv.total for inv in owed]),
        next_due_date=min(due_dates) if due_dates else None,
    )


def payments_due(invoices: list[InvoiceSummary], *, today: date, window_days: int = PAYMENTS_DUE_WINDOW_DAYS) -> list[PaymentDue]:
    """Unpaid verified invoices due within ``window_days`` or overdue, soonest first."""
    horizon = today + timedelta(days=window_days)
    due = [
        PaymentDue(
            invoice_id=inv.id, supplier_id=inv.supplier_id, supplier_name=inv.supplier_name,
            invoice_number=inv.invoice_number, due_date=inv.due_date, total=inv.total,
            days_left=(inv.due_date - today).days,
        )
        for inv in unpaid_verified(invoices)
        if inv.due_date is not None and inv.due_date <= horizon
    ]
    return sorted(due, key=lambda row: row.due_date)
