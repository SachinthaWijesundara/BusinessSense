"""Invoice API (proposal: api/invoices.py, the Invoice Manager): upload, type in, review, fix,
confirm products, verify, reject, mark paid, search; plus the product catalogue the owner picks
from or adds to while confirming matches.

Routes are thin: they read the request, call the pipeline or the invoice service, and return JSON.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, File, Form, Query, Request, UploadFile

from agentic.pipeline import invoice_ingest
from database.client import AuditEvent, Invoice, InvoiceQuery, InvoiceStatus, InvoiceSummary, NewProduct, PaymentStatus, ProductRecord
from services.invoice_service import (
    ConfirmSku,
    InvoiceCreate,
    InvoiceFix,
    PaymentRequest,
    RejectRequest,
    WorkflowError,
    confirm_sku,
    create_manual_invoice,
    create_product_with_alias,
    fix_invoice,
    get_invoice_or_404,
    reject_invoice,
    set_payment_status,
    verify_invoice,
)

if TYPE_CHECKING:
    from main import AppServices

router = APIRouter()
_ACCEPTED_TYPES = ("image/", "application/octet-stream")


def _context(request: Request) -> tuple[AppServices, str]:
    """The services built at startup and the business this request acts for."""
    services = request.app.state.services
    return services, services.business_id_for(request)


async def read_upload(file: UploadFile, max_bytes: int, max_mb: float) -> bytes:
    """Read an uploaded photo; refuse non-images (415) and files over the size limit (413)."""
    content_type = (file.content_type or "").lower()
    if content_type and not content_type.startswith(_ACCEPTED_TYPES):
        raise WorkflowError(415, "unsupported_media_type", "Upload a photo (JPEG, PNG or WebP).")
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise WorkflowError(413, "file_too_large", f"The photo is larger than {max_mb:g} MB.")
    return data


async def _with_image_link(services: AppServices, invoice: Invoice) -> Invoice:
    """Add a short-lived link to the invoice photo, when one is stored."""
    if invoice.image_path and services.storage is not None:
        try:
            invoice.image_url = await services.storage.signed_url(invoice.image_path)
        except Exception:  # noqa: BLE001 - the invoice is still useful without its photo link
            invoice.image_url = None
    return invoice


@router.get("/invoices", response_model=list[InvoiceSummary], tags=["invoices"])
async def list_invoices(
    request: Request,
    supplier_id: UUID | None = None,
    status: InvoiceStatus | None = None,
    payment_status: PaymentStatus | None = None,
    invoice_number: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[InvoiceSummary]:
    """Search invoices by supplier, status, payment status, number and date range."""
    services, business_id = _context(request)
    query = InvoiceQuery(supplier_id=str(supplier_id) if supplier_id else None, status=status, payment_status=payment_status,
                         invoice_number=invoice_number, date_from=date_from, date_to=date_to, limit=limit)
    return await services.db.list_invoices(business_id, query)


@router.post("/invoices/upload", status_code=201, response_model=Invoice, tags=["invoices"])
async def upload_invoice(request: Request, file: UploadFile = File(...), supplier_id: UUID | None = Form(None)) -> Invoice:
    """Read a photo with InvoiceVisionAgent, audit the math, match products and save a draft."""
    services, business_id = _context(request)
    data = await read_upload(file, services.settings.max_upload_bytes, services.settings.max_upload_mb)
    invoice = await invoice_ingest(data, services=services, business_id=business_id,
                                   supplier_id=str(supplier_id) if supplier_id else None, content_type=file.content_type)
    return await _with_image_link(services, invoice)


@router.post("/invoices", status_code=201, response_model=Invoice, tags=["invoices"])
async def create_invoice(request: Request, body: InvoiceCreate) -> Invoice:
    """Save an invoice typed in by hand; the same audit and product matching run on it."""
    services, business_id = _context(request)
    return await create_manual_invoice(services, business_id, body)


@router.get("/invoices/{invoice_id}", response_model=Invoice, tags=["invoices"])
async def get_invoice(request: Request, invoice_id: UUID) -> Invoice:
    """One invoice with its lines, product matches and a link to its photo."""
    services, business_id = _context(request)
    return await _with_image_link(services, await get_invoice_or_404(services, business_id, str(invoice_id)))


@router.get("/invoices/{invoice_id}/events", response_model=list[AuditEvent], tags=["invoices"])
async def list_invoice_events(request: Request, invoice_id: UUID) -> list[AuditEvent]:
    """The audit log of one invoice: corrections, confirmations, verification."""
    services, business_id = _context(request)
    await get_invoice_or_404(services, business_id, str(invoice_id))
    return await services.db.list_events(business_id, str(invoice_id))


@router.post("/invoices/{invoice_id}/fix", response_model=Invoice, tags=["invoices"])
async def fix(request: Request, invoice_id: UUID, body: InvoiceFix) -> Invoice:
    """Owner corrections; re-runs the math audit, and product matching once it passes."""
    services, business_id = _context(request)
    return await _with_image_link(services, await fix_invoice(services, business_id, str(invoice_id), body))


@router.post("/invoices/{invoice_id}/confirm-sku", response_model=Invoice, tags=["invoices"])
async def confirm(request: Request, invoice_id: UUID, body: ConfirmSku) -> Invoice:
    """'Is this the same as [product]?': link a line to a product (or a new one) and keep the alias."""
    services, business_id = _context(request)
    return await _with_image_link(services, await confirm_sku(services, business_id, str(invoice_id), body))


@router.post("/invoices/{invoice_id}/verify", response_model=Invoice, tags=["invoices"])
async def verify(request: Request, invoice_id: UUID) -> Invoice:
    """Mark verified (sets the due date). 409 with reasons until everything is done."""
    services, business_id = _context(request)
    return await _with_image_link(services, await verify_invoice(services, business_id, str(invoice_id)))


@router.post("/invoices/{invoice_id}/reject", response_model=Invoice, tags=["invoices"])
async def reject(request: Request, invoice_id: UUID, body: RejectRequest) -> Invoice:
    """Exclude a draft invoice from the ledger, with a reason (kept and logged)."""
    services, business_id = _context(request)
    return await _with_image_link(services, await reject_invoice(services, business_id, str(invoice_id), body))


@router.post("/invoices/{invoice_id}/payment", response_model=Invoice, tags=["invoices"])
async def payment(request: Request, invoice_id: UUID, body: PaymentRequest) -> Invoice:
    """Mark a verified invoice paid or not paid."""
    services, business_id = _context(request)
    return await _with_image_link(services, await set_payment_status(services, business_id, str(invoice_id), body))


@router.get("/products", response_model=list[ProductRecord], tags=["products"])
async def list_products(request: Request) -> list[ProductRecord]:
    """The master product catalogue, by name."""
    services, business_id = _context(request)
    return await services.db.list_products(business_id)


@router.post("/products", status_code=201, response_model=ProductRecord, tags=["products"])
async def create_product(request: Request, body: NewProduct) -> ProductRecord:
    """Add a product to the catalogue (409 if the name already exists)."""
    services, business_id = _context(request)
    return await create_product_with_alias(services, business_id, body)
