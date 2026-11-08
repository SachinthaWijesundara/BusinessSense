"""Structured output of InvoiceVisionAgent.

These models are the agent's ``output_type``: the OpenAI Agents SDK turns them into a strict
JSON schema and validates every response against it. Keep every optional field defaulting to
``None`` (or a list factory) so the schema stays strict-mode compatible.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class SupplierDraft(BaseModel):
    name: Optional[str] = Field(
        None, description="Supplier (seller) name from the letterhead. Never the purchaser/shop."
    )
    address: Optional[str] = None
    phone1: Optional[str] = None


class InvoiceLineDraft(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    sku: Optional[str] = Field(None, description="Item code exactly as printed.")
    item_name: Optional[str] = Field(None, description="Item description exactly as printed.")
    quantity: Optional[float] = None
    unit_rate: Optional[float] = Field(None, alias="mro", description="Unit rate / MRO in rupees.")
    discount: Optional[float] = Field(
        None, description="Line discount in rupees (convert Dis% to rupees if paper shows %)."
    )
    line_total: Optional[float] = Field(None, description="Line value in rupees as printed.")


class InvoiceDraft(BaseModel):
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = Field(None, description="ISO date YYYY-MM-DD.")
    delivery_date: Optional[str] = Field(None, description="ISO date YYYY-MM-DD.")
    payment_type: Optional[Literal["cash", "credit"]] = Field(
        None, description="Only when the paper states cash or credit."
    )
    credit_days: Optional[int] = Field(
        None, description="Credit period in days, only when printed on the invoice."
    )
    special_discount: Optional[float] = Field(
        None, description="Invoice-level Spl. Discount in rupees."
    )
    tax: Optional[float] = Field(None, description="Tax / VAT amount in rupees, if printed.")
    total: Optional[float] = Field(None, description="Invoice total in rupees as printed.")
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
    low_confidence_fields: list[str] = Field(
        default_factory=list,
        description=(
            "Dotted paths of values you could only partly read, "
            "e.g. 'invoice.invoice_number' or 'invoice.lines[2].quantity'."
        ),
    )


def usable_lines(invoice: InvoiceDraft | None) -> list[InvoiceLineDraft]:
    """Lines that carry an item description; blank table rows are dropped."""
    if invoice is None:
        return []
    return [line for line in invoice.lines if line.item_name and line.item_name.strip()]
