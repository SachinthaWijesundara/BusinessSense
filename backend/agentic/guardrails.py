from __future__ import annotations

from .schemas import ExtractedDocument


def _blank(value: str | None) -> bool:
    return value is None or not str(value).strip()


def missing_fields(doc: ExtractedDocument) -> list[str]:
    """Return mandatory fields that are missing for this document type."""
    missing: list[str] = []
    supplier_name = doc.supplier.name if doc.supplier else None

    if doc.doc_type == "invoice":
        if _blank(supplier_name):
            missing.append("supplier.name")
        inv = doc.invoice
        if inv is None:
            missing.extend(
                [
                    "invoice.invoice_number",
                    "invoice.invoice_date",
                    "invoice.lines",
                ]
            )
            return missing
        if _blank(inv.invoice_number):
            missing.append("invoice.invoice_number")
        if _blank(inv.invoice_date):
            missing.append("invoice.invoice_date")
        usable_lines = [
            line
            for line in inv.lines
            if line.item_name and str(line.item_name).strip()
        ]
        if not usable_lines:
            missing.append("invoice.lines")
        return missing

    # receipt
    rec = doc.receipt
    if rec is None:
        missing.extend(
            [
                "receipt.receipt_number",
                "receipt.receipt_date",
                "receipt.payments",
            ]
        )
        return missing

    if _blank(rec.receipt_number):
        missing.append("receipt.receipt_number")
    if _blank(rec.receipt_date):
        missing.append("receipt.receipt_date")
    # Prefer supplier name when present on paper; if absent, receipt_number is enough
    # for matching later — but we still flag supplier when completely empty AND no number.
    if _blank(supplier_name) and _blank(rec.receipt_number):
        missing.append("supplier.name")

    usable_payments = [
        p
        for p in rec.payments
        if (p.invoice_number and str(p.invoice_number).strip())
        and p.amount is not None
        and float(p.amount) > 0
    ]
    if not usable_payments:
        missing.append("receipt.payments")
    return missing


def guardrail_ok(doc: ExtractedDocument) -> bool:
    return len(missing_fields(doc)) == 0
