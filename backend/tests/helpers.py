"""Test builders: fictional invoice data, synthetic photos and scripted (offline) models.

All names and numbers here are made up. Never put the participating shop's real supplier names,
invoice numbers or photos in this public repository.
"""

from __future__ import annotations

import json
from typing import Any

import cv2
import numpy as np
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call

SUPPLIER = "Lanka Pipe Traders"


def invoice_line(item: str, qty: float, rate: float, *, discount: float = 0.0, sku: str | None = None,
                 line_total: float | None = None) -> dict[str, Any]:
    return {
        "sku": sku,
        "item_name": item,
        "quantity": qty,
        "mro": rate,
        "discount": discount,
        "line_total": round(qty * rate - discount, 2) if line_total is None else line_total,
    }


def invoice_doc(
    *,
    supplier: str | None = SUPPLIER,
    number: str | None = "INV-1001",
    date: str | None = "2026-10-20",
    lines: list[dict[str, Any]] | None = None,
    total: float | None = None,
    special_discount: float | None = None,
    tax: float | None = None,
    low_confidence: list[str] | None = None,
) -> dict[str, Any]:
    lines = (
        [invoice_line("63mm uPVC pipe", 10, 585.0, sku="PP-063"), invoice_line("PVC solvent cement 250ml", 2, 450.0)]
        if lines is None
        else lines
    )
    if total is None:
        total = round(
            sum(line["line_total"] or 0 for line in lines) - (special_discount or 0) + (tax or 0), 2
        )
    return {
        "doc_type": "invoice",
        "supplier": {"name": supplier, "address": "12 Main Street", "phone1": None},
        "invoice": {
            "invoice_number": number,
            "invoice_date": date,
            "delivery_date": None,
            "payment_type": None,
            "credit_days": None,
            "special_discount": special_discount,
            "tax": tax,
            "total": total,
            "lines": lines,
        },
        "receipt": None,
        "low_confidence_fields": low_confidence or [],
    }


def receipt_doc() -> dict[str, Any]:
    return {
        "doc_type": "receipt",
        "supplier": {"name": SUPPLIER, "address": None, "phone1": None},
        "invoice": None,
        "receipt": {
            "receipt_number": "R-77",
            "receipt_date": "2026-10-22",
            "payment_method": "cash",
            "payments": [{"invoice_number": "INV-1001", "amount": 6750.0}],
            "cheques": [],
        },
        "low_confidence_fields": [],
    }


def invoice_image(width: int = 1240, height: int = 1754, ext: str = ".jpg") -> bytes:
    """A synthetic invoice-like page (letterhead, table, totals)."""
    image = np.full((height, width, 3), 236, dtype=np.uint8)
    ink = (60, 60, 60)
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = width / 1240
    cv2.putText(image, "LANKA PIPE TRADERS", (int(60 * scale), int(120 * scale)), font, 1.4 * scale, ink, 2)
    cv2.putText(image, "Invoice No INV-1001", (int(760 * scale), int(120 * scale)), font, 1.0 * scale, ink, 2)
    for row in range(6):
        y = int((700 + row * 70) * scale)
        cv2.putText(image, f"PP-06{row}  item {row}   10   585.00   5,850.00", (int(60 * scale), y), font, 0.9 * scale, ink, 2)
    cv2.putText(image, "Total 6,750.00", (int(760 * scale), int(1500 * scale)), font, 1.0 * scale, ink, 2)
    ok, encoded = cv2.imencode(ext, image)
    assert ok
    return encoded.tobytes()


def vision_step(doc: dict[str, Any] | Exception) -> Any:
    """One scripted InvoiceVisionAgent answer (or a raised error)."""
    return doc if isinstance(doc, Exception) else [assistant_message(json.dumps(doc))]


def vision_model(*docs: dict[str, Any] | Exception) -> ScriptedModel:
    """InvoiceVisionAgent stand-in: one scripted answer per Runner.run call."""
    return ScriptedModel([vision_step(doc) for doc in docs])


def _get(item: Any, key: str) -> Any:
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def auto_sku_responder(*, pick: str = "top", wrong_id: str | None = None):
    """SkuMatchAgent stand-in that behaves like the real agent: first call the tool for every
    line, then pick the top candidate (pick="top"), a new product (pick="new"), or an id the tool
    never returned (pick="wrong")."""

    def respond(call: Any) -> Any:
        items = call.input if isinstance(call.input, list) else [{"role": "user", "content": call.input}]
        user = next(item for item in items if _get(item, "role") == "user")
        lines = json.loads(_get(user, "content"))
        outputs = [item for item in items if _get(item, "type") == "function_call_output"]
        if not outputs:
            return [
                function_call("find_sku_candidates", {"raw_text": line["raw_text"]}, call_id=f"call_{line['line_index']}")
                for line in lines
            ]
        found = {}
        for output in outputs:
            data = json.loads(_get(output, "output"))
            found[data["raw_text"]] = data["candidates"]
        matches = []
        for line in lines:
            candidates = found.get(line["raw_text"], [])
            if pick == "top" and candidates:
                matches.append({"line_index": line["line_index"], "product_id": candidates[0]["product_id"],
                                "is_new_product": False, "reason": "Top candidate."})
            elif pick == "wrong":
                matches.append({"line_index": line["line_index"], "product_id": wrong_id,
                                "is_new_product": False, "reason": "Guessed."})
            else:
                matches.append({"line_index": line["line_index"], "product_id": None,
                                "is_new_product": True, "reason": "No candidate is the same product."})
        return [assistant_message(json.dumps({"matches": matches}))]

    return respond


def sku_model(*, pick: str = "top", wrong_id: str | None = None, calls: int = 200) -> ScriptedModel:
    responder = auto_sku_responder(pick=pick, wrong_id=wrong_id)
    return ScriptedModel([ModelStep.respond(responder) for _ in range(calls)])
