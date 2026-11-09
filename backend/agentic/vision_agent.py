"""InvoiceVisionAgent: reads an invoice or receipt photo into ``ExtractedDocument``.

1. Image preparation (proposal: "add image clean-up"): decode the photo with its EXIF rotation,
   lift faint dot-matrix print with CLAHE, cut four zoomed crops of the usual Sri Lankan supplier
   layout, and cap everything sent to OpenAI at 2048 px.
2. The agent: built on the OpenAI Agents SDK (``Agent`` + ``Runner``). The SDK enforces the
   Pydantic output schema, runs the ``readable_document`` output guardrail and traces every run.
   The agent only reads; it never decides whether a purchase is affordable or a price is fair.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from agents import Agent, ModelSettings, OutputGuardrailTripwireTriggered, RunConfig, Runner, TResponseInputItem
from agents.models.interface import Model

from .guardrails import readable_document_guardrail
from .schemas import ExtractedDocument

# =============================================================================================
# 1. Image preparation
# =============================================================================================

MAX_MODEL_SIDE = 2048  # the largest size OpenAI uses for high-detail images
JPEG_QUALITY = 92

# (name, top, bottom, left, right, zoom) as fractions of the page: supplier letterhead top-left,
# invoice number and purchaser top-right, line-item table in the middle, totals at the bottom.
REGION_SPECS: tuple[tuple[str, float, float, float, float, float], ...] = (
    ("supplier_panel", 0.0, 0.35, 0.0, 0.48, 2.5),
    ("header_right", 0.0, 0.35, 0.48, 1.0, 2.0),
    ("body", 0.32, 0.72, 0.0, 1.0, 1.5),
    ("footer", 0.68, 1.0, 0.0, 1.0, 1.5),
)
REGION_NAMES = tuple(spec[0] for spec in REGION_SPECS)


class ImagePreprocessError(ValueError):
    """The upload cannot be decoded or processed as an image."""


@dataclass(frozen=True)
class EncodedImage:
    """One image ready to send: its name, bytes and mime type."""

    name: str
    data: bytes
    mime: str


@dataclass(frozen=True)
class PreparedImages:
    """What the agent reads (full page + crops) and the cleaned page kept with the invoice."""

    full: EncodedImage
    regions: list[EncodedImage]
    preview_png: bytes


def decode_image(image_bytes: bytes) -> np.ndarray:
    """Decode to a 3-channel picture; IMREAD_COLOR also applies the phone's EXIF rotation."""
    if not image_bytes:
        raise ImagePreprocessError("empty image")
    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise ImagePreprocessError("could not decode image")
    return image


def apply_clahe(image: np.ndarray) -> np.ndarray:
    """Boost local contrast on the lightness channel only, so colours stay natural."""
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    lightness, a_chan, b_chan = cv2.split(cv2.cvtColor(image, cv2.COLOR_BGR2LAB))
    return cv2.cvtColor(cv2.merge((clahe.apply(lightness), a_chan, b_chan)), cv2.COLOR_LAB2BGR)


def limit_longest_side(image: np.ndarray, max_side: int = MAX_MODEL_SIDE) -> np.ndarray:
    """Shrink (never enlarge) so the longest side is at most ``max_side`` pixels."""
    height, width = image.shape[:2]
    if max(height, width) <= max_side:
        return image
    scale = max_side / max(height, width)
    return cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv2.INTER_AREA)


def crop_regions(image: np.ndarray, *, max_side: int = MAX_MODEL_SIDE) -> list[tuple[str, np.ndarray]]:
    """Cut and zoom the four layout regions; a zoom never pushes a crop past ``max_side``."""
    height, width = image.shape[:2]
    crops: list[tuple[str, np.ndarray]] = []
    for name, top, bottom, left, right, zoom in REGION_SPECS:
        y0 = max(0, min(height - 1, int(height * top)))
        y1 = max(y0 + 1, min(height, int(height * bottom)))
        x0 = max(0, min(width - 1, int(width * left)))
        x1 = max(x0 + 1, min(width, int(width * right)))
        band = image[y0:y1, x0:x1]
        band_h, band_w = band.shape[:2]
        scale = min(zoom, max_side / max(band_h, band_w))
        if abs(scale - 1.0) < 1e-6:
            crops.append((name, band.copy()))
            continue
        size = (max(1, round(band_w * scale)), max(1, round(band_h * scale)))
        crops.append((name, cv2.resize(band, size, interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)))
    return crops


