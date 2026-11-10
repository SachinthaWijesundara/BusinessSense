"""Purchase Analytics API: the proposal's Forecast API for Model 1 (price estimates and anomaly flags).

Routes are thin: they call the purchase analysis service, which uses the shop's trained Model 1
(ml/inference.py) or, before it is trained, the median of recent prices as a labelled baseline.
Like every data route, each one needs the owner's login and reads only that shop's rows.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Query, Request

from services.cash_flow_service import PLANNER_DAYS, CashPlan, cash_plan
from services.invoice_service import WorkflowError, get_invoice_or_404
from services.purchase_analysis_service import (
    AnalyticsOverview,
    CashOverview,
    LineCheck,
    ProductHistory,
    analytics_overview,
    cash_overview,
    invoice_price_check,
    product_history,
)

if TYPE_CHECKING:
    from main import AppServices

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _context(request: Request) -> tuple[AppServices, str]:
    """The services built at startup and the business this request acts for."""
    services = request.app.state.services
    return services, services.business_id_for(request)


@router.get("/overview", response_model=AnalyticsOverview)
async def overview(request: Request) -> AnalyticsOverview:
    """Products with their next expected price, prices flagged in the last year, and spend by
    month and by supplier."""
    services, business_id = _context(request)
    return await analytics_overview(services, business_id, today=date.today())


@router.get("/cash-overview", response_model=CashOverview)
async def cash(
    request: Request,
    payable_days: int = Query(30, ge=1, le=365),
    months: int = Query(3, ge=1, le=36),
) -> CashOverview:
    """Dashboard charts: payable cash-out by day, monthly purchases+paid, supplier and item pies."""
    services, business_id = _context(request)
    return await cash_overview(
        services, business_id, today=date.today(), payable_days=payable_days, months=months
    )


@router.get("/cash-plan", response_model=CashPlan)
async def planner(request: Request, days: int = Query(30)) -> CashPlan:
    """Purchasing Cash Planner: bills falling due, Model 2's expected purchases beside the baseline,
    and the recommended purchasing cash reserve for the next ``days`` days (14, 30, 60 or 90)."""
    if days not in PLANNER_DAYS:
        raise WorkflowError(422, "invalid_period", f"Plan for {', '.join(map(str, PLANNER_DAYS))} days.")
    services, business_id = _context(request)
    return await cash_plan(services, business_id, today=date.today(), days=days)


@router.get("/products/{product_id}", response_model=ProductHistory)
async def product(request: Request, product_id: UUID) -> ProductHistory:
    """One product's unit prices over time with the expected range, and its next expected price."""
    services, business_id = _context(request)
    history = await product_history(services, business_id, str(product_id), today=date.today())
    if history is None:
        raise WorkflowError(404, "not_found", "No verified purchases of this product yet.")
    return history


@router.get("/invoices/{invoice_id}/price-check", response_model=list[LineCheck])
async def price_check(request: Request, invoice_id: UUID) -> list[LineCheck]:
    """Each line's unit price compared with its product's expected range (before verifying)."""
    services, business_id = _context(request)
    invoice = await get_invoice_or_404(services, business_id, str(invoice_id))
    return await invoice_price_check(services, business_id, invoice, today=date.today())
