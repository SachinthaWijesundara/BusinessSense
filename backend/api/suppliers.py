"""Supplier and Payment API: suppliers (e.g. the usual credit period), the Supplier Profile figures,
and supplier payments (record, correct, confirm, reject).

The team's architecture diagram lists no supplier file, but the proposal's Supplier Profile screen
("invoices, item lines, unit prices, credit terms, payments and outstanding balance") and its
"Supplier and Payment Services" need these endpoints, so they live here. Receipt photos are
uploaded through POST /invoices/upload, which reads both invoices and receipts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel

from database.client import (
    CreditDays,
    InvoiceSummary,
    Name,
    NewSupplier,
    NotFoundError,
    PaymentRecord,
    SupplierLine,
    SupplierPaymentStatus,
    SupplierRecord,
)
from services.cash_flow_service import (
    PaymentCreate,
    PaymentFix,
    PaymentReject,
    SupplierTotals,
    confirm_payment,
    fix_payment,
    get_payment_or_404,
    record_payment,
    reject_payment,
    supplier_totals,
    with_receipt_links,
)
from services.invoice_service import WorkflowError
from services.purchase_analysis_service import supplier_lines

if TYPE_CHECKING:
    from main import AppServices

router = APIRouter(prefix="/suppliers", tags=["suppliers"])
payments_router = APIRouter(prefix="/payments", tags=["payments"])


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
    payments: list[PaymentRecord]


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


# ---- suppliers ---------------------------------------------------------------------------------
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
    """Totals (cash flow), invoices, verified item lines (purchase analysis) and payments of one supplier."""
    services, business_id = _context(request)
    supplier = await _get_or_404(services, business_id, supplier_id)
    invoices = await services.db.list_all_invoices(business_id, supplier_id=supplier.id)
    payments = await with_receipt_links(services, await services.db.list_payments(business_id, supplier_id=supplier.id))
    return SupplierProfile(supplier=supplier, totals=supplier_totals(invoices, payments), invoices=invoices,
                           lines=await supplier_lines(services, business_id, supplier.id), payments=payments)


# ---- supplier payments -------------------------------------------------------------------------
@payments_router.get("", response_model=list[PaymentRecord])
async def list_payments(request: Request, supplier_id: UUID | None = None, invoice_id: UUID | None = None,
                        status: SupplierPaymentStatus | None = None) -> list[PaymentRecord]:
    """Payments by supplier, invoice or status (e.g. ``?status=unconfirmed`` for those waiting)."""
    services, business_id = _context(request)
    payments = await services.db.list_payments(
        business_id, supplier_id=str(supplier_id) if supplier_id else None,
        invoice_id=str(invoice_id) if invoice_id else None, status=status,
    )
    return await with_receipt_links(services, payments)


@payments_router.post("", status_code=201, response_model=PaymentRecord)
async def create_payment(request: Request, body: PaymentCreate) -> PaymentRecord:
    """Record a payment typed in by hand (unconfirmed, or confirmed at once with ``confirm: true``)."""
    services, business_id = _context(request)
    return await record_payment(services, business_id, body)


@payments_router.get("/{payment_id}", response_model=PaymentRecord)
async def get_payment(request: Request, payment_id: UUID) -> PaymentRecord:
    """One payment, with a link to its receipt photo."""
    services, business_id = _context(request)
    payment = await get_payment_or_404(services, business_id, str(payment_id))
    return (await with_receipt_links(services, [payment]))[0]


@payments_router.post("/{payment_id}/fix", response_model=PaymentRecord)
async def fix(request: Request, payment_id: UUID, body: PaymentFix) -> PaymentRecord:
    """Owner corrections; the payment goes back to unconfirmed until it is confirmed again."""
    services, business_id = _context(request)
    return await fix_payment(services, business_id, str(payment_id), body)


@payments_router.post("/{payment_id}/confirm", response_model=PaymentRecord)
async def confirm(request: Request, payment_id: UUID) -> PaymentRecord:
    """The owner confirms a payment. 409 with reasons until it is complete and adds up."""
    services, business_id = _context(request)
    return await confirm_payment(services, business_id, str(payment_id))


@payments_router.post("/{payment_id}/reject", response_model=PaymentRecord)
async def reject(request: Request, payment_id: UUID, body: PaymentReject) -> PaymentRecord:
    """Keep a wrong payment out of the ledger, with a reason (kept and logged)."""
    services, business_id = _context(request)
    return await reject_payment(services, business_id, str(payment_id), body)
