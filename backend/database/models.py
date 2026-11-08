"""Records read from and written to Supabase Postgres (see supabase/migrations)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def _uuid_text(value: str) -> str:
    return str(uuid.UUID(value))


# An id that must be a UUID, kept as text (ids are compared as strings throughout).
UUIDStr = Annotated[str, AfterValidator(_uuid_text)]

InvoiceStatus = Literal["draft", "verified", "rejected"]
MatchStatus = Literal["unmatched", "linked", "pending", "new_product"]
PaymentStatus = Literal["unpaid", "paid", "unconfirmed"]

# Columns a caller may change through Repository.update_invoice.
INVOICE_UPDATABLE_COLUMNS = frozenset(
    {
        "supplier_id",
        "supplier_name_raw",
        "invoice_number",
        "invoice_date",
        "delivery_date",
        "due_date",
        "payment_type",
        "credit_days",
        "special_discount",
        "tax",
        "total",
        "status",
        "math_ok",
        "math_issues",
        "field_issues",
        "missing_fields",
        "low_confidence_fields",
        "image_path",
        "verified_at",
    }
)

SUPPLIER_UPDATABLE_COLUMNS = frozenset(
    {"name", "credit_days", "phone1", "phone2", "email", "address"}
)


class NewSupplier(BaseModel):
    name: str = Field(min_length=1)
    credit_days: int | None = Field(None, ge=0)
    phone1: str | None = None
    phone2: str | None = None
    email: str | None = None
    address: str | None = None


class SupplierRecord(NewSupplier):
    id: str
    business_id: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NewProduct(BaseModel):
    name: str = Field(min_length=1)
    sku: str | None = None
    unit: str | None = None


class ProductRecord(NewProduct):
    id: str
    business_id: str
    created_at: datetime | None = None


class AliasRecord(BaseModel):
    id: str
    product_id: str
    alias_text: str
    normalized_text: str
    source: str
    confirmed: bool


class AliasMatch(BaseModel):
    product_id: str
    product_name: str
    product_sku: str | None = None
    alias_text: str
    similarity: float


class NewInvoiceLine(BaseModel):
    # The column is called "mro" (the paper's unit rate); "mro" would shadow BaseModel.mro.
    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True, serialize_by_alias=True)

    sort_order: int = 0
    sku: str | None = None
    item_name: str
    quantity: float | None = None
    unit_rate: float | None = Field(None, alias="mro", description="Unit rate in rupees.")
    discount: float = 0.0
    line_total: float | None = None
    product_id: str | None = None
    suggested_product_id: str | None = None
    match_status: MatchStatus = "unmatched"
    match_similarity: float | None = None
    match_candidates: list[dict[str, Any]] = Field(default_factory=list)
    match_reason: str | None = None


class InvoiceLineRecord(NewInvoiceLine):
    id: str
    invoice_id: str


class NewInvoice(BaseModel):
    supplier_id: str | None = None
    supplier_name_raw: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    delivery_date: date | None = None
    due_date: date | None = None
    payment_type: Literal["cash", "credit"] | None = None
    credit_days: int | None = None
    special_discount: float = 0.0
    tax: float = 0.0
    total: float | None = None
    status: InvoiceStatus = "draft"
    source: Literal["manual", "agent"] = "manual"
    math_ok: bool | None = None
    math_issues: list[dict[str, Any]] = Field(default_factory=list)
    field_issues: list[dict[str, Any]] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    low_confidence_fields: list[str] = Field(default_factory=list)
    payment_status: PaymentStatus = "unpaid"
    image_path: str | None = None


class InvoiceRecord(NewInvoice):
    id: str
    business_id: str
    verified_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    lines: list[InvoiceLineRecord] = Field(default_factory=list)


class InvoiceQuery(BaseModel):
    supplier_id: str | None = None
    status: InvoiceStatus | None = None
    invoice_number: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    limit: int = Field(100, ge=1, le=500)


class LineMatchUpdate(BaseModel):
    product_id: str | None = None
    suggested_product_id: str | None = None
    match_status: MatchStatus
    match_similarity: float | None = None
    match_candidates: list[dict[str, Any]] = Field(default_factory=list)
    match_reason: str | None = None


class ExtractionRunLog(BaseModel):
    invoice_id: str | None = None
    agent: str
    model: str | None = None
    guardrail_passed: bool | None = None
    raw_output: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int | None = None


class AuditEvent(BaseModel):
    id: str
    invoice_id: str | None
    event_type: str
    payload: dict[str, Any]
    created_at: datetime | None = None
