from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class SupplierDraft(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    phone1: Optional[str] = None


class InvoiceLineDraft(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    sku: Optional[str] = None
    item_name: Optional[str] = None
    quantity: Optional[float] = None
    unit_rate: Optional[float] = Field(
        None, alias="mro", description="Unit rate / MRO"
    )
    discount: Optional[float] = Field(
        None,
        description="Discount as rupee amount (convert Dis% to rupees if paper shows %)",
    )
    line_total: Optional[float] = None


class InvoiceDraft(BaseModel):
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = Field(
        None, description="ISO date YYYY-MM-DD when possible"
    )
    delivery_date: Optional[str] = None
    special_discount: Optional[float] = Field(
        None, description="Invoice-level Spl. Discount in rupees"
    )
    total: Optional[float] = None
    lines: list[InvoiceLineDraft] = Field(default_factory=list)


class ReceiptPaymentDraft(BaseModel):
    invoice_number: Optional[str] = None
    amount: Optional[float] = None


class ReceiptChequeDraft(BaseModel):
    cheque_number: Optional[str] = None
    bank_name: Optional[str] = None
    cheque_date: Optional[str] = None


class ReceiptDraft(BaseModel):
    receipt_number: Optional[str] = None
    receipt_date: Optional[str] = None
    payment_method: Optional[Literal["cash", "cheque"]] = None
    payments: list[ReceiptPaymentDraft] = Field(default_factory=list)
    cheques: list[ReceiptChequeDraft] = Field(default_factory=list)


class ExtractedDocument(BaseModel):
    """Structured extract from an invoice or payment-receipt photo."""

    doc_type: Literal["invoice", "receipt"]
    supplier: SupplierDraft = Field(default_factory=SupplierDraft)
    invoice: Optional[InvoiceDraft] = None
    receipt: Optional[ReceiptDraft] = None


class ExtractErrorBody(BaseModel):
    error_type: Literal["reading_unsuccessful", "api_error"]
    message: str
    missing_fields: list[str] = Field(default_factory=list)
