"""Supplier API: list, add and update suppliers (e.g. the usual credit period) and the Supplier
Profile figures.

The team's architecture diagram lists no supplier file, but the proposal's Supplier Profile screen
("invoices, item lines, unit prices, credit terms, payments and outstanding balance") and its
"Supplier and Payment Services" need these endpoints, so they live here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel

from database.client import CreditDays, InvoiceSummary, Name, NewSupplier, NotFoundError, SupplierLine, SupplierRecord
from services.cash_flow_service import SupplierTotals, supplier_totals
from services.invoice_service import WorkflowError
from services.purchase_analysis_service import supplier_lines

if TYPE_CHECKING:
    from main import AppServices

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


class SupplierUpdate(BaseModel):
    """Supplier fields the owner may change; only the fields sent are updated."""

    name: Name | None = None
    credit_days: CreditDays | None = None
    phone1: str | None = None
    phone2: str | None = None
    email: str | None = None
    address: str | None = None


class SupplierProfile(BaseModel):
    """Everything the Supplier Profile screen shows."""

    supplier: SupplierRecord
    totals: SupplierTotals
    invoices: list[InvoiceSummary]
    lines: list[SupplierLine]


def _context(request: Request) -> tuple[AppServices, str]:
    """The services built at startup and the business this request acts for."""
    services = request.app.state.services
    return services, services.business_id_for(request)


async def _get_or_404(services: AppServices, business_id: str, supplier_id: UUID) -> SupplierRecord:
    """Load a supplier, or refuse with 404."""
    supplier = await services.db.get_supplier(business_id, str(supplier_id))
    if supplier is None:
        raise WorkflowError(404, "not_found", "Supplier not found.")
    return supplier


@router.get("", response_model=list[SupplierRecord])
async def list_suppliers(request: Request) -> list[SupplierRecord]:
    """All suppliers, by name."""
    services, business_id = _context(request)
    return await services.db.list_suppliers(business_id)


@router.post("", status_code=201, response_model=SupplierRecord)
async def create_supplier(request: Request, body: NewSupplier) -> SupplierRecord:
    """Add a supplier. Refused (409) if a supplier with the same name already exists."""
    services, business_id = _context(request)
    if await services.db.find_suppliers_by_name(business_id, body.name):
        raise WorkflowError(409, "duplicate_supplier", f"A supplier named '{body.name}' already exists.")
    return await services.db.create_supplier(business_id, body)


@router.get("/{supplier_id}", response_model=SupplierRecord)
async def get_supplier(request: Request, supplier_id: UUID) -> SupplierRecord:
    """One supplier."""
    services, business_id = _context(request)
    return await _get_or_404(services, business_id, supplier_id)


@router.post("/{supplier_id}", response_model=SupplierRecord)
async def update_supplier(request: Request, supplier_id: UUID, body: SupplierUpdate) -> SupplierRecord:
    """Change some supplier fields, e.g. ``{"credit_days": 30}``."""
    services, business_id = _context(request)
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is None:
        raise WorkflowError(422, "invalid", "Supplier name cannot be empty.")
    if not changes:
        return await _get_or_404(services, business_id, supplier_id)
    try:
        return await services.db.update_supplier(business_id, str(supplier_id), changes)
    except NotFoundError as exc:
        raise WorkflowError(404, "not_found", "Supplier not found.") from exc


@router.get("/{supplier_id}/profile", response_model=SupplierProfile)
async def supplier_profile(request: Request, supplier_id: UUID) -> SupplierProfile:
    """Totals (cash flow), invoices and verified item lines (purchase analysis) of one supplier."""
    services, business_id = _context(request)
    supplier = await _get_or_404(services, business_id, supplier_id)
    invoices = await services.db.list_all_invoices(business_id, supplier_id=supplier.id)
    return SupplierProfile(supplier=supplier, totals=supplier_totals(invoices), invoices=invoices,
                           lines=await supplier_lines(services, business_id, supplier.id))