def _encode(image: np.ndarray, extension: str, params: list[int] | None = None) -> bytes:
    """Encode pixels as PNG (lossless, kept with the invoice) or JPEG (small, sent to OpenAI)."""
    ok, encoded = cv2.imencode(extension, image, params or [])
    if not ok:
        raise ImagePreprocessError(f"could not encode {extension}")
    return encoded.tobytes()


def prepare_images(image_bytes: bytes) -> PreparedImages:
    """Decode, clean and cut everything InvoiceVisionAgent reads."""
    enhanced = apply_clahe(decode_image(image_bytes))
    page = limit_longest_side(enhanced)
    jpeg = [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
    return PreparedImages(
        full=EncodedImage("full", _encode(page, ".jpg", jpeg), "image/jpeg"),
        regions=[EncodedImage(name, _encode(crop, ".jpg", jpeg), "image/jpeg") for name, crop in crop_regions(enhanced)],
        preview_png=_encode(page, ".png"),
    )


# =============================================================================================
# 2. The agent
# =============================================================================================

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

    def __init__(self, blocking_fields: list[str], missing_fields: list[str], document: dict[str, Any] | None) -> None:
        """Keep which fields blocked the read and what the agent did read."""
        super().__init__(f"unreadable document: {', '.join(blocking_fields)}")
        self.blocking_fields = blocking_fields
        self.missing_fields = missing_fields
        self.document = document


class VisionServiceError(Exception):
    """The model call failed (no API key, network, rate limit, schema failure...)."""


@dataclass(frozen=True)
class VisionResult:
    """A successful read: the document, which model read it and how long it took."""

    document: ExtractedDocument
    model: str
    duration_ms: int


def build_invoice_vision_agent(model: str | Model) -> Agent[Any]:
    """Create InvoiceVisionAgent: instructions, strict output schema and output guardrail."""
    return Agent(
        name="InvoiceVisionAgent",
        instructions=INSTRUCTIONS,
        model=model,
        model_settings=ModelSettings(temperature=0),
        output_type=ExtractedDocument,
        output_guardrails=[readable_document_guardrail],
    )


def _data_url(image: EncodedImage) -> str:
    """Embed an image in the request as a base64 data URL."""
    return f"data:{image.mime};base64,{base64.b64encode(image.data).decode('ascii')}"


def build_vision_input(images: PreparedImages) -> list[TResponseInputItem]:
    """The user message: the full page and the four labelled crops, all at high detail."""
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
    """Runs InvoiceVisionAgent once per photo and turns SDK outcomes into pipeline errors."""

    def __init__(self, model: str | Model, *, api_key_configured: bool = True, trace_sensitive_data: bool = False) -> None:
        """Build the agent for a model name (or a scripted test model)."""
        self._model = model
        self._agent = build_invoice_vision_agent(model)
        self._api_key_configured = api_key_configured
        self._trace_sensitive_data = trace_sensitive_data

    @property
    def model_name(self) -> str:
        """The model's name, for the extraction log."""
        return self._model if isinstance(self._model, str) else type(self._model).__name__

    async def read(self, images: PreparedImages, *, group_id: str | None = None) -> VisionResult:
        """Read one photo. Raises UnreadableDocument (guardrail) or VisionServiceError (call failed)."""
        if isinstance(self._model, str) and not self._api_key_configured:
            raise VisionServiceError("OPENAI_API_KEY is not set")
        started = time.perf_counter()
        try:
            result = await Runner.run(
                self._agent,
                build_vision_input(images),
                max_turns=2,
                run_config=RunConfig(workflow_name="InvoiceIngest", group_id=group_id,
                                     trace_include_sensitive_data=self._trace_sensitive_data),
            )
        except OutputGuardrailTripwireTriggered as exc:
            info = exc.guardrail_result.output.output_info or {}
            raise UnreadableDocument(list(info.get("blocking_fields", [])), list(info.get("missing_fields", [])),
                                     info.get("document")) from exc
        except Exception as exc:  # noqa: BLE001 - surfaced to the client as an API error
            raise VisionServiceError(str(exc) or type(exc).__name__) from exc

        document = result.final_output
        if not isinstance(document, ExtractedDocument):
            raise VisionServiceError("InvoiceVisionAgent returned no structured output")
        return VisionResult(document=document, model=self.model_name, duration_ms=int((time.perf_counter() - started) * 1000))
