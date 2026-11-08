"""InvoiceVisionAgent: reads an invoice or receipt photo into ``ExtractedDocument``.

Built on the OpenAI Agents SDK (``Agent`` + ``Runner``): the SDK enforces the Pydantic output
schema, runs the ``readable_document`` output guardrail, and traces every run. The agent only
reads; it never decides whether a purchase is affordable or a price is fair.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any

from agents import (
    Agent,
    ModelSettings,
    OutputGuardrailTripwireTriggered,
    RunConfig,
    Runner,
    TResponseInputItem,
)
from agents.models.interface import Model

from .guardrails import readable_document_guardrail
from .preprocess import EncodedImage, PreparedImages
from .schemas import ExtractedDocument

INSTRUCTIONS = """You extract structured data from ONE photographed supplier invoice OR supplier payment receipt
from a Sri Lankan hardware shop. Many invoices are dot-matrix printed; receipts may be handwritten.

You receive several images of the SAME document:
1) full - the whole page, for layout
2) supplier_panel - ZOOMED top-LEFT letterhead (supplier identity)
3) header_right - ZOOMED top-RIGHT (invoice number, purchaser block)
4) body - ZOOMED line-item table
5) footer - ZOOMED totals / Spl. Discount / stamps

Rules:
- Read literal printed text (and clearly handwritten field values) only. Never invent values.
- If a value is unreadable, return null and add its path to low_confidence_fields when you could
  read only part of it.
- Ignore pen scribbles, signatures and rubber stamps drawn over cells.
- You only read. Never judge prices, affordability, credit or whether to buy.
- doc_type is "invoice" or "receipt". Set the unused payload (invoice or receipt) to null.

Supplier:
- supplier.name, supplier.address, supplier.phone1 come from the supplier_panel (left block).
- On these invoices the LEFT block is the supplier and the RIGHT block is the purchaser (the shop).
  NEVER put the purchaser / "Purchaser's Name" into supplier.name.

Invoice header:
- invoice_number: prefer header_right (often a red "Invoice No.").
- invoice_date / delivery_date ("Date of Supply"): convert to ISO YYYY-MM-DD.
  Dates on these invoices are usually M/D/YYYY (e.g. 6/13/2023 -> 2023-06-13); when both readings
  are possible, use the one that is a valid calendar date and say so in low_confidence_fields.
- payment_type and credit_days: only when printed (e.g. "Credit 30 days"); otherwise null.

Invoice lines (body):
- sku = Item No / item code exactly as printed, item_name = Description exactly as printed,
  quantity = Qty, mro = Rate (unit price), line_total = Value / Amount.
- discount is a RUPEE amount. If the paper shows Dis%, convert: quantity * rate * dis% / 100.
- Skip blank rows. Keep abbreviations and pack sizes exactly as printed.

Totals (footer):
- special_discount = Spl. Discount in rupees; tax = VAT / tax amount if printed;
  total = invoice total / Total Value of Supply in rupees.

Receipts:
- receipt_number, receipt_date (ISO), payment_method cash|cheque, payments[] (invoice number and
  amount settled for each listed invoice), cheques[] when cheque details are written.
"""

_REGION_LABELS = {
    "supplier_panel": "IMAGE supplier_panel - top-LEFT zoom; supplier name/address/phone ONLY",
    "header_right": "IMAGE header_right - top-RIGHT zoom; invoice number + purchaser (NOT supplier)",
    "body": "IMAGE body - line items table",
    "footer": "IMAGE footer - Spl. Discount, tax and totals",
}


class UnreadableDocument(Exception):
    """The output guardrail tripped: no line items, no date, or nothing usable on a receipt."""

    def __init__(
        self,
        blocking_fields: list[str],
        missing_fields: list[str],
        document: dict[str, Any] | None,
    ) -> None:
        super().__init__(f"unreadable document: {', '.join(blocking_fields)}")
        self.blocking_fields = blocking_fields
        self.missing_fields = missing_fields
        self.document = document


class VisionServiceError(Exception):
    """The model call failed (no API key, network, rate limit, schema failure...)."""


@dataclass(frozen=True)
class VisionResult:
    document: ExtractedDocument
    model: str
    duration_ms: int


def build_invoice_vision_agent(model: str | Model) -> Agent[Any]:
    return Agent(
        name="InvoiceVisionAgent",
        instructions=INSTRUCTIONS,
        model=model,
        model_settings=ModelSettings(temperature=0),
        output_type=ExtractedDocument,
        output_guardrails=[readable_document_guardrail],
    )


def _data_url(image: EncodedImage) -> str:
    return f"data:{image.mime};base64,{base64.b64encode(image.data).decode('ascii')}"


def build_vision_input(images: PreparedImages) -> list[TResponseInputItem]:
    content: list[dict[str, Any]] = [
        {"type": "input_text", "text": "Extract into the schema. Use each labelled crop for its purpose."},
        {"type": "input_text", "text": "IMAGE full - entire document"},
        {"type": "input_image", "image_url": _data_url(images.full), "detail": "high"},
    ]
    for crop in images.regions:
        content.append({"type": "input_text", "text": _REGION_LABELS.get(crop.name, f"IMAGE {crop.name}")})
        content.append({"type": "input_image", "image_url": _data_url(crop), "detail": "high"})
    return [{"role": "user", "content": content}]  # type: ignore[list-item]


class InvoiceVisionReader:
    """Runs InvoiceVisionAgent once per document and maps SDK outcomes to pipeline errors."""

    def __init__(
        self,
        model: str | Model,
        *,
        api_key_configured: bool = True,
        trace_sensitive_data: bool = False,
    ) -> None:
        self._model = model
        self._agent = build_invoice_vision_agent(model)
        self._api_key_configured = api_key_configured
        self._trace_sensitive_data = trace_sensitive_data

    @property
    def model_name(self) -> str:
        return self._model if isinstance(self._model, str) else type(self._model).__name__

    async def read(self, images: PreparedImages, *, group_id: str | None = None) -> VisionResult:
        if isinstance(self._model, str) and not self._api_key_configured:
            raise VisionServiceError("OPENAI_API_KEY is not set")
        started = time.perf_counter()
        try:
            result = await Runner.run(
                self._agent,
                build_vision_input(images),
                max_turns=2,
                run_config=RunConfig(
                    workflow_name="InvoiceIngest",
                    group_id=group_id,
                    trace_include_sensitive_data=self._trace_sensitive_data,
                ),
            )
        except OutputGuardrailTripwireTriggered as exc:
            info = exc.guardrail_result.output.output_info or {}
            raise UnreadableDocument(
                list(info.get("blocking_fields", [])),
                list(info.get("missing_fields", [])),
                info.get("document"),
            ) from exc
        except Exception as exc:  # noqa: BLE001 - surfaced to the client as an API error
            raise VisionServiceError(str(exc) or type(exc).__name__) from exc

        document = result.final_output
        if not isinstance(document, ExtractedDocument):
            raise VisionServiceError("InvoiceVisionAgent returned no structured output")
        return VisionResult(
            document=document,
            model=self.model_name,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
