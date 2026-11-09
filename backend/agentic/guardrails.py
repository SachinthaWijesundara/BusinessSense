"""The output guardrail on InvoiceVisionAgent, and the list of fields it left empty.

* ``blocking_fields`` drives the Agents SDK output guardrail: an invoice with no line items or no
  date (or a receipt with nothing on it) trips the wire. Nothing is saved and the owner is asked
  to retake the photo.
* ``missing_fields`` lists required fields that are empty but do not block; the invoice is saved
  as a draft with those fields highlighted.
"""

from __future__ import annotations

from typing import Any

from agents import Agent, GuardrailFunctionOutput, RunContextWrapper, output_guardrail

from .schemas import ExtractedDocument, usable_lines


def _blank(value: object) -> bool:
    """True for None or text that is only spaces."""
    return value is None or not str(value).strip()


def blocking_fields(doc: ExtractedDocument) -> list[str]:
    """Fields whose absence makes the read unusable (the guardrail's tripwire)."""
    if doc.doc_type == "invoice":
        inv = doc.invoice
        if inv is None:
            return ["invoice.lines", "invoice.invoice_date"]
        blocking: list[str] = []
        if not usable_lines(inv):
            blocking.append("invoice.lines")
        if _blank(inv.invoice_date):
            blocking.append("invoice.invoice_date")
        return blocking

    rec = doc.receipt
    if rec is None:
        return ["receipt.payments"]
    has_payment = any(
        not _blank(payment.invoice_number) or payment.amount is not None for payment in rec.payments
    )
    if not has_payment and _blank(rec.receipt_number):
        return ["receipt.payments"]
    return []


def missing_fields(doc: ExtractedDocument) -> list[str]:
    """Required fields that are empty for this kind of document."""
    missing: list[str] = []
    supplier_name = doc.supplier.name if doc.supplier else None

    if doc.doc_type == "invoice":
        if _blank(supplier_name):
            missing.append("supplier.name")
        inv = doc.invoice
        if inv is None:
            return [*missing, "invoice.invoice_number", "invoice.invoice_date", "invoice.lines"]
        if _blank(inv.invoice_number):
            missing.append("invoice.invoice_number")
        if _blank(inv.invoice_date):
            missing.append("invoice.invoice_date")
        if not usable_lines(inv):
            missing.append("invoice.lines")
        if inv.total is None:
            missing.append("invoice.total")
        return missing

    rec = doc.receipt
    if rec is None:
        return ["receipt.receipt_number", "receipt.receipt_date", "receipt.payments"]
    if _blank(rec.receipt_number):
        missing.append("receipt.receipt_number")
    if _blank(rec.receipt_date):
        missing.append("receipt.receipt_date")
    if _blank(supplier_name) and _blank(rec.receipt_number):
        missing.append("supplier.name")
    usable_payments = [
        payment
        for payment in rec.payments
        if not _blank(payment.invoice_number) and payment.amount is not None and float(payment.amount) > 0
    ]
    if not usable_payments:
        missing.append("receipt.payments")
    return missing


@output_guardrail(name="readable_document")
async def readable_document_guardrail(
    ctx: RunContextWrapper[Any], agent: Agent[Any], output: ExtractedDocument
) -> GuardrailFunctionOutput:
    """Trip when the agent's answer has no line items or no date (SDK output guardrail)."""
    blocking = blocking_fields(output)
    return GuardrailFunctionOutput(
        output_info={
            "blocking_fields": blocking,
            "missing_fields": missing_fields(output),
            "document": output.model_dump(by_alias=True),
        },
        tripwire_triggered=bool(blocking),
    )
