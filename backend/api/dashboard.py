"""Dashboard API (proposal: Dashboard screen): one GET /dashboard with what needs attention first.

Combines the purchase analysis (monthly purchases, supplier spend) and the cash flow (payments due,
payments waiting for confirmation) services with the review queue. A payment unconfirmed for more
than two days is listed as a reminder; the first time it is shown, the reminder is recorded.
The next-30-day purchasing forecast (Model 2, or its baseline) sits beside its baseline and the
supplier payments already due in that period (proposal: Dashboard screen).
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from pydantic import BaseModel

from database.client import InvoiceSummary, PaymentRecord
from services.cash_flow_service import PaymentDue, payments_due, plan_cash, reminders
from services.purchase_analysis_service import MonthTotal, SupplierSpend, monthly_purchases, supplier_spend

if TYPE_CHECKING:
    from main import AppServices

router = APIRouter(tags=["dashboard"])
FORECAST_DAYS = 30
FORECAST_NOTE = "Purchases expected in the next 30 days, beside the baseline and the bills already due then."


class DashboardForecast(BaseModel):
    """The next-30-day purchasing forecast beside its baseline and the bills already due."""

    days: int
    expected: float
    low: float | None
    high: float | None
    method: str
    is_baseline: bool
    baseline_name: str
    baseline: float
    bills_due: float
    reserve: float
    model_note: str


class Dashboard(BaseModel):
    """Everything the Dashboard screen shows."""

    review_queue: int
    verified_invoices: int
    monthly_purchases: list[MonthTotal]
    supplier_spend: list[SupplierSpend]
    payments_due: list[PaymentDue]
    unconfirmed_payments: int = 0
    payment_reminders: list[PaymentRecord] = []
    forecast: DashboardForecast | None = None
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
    invoices = await services.db.list_all_invoices(business_id)
    dashboard = build_dashboard(invoices, today=date.today(), unconfirmed=unconfirmed)
    model = await services.spend_model(business_id)
    suppliers = await services.db.list_suppliers(business_id)
    plan = plan_cash(today=date.today(), days=FORECAST_DAYS, invoices=invoices,
                     credit_days={s.id: s.credit_days for s in suppliers},
                     unconfirmed=unconfirmed, model=model)
    spend = plan.period_spend
    dashboard.forecast = DashboardForecast(days=FORECAST_DAYS, expected=spend.expected, low=spend.low, high=spend.high,
                                           method=spend.method, is_baseline=spend.is_baseline,
                                           baseline_name=spend.baseline_name, baseline=spend.baseline,
                                           bills_due=plan.bills_due, reserve=plan.reserve, model_note=plan.model.note)
    await services.db.mark_reminders_sent(
        business_id, [payment.id for payment in dashboard.payment_reminders if payment.reminder_sent_at is None])
    return dashboard
