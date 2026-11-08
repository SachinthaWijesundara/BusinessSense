from __future__ import annotations

import base64
import os
from typing import Any

import instructor
from openai import OpenAI

from .regions import RegionCrop
from .schemas import ExtractedDocument

SYSTEM_PROMPT = """You are a JSON-only extraction engine. Output strictly valid JSON matching the requested schema without markdown commentary.

You extract structured data from a supplier invoice OR payment receipt.
You receive MULTIPLE images of the SAME document:
1) full — whole page for layout
2) supplier_panel — ZOOMED top-LEFT letterhead (supplier identity)
3) header_right — ZOOMED top-RIGHT (invoice number, purchaser block)
4) body — ZOOMED line-item table
5) footer — ZOOMED totals / Spl. Discount / stamps

Rules:
- Read literal printed text (and clear handwritten field values) only. Do not invent values.
- Ignore handwritten pen scribbles, signatures, or rubber stamps layered on top of text cells unless extracting receipt totals.
- Do not make affordability, credit, or Take-Wait decisions.
- doc_type: "invoice" or "receipt".

Supplier (CRITICAL):
- Fill supplier.name, supplier.address, supplier.phone1 primarily from IMAGE supplier_panel (left column).
- On many Sri Lankan invoices the LEFT block is the supplier (e.g. TINUDI ENTERPRISES) and the RIGHT block is the PURCHASER / shop (e.g. Rathnayaka Hardware).
- NEVER put the purchaser / "Purchaser's Name" into supplier.name.
- If supplier_panel is unclear, fall back to the left side of IMAGE full — still not the purchaser.

Invoice header:
- invoice_number: prefer IMAGE header_right (often red "Invoice No.").
- invoice_date / delivery_date: from supplier_panel or header ("Date of Supply").
- Convert dates into standard ISO YYYY-MM-DD format (e.g., "10/31/2026" or "31/10/2026" becomes "2026-10-31").
- Phone vs TIN/tax IDs: choose by content type even if columns look misaligned.

Invoice lines (IMAGE body):
- sku = Item No, item_name = Description, quantity = Qty, mro = Rate.
- discount must be a RUPEE amount. If the paper shows Dis%, convert: qty * rate * dis%/100.
- line_total = Value. Skip blank rows. Ignore stamps/signatures drawn across cells (e.g. blue pen over qty/rate/subtotal).

Totals (IMAGE footer):
- special_discount = Spl. Discount (rupees).
- total = Total Value of Supply / consideration (rupees).

Receipts (if doc_type is receipt):
- receipt_number, receipt_date, payment_method cash|cheque, payments[], cheques[].
- Put unused invoice or receipt payload as null.

If a field is unreadable, leave it null rather than guessing.
"""

_REGION_LABELS = {
    "supplier_panel": "IMAGE supplier_panel — top-LEFT zoom; supplier name/address/phone ONLY",
    "header_right": "IMAGE header_right — top-RIGHT zoom; invoice number + purchaser (NOT supplier)",
    "body": "IMAGE body — line items table",
    "footer": "IMAGE footer — Spl. Discount and totals",
}


def _client() -> Any:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    raw = OpenAI(api_key=api_key)
    return instructor.from_openai(raw)


def _data_url(image_bytes: bytes, mime: str) -> str:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


def extract_with_vision(
    image_bytes: bytes,
    mime: str = "image/jpeg",
    *,
    regions: list[RegionCrop] | None = None,
) -> ExtractedDocument:
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
    client = _client()

    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": "Extract into the schema. Use each labeled crop for its purpose.",
        },
        {"type": "text", "text": "IMAGE full — entire document"},
        {"type": "image_url", "image_url": {"url": _data_url(image_bytes, mime)}},
    ]

    for crop in regions or []:
        label = _REGION_LABELS.get(crop.name, f"IMAGE {crop.name}")
        content.append({"type": "text", "text": label})
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": _data_url(crop.png_bytes, crop.mime)},
            }
        )

    return client.chat.completions.create(
        model=model,
        response_model=ExtractedDocument,
        max_retries=2,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
    )
