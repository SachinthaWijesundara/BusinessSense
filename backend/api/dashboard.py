"""Dashboard API (proposal: Dashboard screen): one GET /dashboard with what needs attention first.

Combines the purchase analysis (monthly purchases, supplier spend) and the cash flow (payments due)
services with the review queue. The Model 2 forecast is not built yet, so it is null with a note.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from pydantic import BaseModel

from database.client import InvoiceSummary
from services.cash_flow_service import PaymentDue, payments_due
from services.purchase_analysis_service import MonthTotal, SupplierSpend, monthly_purchases, supplier_spend

if TYPE_CHECKING:
    from main import AppServices

router = APIRouter(tags=["dashboard"])
FORECAST_NOTE = "Model 2 (purchasing expenditure forecast) is not built yet."


class Dashboard(BaseModel):
    """Everything the Dashboard screen shows."""

    review_queue: int
    verified_invoices: int
    monthly_purchases: list[MonthTotal]
    supplier_spend: list[SupplierSpend]
    payments_due: list[PaymentDue]
    forecast: None = None
    forecast_note: str = FORECAST_NOTE


def build_dashboard(invoices: list[InvoiceSummary], *, today: date) -> Dashboard:
    """Assemble the Dashboard figures from all of the business's invoices."""
    return Dashboard(
        review_queue=sum(1 for inv in invoices if inv.status == "draft"),
        verified_invoices=sum(1 for inv in invoices if inv.status == "verified"),
        monthly_purchases=monthly_purchases(invoices, today=today),
        supplier_spend=supplier_spend(invoices),
        payments_due=payments_due(invoices, today=today),
    )


def _context(request: Request) -> tuple[AppServices, str]:
    """The services built at startup and the business this request acts for."""
    services = request.app.state.services
    return services, services.business_id_for(request)


@router.get("/dashboard", response_model=Dashboard)
async def dashboard(request: Request) -> Dashboard:
    """Review queue, monthly purchases, supplier spend and payments due."""
    services, business_id = _context(request)
    return build_dashboard(await services.db.list_all_invoices(business_id), today=date.today())
