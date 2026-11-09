"""The form InvoiceVisionAgent must fill in (its Pydantic ``output_type``).

The OpenAI Agents SDK turns these models into a strict JSON schema and validates every answer
against it. Every optional field defaults to ``None`` (or an empty list) so the schema stays
compatible with OpenAI's strict structured outputs.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class SupplierDraft(BaseModel):
    """The supplier block of the paper (the seller, never the shop that bought)."""

    name: Optional[str] = Field(
        None, description="Supplier (seller) name from the letterhead. Never the purchaser/shop."
    )
    address: Optional[str] = None
    phone1: Optional[str] = None


class InvoiceLineDraft(BaseModel):
    """One printed item line, values exactly as on the paper."""

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
    """The invoice header, totals and lines."""

    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = Field(None, description="ISO date YYYY-MM-DD.")
    delivery_date: Optional[str] = Field(None, description="ISO date YYYY-MM-DD.")
    payment_type: Optional[Literal["cash", "credit"]] = Field(
        None, description="Only when the paper states cash or credit."
    )
    credit_days: Optional[int] = Field(
        None, description="Credit period in days, only when printed on the invoice."
    )
    special_discount: Optional[float] = Field(None, description="Invoice-level Spl. Discount in rupees.")
    tax: Optional[float] = Field(None, description="Tax / VAT amount in rupees, if printed.")
    total: Optional[float] = Field(None, description="Invoice total in rupees as printed.")
    lines: list[InvoiceLineDraft] = Field(default_factory=list)


class ReceiptPaymentDraft(BaseModel):
    """One invoice settled on a payment receipt."""

    invoice_number: Optional[str] = None
    amount: Optional[float] = None


class ReceiptChequeDraft(BaseModel):
    """Cheque details written on a receipt."""

    cheque_number: Optional[str] = None
    bank_name: Optional[str] = None
    cheque_date: Optional[str] = None


class ReceiptDraft(BaseModel):
    """A supplier payment receipt."""

    receipt_number: Optional[str] = None
    receipt_date: Optional[str] = None
    payment_method: Optional[Literal["cash", "cheque"]] = None
    payments: list[ReceiptPaymentDraft] = Field(default_factory=list)
    cheques: list[ReceiptChequeDraft] = Field(default_factory=list)


class ExtractedDocument(BaseModel):
    """Everything the agent read from one photo: an invoice or a payment receipt."""

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
