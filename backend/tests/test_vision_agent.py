from __future__ import annotations

import json

import pytest
from agents.agent_output import AgentOutputSchema
from agents.testing import ScriptedModel, assistant_message

from agentic.preprocess import prepare_images
from agentic.schemas import ExtractedDocument
from agentic.sku_match_agent import SkuMatches
from agentic.vision_agent import (
    InvoiceVisionReader,
    UnreadableDocument,
    VisionServiceError,
    build_invoice_vision_agent,
)
from tests.helpers import invoice_doc, invoice_image, receipt_doc, vision_model

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def images():
    return prepare_images(invoice_image())


@pytest.mark.parametrize("output_type", [ExtractedDocument, SkuMatches])
def test_output_schemas_are_strict(output_type):
    """OpenAI structured outputs need a strict schema; the SDK must accept ours as-is."""
    schema = AgentOutputSchema(output_type)
    assert schema.is_strict_json_schema()
    assert schema.json_schema()["additionalProperties"] is False


def test_agent_definition():
    agent = build_invoice_vision_agent("gpt-4o")
    assert agent.name == "InvoiceVisionAgent"
    assert agent.output_type is ExtractedDocument
    assert [g.get_name() for g in agent.output_guardrails] == ["readable_document"]
    assert agent.model_settings.temperature == 0


async def test_reads_invoice(images):
    model = vision_model(invoice_doc())
    result = await InvoiceVisionReader(model).read(images)
    assert result.document.invoice.invoice_number == "INV-1001"
    assert result.document.invoice.lines[0].unit_rate == 585.0
    assert result.document.model_dump(by_alias=True)["invoice"]["lines"][0]["mro"] == 585.0


async def test_model_receives_page_and_four_labelled_crops(images):
    model = vision_model(invoice_doc())
    await InvoiceVisionReader(model).read(images)
    [call] = model.calls
    content = call.input[0]["content"]
    pictures = [part for part in content if part["type"] == "input_image"]
    labels = [part["text"] for part in content if part["type"] == "input_text"]
    assert len(pictures) == 5
    assert all(p["image_url"].startswith("data:image/jpeg;base64,") and p["detail"] == "high" for p in pictures)
    assert any("supplier_panel" in label for label in labels)
    assert call.output_schema is not None and call.output_schema.is_strict_json_schema()
    assert "NEVER put the purchaser" in call.system_instructions


async def test_reads_receipt(images):
    result = await InvoiceVisionReader(vision_model(receipt_doc())).read(images)
    assert result.document.doc_type == "receipt"
    assert result.document.receipt.payments[0].amount == 6750.0


@pytest.mark.parametrize(
    ("doc", "blocking"),
    [
        (invoice_doc(lines=[], total=0), ["invoice.lines"]),
        (invoice_doc(date=None), ["invoice.invoice_date"]),
        ({"doc_type": "invoice", "supplier": {}, "invoice": None, "receipt": None, "low_confidence_fields": []},
         ["invoice.lines", "invoice.invoice_date"]),
    ],
)
async def test_output_guardrail_trips(images, doc, blocking):
    with pytest.raises(UnreadableDocument) as caught:
        await InvoiceVisionReader(vision_model(doc)).read(images)
    assert caught.value.blocking_fields == blocking
    assert caught.value.document is not None


async def test_schema_violation_is_an_api_error(images):
    bad = ScriptedModel([[assistant_message(json.dumps({"doc_type": "letter"}))]])
    with pytest.raises(VisionServiceError):
        await InvoiceVisionReader(bad).read(images)


async def test_model_failure_is_an_api_error(images):
    with pytest.raises(VisionServiceError, match="rate limited"):
        await InvoiceVisionReader(vision_model(RuntimeError("rate limited"))).read(images)


async def test_missing_api_key_fails_before_calling_openai(images):
    reader = InvoiceVisionReader("gpt-4o", api_key_configured=False)
    with pytest.raises(VisionServiceError, match="OPENAI_API_KEY is not set"):
        await reader.read(images)
