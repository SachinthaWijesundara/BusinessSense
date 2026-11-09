"""Tests for the agent layer: InvoiceVisionAgent (with its image preparation) and SkuMatchAgent.

The agents run through the real OpenAI Agents SDK ``Runner`` with the SDK's ``ScriptedModel``
instead of OpenAI, so the output schema, the guardrail, tool calls and the 0.82 gate all really
run. Two opt-in live tests at the end call OpenAI (RUN_LIVE_TESTS=1 and OPENAI_API_KEY).
"""

from __future__ import annotations

import json
import os

import cv2
import numpy as np
import pytest
from agents.agent_output import AgentOutputSchema
from agents.testing import ScriptedModel, assistant_message, function_call

from agentic.schemas import ExtractedDocument
from agentic.sku_match_agent import LineToMatch, SkuMatcher, SkuMatches, build_sku_match_agent
from agentic.vision_agent import (
    MAX_MODEL_SIDE,
    REGION_NAMES,
    ImagePreprocessError,
    InvoiceVisionReader,
    UnreadableDocument,
    VisionServiceError,
    apply_clahe,
    build_invoice_vision_agent,
    decode_image,
    prepare_images,
)
from database.client import NewProduct
from services.alias_service import OpenAIEmbedder, find_candidates, prepare_catalogue, remember_alias
from tests.conftest import BIZ, HashingEmbedder, cosine, invoice_doc, invoice_image, receipt_doc, sku_model, vision_model

OTHER_BIZ = "00000000-0000-0000-0000-000000000002"


def _decode(data: bytes) -> np.ndarray:
    """Decode encoded image bytes back to pixels."""
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


# ---- image clean-up ----------------------------------------------------------------------------
def test_prepare_images_outputs():
    """The full page and the four crops are JPEGs; the kept copy is a PNG."""
    prepared = prepare_images(invoice_image())
    assert prepared.full.mime == "image/jpeg" and prepared.preview_png.startswith(b"\x89PNG")
    assert [crop.name for crop in prepared.regions] == list(REGION_NAMES)


def test_small_crops_are_zoomed_and_large_photos_capped():
    """Crops are zoomed in; nothing sent to the model is larger than 2048 px."""
    small = prepare_images(invoice_image(800, 1100))
    assert _decode(small.regions[0].data).shape[1] == pytest.approx(800 * 0.48 * 2.5, abs=2)
    large = prepare_images(invoice_image(3000, 4000))
    assert max(_decode(large.full.data).shape[:2]) == MAX_MODEL_SIDE
    assert all(max(_decode(crop.data).shape[:2]) <= MAX_MODEL_SIDE for crop in large.regions)
    assert len(large.full.data) < 2_000_000


@pytest.mark.parametrize("ext", [".png", ".jpg", ".webp"])
def test_common_formats_grayscale_and_alpha(ext):
    """JPEG, PNG and WebP decode; grayscale and transparent images become 3-channel."""
    assert prepare_images(invoice_image(600, 800, ext=ext)).full.data
    for image in (np.full((40, 30), 200, np.uint8), np.dstack([np.zeros((40, 30, 3), np.uint8), np.full((40, 30), 255, np.uint8)])):
        assert decode_image(cv2.imencode(".png", image)[1].tobytes()).shape == (40, 30, 3)


def test_contrast_is_enhanced():
    """CLAHE makes faint print stand out more."""
    faint = np.full((300, 300, 3), 200, np.uint8)
    cv2.putText(faint, "faint", (20, 150), cv2.FONT_HERSHEY_SIMPLEX, 2, (185, 185, 185), 3)
    assert apply_clahe(faint).std() > faint.std()


@pytest.mark.parametrize("data", [b"", b"not an image", b"\x89PNG\r\n\x1a\n broken"])
def test_bad_input_raises(data):
    """Empty or broken files are refused."""
    with pytest.raises(ImagePreprocessError):
        prepare_images(data)


# ---- InvoiceVisionAgent ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def images():
    """One cleaned synthetic invoice photo."""
    return prepare_images(invoice_image())


@pytest.mark.parametrize("output_type", [ExtractedDocument, SkuMatches])
def test_output_schemas_are_strict(output_type):
    """OpenAI structured outputs need a strict schema; the SDK accepts ours as is."""
    schema = AgentOutputSchema(output_type)
    assert schema.is_strict_json_schema() and schema.json_schema()["additionalProperties"] is False


def test_vision_agent_definition():
    """The agent has its schema, guardrail and temperature 0."""
    agent = build_invoice_vision_agent("gpt-4o")
    assert (agent.name, agent.output_type) == ("InvoiceVisionAgent", ExtractedDocument)
    assert [g.get_name() for g in agent.output_guardrails] == ["readable_document"]
    assert agent.model_settings.temperature == 0


