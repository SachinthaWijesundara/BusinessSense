"""What the shop bought: purchase totals over time and per supplier (proposal: "monthly purchases",
"supplier spending" and the Supplier Profile's item lines).

Plain Python over verified invoices only, no LLM and no model. Item price history with the Model 1
expected range belongs to Purchase Analytics and is not built yet.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from pydantic import BaseModel

from database.client import InvoiceSummary, SupplierLine

from .cash_flow_service import money_sum

if TYPE_CHECKING:
    from main import AppServices


class MonthTotal(BaseModel):
    """Verified purchases in one month ("2026-10")."""

    month: str
    total: float


class SupplierSpend(BaseModel):
    """Verified purchases from one supplier."""

    supplier_id: str
    supplier_name: str
    total: float


def last_months(today: date, count: int = 12) -> list[str]:
    """The last ``count`` months as "YYYY-MM", oldest first, ending with this month."""
    months = []
    year, month = today.year, today.month
    for _ in range(count):
        months.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(months))


def monthly_purchases(invoices: list[InvoiceSummary], *, today: date, months: int = 12) -> list[MonthTotal]:
    """Verified purchases per month by invoice date, for the last ``months`` months (0 when none)."""
    keys = last_months(today, months)
    by_month: dict[str, list[float | None]] = {key: [] for key in keys}
    for inv in invoices:
        if inv.status == "verified" and inv.invoice_date is not None:
            key = inv.invoice_date.strftime("%Y-%m")
            if key in by_month:
                by_month[key].append(inv.total)
    return [MonthTotal(month=key, total=money_sum(by_month[key])) for key in keys]


def supplier_spend(invoices: list[InvoiceSummary], *, limit: int = 10) -> list[SupplierSpend]:
    """Verified purchases per supplier, highest first (top ``limit``)."""
    by_supplier: dict[str, tuple[str, list[float | None]]] = {}
    for inv in invoices:
        if inv.status == "verified" and inv.supplier_id:
            name, totals = by_supplier.setdefault(inv.supplier_id, (inv.supplier_name or "Unknown supplier", []))
            totals.append(inv.total)
    spend = [SupplierSpend(supplier_id=sid, supplier_name=name, total=money_sum(totals)) for sid, (name, totals) in by_supplier.items()]
    return sorted(spend, key=lambda row: row.total, reverse=True)[:limit]


async def supplier_lines(services: AppServices, business_id: str, supplier_id: str) -> list[SupplierLine]:
    """The item lines of a supplier's verified invoices, newest first."""
    return await services.db.list_supplier_lines(business_id, supplier_id)
