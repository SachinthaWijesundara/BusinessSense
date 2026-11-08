"""Invoice API: upload, review, fix, confirm products, verify, search."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from agentic.pipeline import invoice_ingest
from database.models import AuditEvent, InvoiceQuery, InvoiceRecord, InvoiceStatus
from services.container import AppServices
from services.errors import WorkflowError
from services.invoice_workflow import ConfirmSku, InvoiceFix, confirm_sku, fix_invoice, verify_invoice

from .deps import get_business_id, get_services, read_upload

router = APIRouter(prefix="/invoices", tags=["invoices"])


class InvoiceDetail(InvoiceRecord):
    image_signed_url: str | None = None


async def _detail(services: AppServices, record: InvoiceRecord) -> InvoiceDetail:
    signed = None
    if record.image_path:
        try:
            signed = await services.images.signed_url(record.image_path)
        except Exception:  # noqa: BLE001 - the invoice is still useful without its photo link
            signed = None
    return InvoiceDetail(**record.model_dump(), image_signed_url=signed)


@router.post("/upload", status_code=201, response_model=InvoiceDetail)
async def upload_invoice(
    file: UploadFile = File(...),
    supplier_id: UUID | None = Form(None),
    services: AppServices = Depends(get_services),
    business_id: str = Depends(get_business_id),
) -> InvoiceDetail:
    """Read a photo with InvoiceVisionAgent, audit the math, match products, save a draft."""
    data = await read_upload(file, services.settings)
    record = await invoice_ingest(
        data,
        services=services,
        business_id=business_id,
        supplier_id=str(supplier_id) if supplier_id else None,
        content_type=file.content_type,
    )
    return await _detail(services, record)


@router.get("", response_model=list[InvoiceRecord])
async def list_invoices(
    supplier_id: UUID | None = None,
    status: InvoiceStatus | None = None,
    invoice_number: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(100, ge=1, le=500),
    services: AppServices = Depends(get_services),
    business_id: str = Depends(get_business_id),
) -> list[InvoiceRecord]:
    query = InvoiceQuery(
        supplier_id=str(supplier_id) if supplier_id else None,
        status=status,
        invoice_number=invoice_number,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    return await services.repo.list_invoices(business_id, query)


@router.get("/{invoice_id}", response_model=InvoiceDetail)
async def get_invoice(
    invoice_id: UUID,
    services: AppServices = Depends(get_services),
    business_id: str = Depends(get_business_id),
) -> InvoiceDetail:
    record = await services.repo.get_invoice(business_id, str(invoice_id))
    if record is None:
        raise WorkflowError(404, "not_found", "Invoice not found.")
    return await _detail(services, record)


@router.get("/{invoice_id}/events", response_model=list[AuditEvent])
async def list_invoice_events(
    invoice_id: UUID,
    services: AppServices = Depends(get_services),
    business_id: str = Depends(get_business_id),
) -> list[AuditEvent]:
    if await services.repo.get_invoice(business_id, str(invoice_id)) is None:
        raise WorkflowError(404, "not_found", "Invoice not found.")
    return await services.repo.list_events(business_id, str(invoice_id))


@router.post("/{invoice_id}/fix", response_model=InvoiceDetail)
async def fix(
    invoice_id: UUID,
    body: InvoiceFix,
    services: AppServices = Depends(get_services),
    business_id: str = Depends(get_business_id),
) -> InvoiceDetail:
    """Owner corrections. Re-runs the math audit; product matching runs once it passes."""
    record = await fix_invoice(services, business_id, str(invoice_id), body)
    return await _detail(services, record)


@router.post("/{invoice_id}/confirm-sku", response_model=InvoiceDetail)
async def confirm(
    invoice_id: UUID,
    body: ConfirmSku,
    services: AppServices = Depends(get_services),
    business_id: str = Depends(get_business_id),
) -> InvoiceDetail:
    """'Is this the same as [product]?' - link a line to a product (or a new one) and keep the alias."""
    record = await confirm_sku(services, business_id, str(invoice_id), body)
    return await _detail(services, record)


@router.post("/{invoice_id}/verify", response_model=InvoiceDetail)
async def verify(
    invoice_id: UUID,
    services: AppServices = Depends(get_services),
    business_id: str = Depends(get_business_id),
) -> InvoiceDetail:
    """Mark the invoice verified. Refused (409) until supplier, number, date, math and products are done."""
    record = await verify_invoice(services, business_id, str(invoice_id))
    return await _detail(services, record)