@pytest.mark.anyio
async def test_reads_invoice_and_sends_five_labelled_images(images):
    """A read returns the document; the model got the page plus four labelled crops."""
    model = vision_model(invoice_doc())
    result = await InvoiceVisionReader(model).read(images)
    assert result.document.invoice.invoice_number == "INV-1001"
    assert result.document.model_dump(by_alias=True)["invoice"]["lines"][0]["mro"] == 585.0
    [call] = model.calls
    content = call.input[0]["content"]
    pictures = [part for part in content if part["type"] == "input_image"]
    assert len(pictures) == 5 and all(p["image_url"].startswith("data:image/jpeg;base64,") for p in pictures)
    assert call.output_schema.is_strict_json_schema() and "NEVER put the purchaser" in call.system_instructions


@pytest.mark.anyio
async def test_reads_receipt(images):
    """Receipts are read too."""
    result = await InvoiceVisionReader(vision_model(receipt_doc())).read(images)
    assert result.document.doc_type == "receipt"


@pytest.mark.anyio
@pytest.mark.parametrize(("doc", "blocking"), [
    (invoice_doc(lines=[], total=0), ["invoice.lines"]),
    (invoice_doc(date=None), ["invoice.invoice_date"]),
])
async def test_output_guardrail_trips(images, doc, blocking):
    """No line items or no date: the SDK guardrail stops the run."""
    with pytest.raises(UnreadableDocument) as caught:
        await InvoiceVisionReader(vision_model(doc)).read(images)
    assert caught.value.blocking_fields == blocking and caught.value.document is not None


@pytest.mark.anyio
async def test_model_failures_are_api_errors(images):
    """A schema violation, a model error or a missing key become VisionServiceError."""
    with pytest.raises(VisionServiceError):
        await InvoiceVisionReader(ScriptedModel([[assistant_message(json.dumps({"doc_type": "letter"}))]])).read(images)
    with pytest.raises(VisionServiceError, match="rate limited"):
        await InvoiceVisionReader(vision_model(RuntimeError("rate limited"))).read(images)
    with pytest.raises(VisionServiceError, match="OPENAI_API_KEY is not set"):
        await InvoiceVisionReader("gpt-4o", api_key_configured=False).read(images)


# ---- SkuMatchAgent (needs the test database) -------------------------------------------------
async def _catalogue(db, embedder, *names):
    """Add products and give them embedded catalogue aliases."""
    products = [await db.create_product(BIZ, NewProduct(name=name)) for name in names]
    await prepare_catalogue(db, embedder, BIZ)
    return products


def _matcher(db, model, threshold=0.82):
    """A SkuMatcher with the offline embedder."""
    return SkuMatcher(model=model, db=db, embedder=HashingEmbedder(), threshold=threshold)


def test_sku_agent_definition_and_embedder():
    """The agent has its one tool; the offline embedder is deterministic."""
    assert [tool.name for tool in build_sku_match_agent("gpt-4o-mini").tools] == ["find_sku_candidates"]
    embedder = HashingEmbedder()
    a1, a2, b = (embedder._vector(t) for t in ("63mm uPVC pipe", "63MM upvc-pipe", "Wall paint 4L"))
    assert a1 == a2 and cosine(a1, a1) == pytest.approx(1.0) and cosine(a1, b) < 0.5


@pytest.mark.postgres
@pytest.mark.anyio
async def test_catalogue_search(db):
    """Exact alias first, then similarity; never across businesses; aliases get embeddings."""
    embedder = HashingEmbedder()
    pipe, _ = await _catalogue(db, embedder, "uPVC Pipe 63mm", "Wall Paint 4L")
    assert await db.products_without_aliases(BIZ) == [] and await db.aliases_without_embeddings(BIZ) == []
    exact = await find_candidates(db, embedder, BIZ, "UPVC pipe  63MM")
    assert [(c.product_id, c.similarity) for c in exact] == [(pipe.id, 1.0)]
    fuzzy = await find_candidates(db, embedder, BIZ, "upvc pipe 63 mm")
    assert fuzzy[0].product_id == pipe.id and fuzzy[0].similarity < 1.0
    assert await find_candidates(db, embedder, OTHER_BIZ, "uPVC Pipe 63mm") == []
    assert await find_candidates(db, embedder, BIZ, "   ") == []


@pytest.mark.postgres
@pytest.mark.anyio
async def test_exact_alias_links_without_the_agent(db):
    """A confirmed alias links straight away; the agent is not called."""
    embedder = HashingEmbedder()
    [pipe] = await _catalogue(db, embedder, "uPVC Pipe 63mm")
    await remember_alias(db, embedder, BIZ, product_id=pipe.id, raw_text="63mm upvc pipe")
    model = ScriptedModel([])
    [result] = await _matcher(db, model).match(BIZ, [LineToMatch(0, "63MM uPVC Pipe")])
    assert (result.status, result.product_id, result.similarity) == ("linked", pipe.id, 1.0) and model.calls == ()


