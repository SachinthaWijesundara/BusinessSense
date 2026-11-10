"""Dashboard API (proposal: Dashboard screen): one GET /dashboard with what needs attention first.

Combines the purchase analysis (monthly purchases, supplier spend) and the cash flow (payments due,
payments waiting for confirmation) services with the review queue. A payment unconfirmed for more
than two days is listed as a reminder; the first time it is shown, the reminder is recorded.
The Model 2 forecast is not built yet, so it is null with a note.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from pydantic import BaseModel

from database.client import InvoiceSummary, PaymentRecord
from services.cash_flow_service import PaymentDue, payments_due, reminders
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
    unconfirmed_payments: int = 0
    payment_reminders: list[PaymentRecord] = []
    forecast: None = None
    forecast_note: str = FORECAST_NOTE


def build_dashboard(invoices: list[InvoiceSummary], *, today: date,
                    unconfirmed: list[PaymentRecord] | None = None) -> Dashboard:
    """Assemble the Dashboard figures from the business's invoices and its unconfirmed payments."""
    unconfirmed = unconfirmed or []
    return Dashboard(
        review_queue=sum(1 for inv in invoices if inv.status == "draft"),
        verified_invoices=sum(1 for inv in invoices if inv.status == "verified"),
        monthly_purchases=monthly_purchases(invoices, today=today),
        supplier_spend=supplier_spend(invoices),
        payments_due=payments_due(invoices, today=today),
        unconfirmed_payments=len(unconfirmed),
        payment_reminders=reminders(unconfirmed),
    )


def _context(request: Request) -> tuple[AppServices, str]:
    """The services built at startup and the business this request acts for."""
    services = request.app.state.services
    return services, services.business_id_for(request)


@router.get("/dashboard", response_model=Dashboard)
async def dashboard(request: Request) -> Dashboard:
    """Review queue, monthly purchases, supplier spend, payments due and payment reminders."""
    services, business_id = _context(request)
    unconfirmed = await services.db.list_payments(business_id, status="unconfirmed")
    dashboard = build_dashboard(await services.db.list_all_invoices(business_id), today=date.today(),
                                unconfirmed=unconfirmed)
    await services.db.mark_reminders_sent(
        business_id, [payment.id for payment in dashboard.payment_reminders if payment.reminder_sent_at is None])
    return dashboard
