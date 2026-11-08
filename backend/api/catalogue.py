"""Suppliers and the master product catalogue."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from database.models import NewProduct, NewSupplier, ProductRecord, SupplierRecord
from database.repository import NotFoundError
from services.container import AppServices
from services.errors import WorkflowError
from services.invoice_workflow import create_product_with_alias

from .deps import get_business_id, get_services

router = APIRouter(tags=["catalogue"])


class SupplierUpdate(BaseModel):
    name: str | None = Field(None, min_length=1)
    credit_days: int | None = Field(None, ge=0)
    phone1: str | None = None
    phone2: str | None = None
    email: str | None = None
    address: str | None = None


@router.get("/suppliers", response_model=list[SupplierRecord])
async def list_suppliers(
    services: AppServices = Depends(get_services),
    business_id: str = Depends(get_business_id),
) -> list[SupplierRecord]:
    return await services.repo.list_suppliers(business_id)


@router.post("/suppliers", status_code=201, response_model=SupplierRecord)
async def create_supplier(
    body: NewSupplier,
    services: AppServices = Depends(get_services),
    business_id: str = Depends(get_business_id),
) -> SupplierRecord:
    return await services.repo.create_supplier(business_id, body)


@router.get("/suppliers/{supplier_id}", response_model=SupplierRecord)
async def get_supplier(
    supplier_id: UUID,
    services: AppServices = Depends(get_services),
    business_id: str = Depends(get_business_id),
) -> SupplierRecord:
    supplier = await services.repo.get_supplier(business_id, str(supplier_id))
    if supplier is None:
        raise WorkflowError(404, "not_found", "Supplier not found.")
    return supplier


@router.patch("/suppliers/{supplier_id}", response_model=SupplierRecord)
async def update_supplier(
    supplier_id: UUID,
    body: SupplierUpdate,
    services: AppServices = Depends(get_services),
    business_id: str = Depends(get_business_id),
) -> SupplierRecord:
    """E.g. record the supplier's usual credit period (credit_days), set manually by the owner."""
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is None:
        raise WorkflowError(422, "invalid", "Supplier name cannot be empty.")
    if not changes:
        return await get_supplier(supplier_id, services, business_id)
    try:
        return await services.repo.update_supplier(business_id, str(supplier_id), changes)
    except NotFoundError as exc:
        raise WorkflowError(404, "not_found", "Supplier not found.") from exc


@router.get("/products", response_model=list[ProductRecord])
async def list_products(
    services: AppServices = Depends(get_services),
    business_id: str = Depends(get_business_id),
) -> list[ProductRecord]:
    return await services.repo.list_products(business_id)


@router.post("/products", status_code=201, response_model=ProductRecord)
async def create_product(
    body: NewProduct,
    services: AppServices = Depends(get_services),
    business_id: str = Depends(get_business_id),
) -> ProductRecord:
    return await create_product_with_alias(services, business_id, body)