@pytest.mark.postgres
@pytest.mark.anyio
async def test_similarity_gate(db):
    """Above the threshold: linked. Below: pending with a suggestion. Uses the tool's score."""
    [pipe] = await _catalogue(db, HashingEmbedder(), "uPVC Pipe 63mm")
    model = sku_model(pick="top")
    [linked] = await _matcher(db, model, threshold=0.5).match(BIZ, [LineToMatch(0, "upvc pipe 63 mm")])
    assert linked.status == "linked" and linked.product_id == pipe.id and 0.5 <= linked.similarity < 1.0
    assert [tool.name for tool in model.calls[0].tools] == ["find_sku_candidates"]
    [pending] = await _matcher(db, sku_model(pick="top"), threshold=0.99).match(BIZ, [LineToMatch(0, "upvc pipe 63 mm")])
    assert pending.status == "pending" and pending.product_id is None and pending.suggested_product_id == pipe.id
    assert "below the 0.99 threshold" in pending.reason
    claim = ScriptedModel([
        [function_call("find_sku_candidates", {"raw_text": "pipe"}, call_id="c0")],
        [assistant_message(json.dumps({"matches": [{"line_index": 0, "product_id": pipe.id, "is_new_product": False,
                                                     "reason": "100% certain"}]}))],
    ])
    [gated] = await _matcher(db, claim).match(BIZ, [LineToMatch(0, "pipe")])
    assert gated.status == "pending" and gated.similarity < 0.82


@pytest.mark.postgres
@pytest.mark.anyio
async def test_agent_mistakes_and_failures_wait_for_the_owner(db):
    """An unknown id, a new-product answer, an empty catalogue, an agent error and a missing key."""
    await _catalogue(db, HashingEmbedder(), "uPVC Pipe 63mm")
    stranger = "11111111-1111-1111-1111-111111111111"
    [wrong] = await _matcher(db, sku_model(pick="wrong", wrong_id=stranger), threshold=0.1).match(BIZ, [LineToMatch(0, "upvc pipe")])
    assert wrong.status == "pending" and "did not return" in wrong.reason
    [new] = await _matcher(db, sku_model(pick="new")).match(BIZ, [LineToMatch(0, "Wall paint 4L")])
    assert new.status == "new_product" and new.candidates
    [broken] = await _matcher(db, ScriptedModel([RuntimeError("timeout")])).match(BIZ, [LineToMatch(0, "upvc pipe 63 mm")])
    assert broken.status == "pending" and "unavailable" in broken.reason and broken.suggested_product_id
    assert "timeout" not in broken.reason  # raw provider errors are logged, never shown to the owner
    no_key = SkuMatcher(model="gpt-4o-mini", db=db, embedder=HashingEmbedder(), api_key_configured=False)
    [keyless] = await no_key.match(BIZ, [LineToMatch(0, "upvc pipe 63 mm")])
    assert keyless.status == "pending" and "unavailable" in keyless.reason


@pytest.mark.postgres
@pytest.mark.anyio
async def test_empty_catalogue_and_line_order(db):
    """With no products the agent is skipped; results keep the lines' order."""
    model = ScriptedModel([])
    results = await _matcher(db, model).match(BIZ, [LineToMatch(0, "a"), LineToMatch(1, "b")])
    assert [r.status for r in results] == ["new_product", "new_product"] and model.calls == ()
    embedder = HashingEmbedder()
    pipe, cement = await _catalogue(db, embedder, "uPVC Pipe 63mm", "Cement 50kg")
    await remember_alias(db, embedder, BIZ, product_id=cement.id, raw_text="cement bag 50")
    ordered = await _matcher(db, sku_model(pick="top"), threshold=0.5).match(
        BIZ, [LineToMatch(0, "upvc pipe 63 mm"), LineToMatch(1, "Cement Bag 50"), LineToMatch(2, "zzz qqq")]
    )
    assert [r.line_index for r in ordered] == [0, 1, 2]
    assert ordered[0].product_id == pipe.id and (ordered[1].product_id, ordered[1].similarity) == (cement.id, 1.0)


# ---- opt-in live OpenAI calls ----------------------------------------------------------------
live = pytest.mark.skipif(not (os.getenv("RUN_LIVE_TESTS") == "1" and os.getenv("OPENAI_API_KEY")),
                          reason="set RUN_LIVE_TESTS=1 and OPENAI_API_KEY to call OpenAI")


@pytest.mark.live
@live
@pytest.mark.anyio
async def test_live_vision_agent():
    """One real InvoiceVisionAgent call on a synthetic photo (costs a few cents)."""
    from agents import set_default_openai_key

    set_default_openai_key(os.environ["OPENAI_API_KEY"])
    try:
        result = await InvoiceVisionReader(os.getenv("INVOICE_VISION_MODEL", "gpt-4o")).read(prepare_images(invoice_image()))
    except UnreadableDocument:
        pytest.skip("the synthetic photo has no date, so the guardrail may trip; the call itself worked")
    assert result.document.doc_type == "invoice"


@pytest.mark.live
@live
@pytest.mark.anyio
async def test_live_embeddings():
    """One real text-embedding-3-small call."""
    embedder = OpenAIEmbedder(model="text-embedding-3-small", dimensions=1536, api_key=os.environ["OPENAI_API_KEY"])
    [vector] = await embedder.embed(["63mm upvc pipe"])
    assert len(vector) == 1536
    await embedder.aclose()
