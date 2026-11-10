"""Purchase Analytics API: the proposal's Forecast API for Model 1 (price estimates and anomaly flags).

Routes are thin: they call the purchase analysis service, which uses the shop's trained Model 1
(ml/inference.py) or, before it is trained, the median of recent prices as a labelled baseline.
Like every data route, each one needs the owner's login and reads only that shop's rows.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Request

from services.invoice_service import WorkflowError, get_invoice_or_404
from services.purchase_analysis_service import (
    AnalyticsOverview,
    LineCheck,
    ProductHistory,
    analytics_overview,
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
