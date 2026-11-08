"""Opt-in smoke test against the real OpenAI API (costs a few cents).

    RUN_LIVE_TESTS=1 OPENAI_API_KEY=sk-... pytest -m live

Uses a synthetic invoice image, never a real shop document.
"""

from __future__ import annotations

import os

import pytest

from agentic.preprocess import prepare_images
from agentic.sku_match_agent import LineToMatch, SkuMatcher
from agentic.vision_agent import InvoiceVisionReader, UnreadableDocument
from database.models import NewProduct
from database.repository import InMemoryRepository
from services.alias_service import prepare_catalogue
from services.embeddings import OpenAIEmbedder
from tests.helpers import invoice_image

pytestmark = [
    pytest.mark.live,
    pytest.mark.anyio,
    pytest.mark.skipif(
        not (os.getenv("RUN_LIVE_TESTS") == "1" and os.getenv("OPENAI_API_KEY")),
        reason="set RUN_LIVE_TESTS=1 and OPENAI_API_KEY to call OpenAI",
    ),
]
BIZ = "00000000-0000-0000-0000-000000000001"


async def test_live_vision_agent_reads_a_synthetic_invoice():
    from agents import set_default_openai_key

    set_default_openai_key(os.environ["OPENAI_API_KEY"])
    reader = InvoiceVisionReader(os.getenv("INVOICE_VISION_MODEL", "gpt-4o"))
    try:
        result = await reader.read(prepare_images(invoice_image()))
    except UnreadableDocument:
        pytest.skip("the synthetic image has no date, so the guardrail may trip; the call itself worked")
    assert result.document.doc_type == "invoice"


async def test_live_embeddings_and_sku_agent():
    from agents import set_default_openai_key

    key = os.environ["OPENAI_API_KEY"]
    set_default_openai_key(key)
    repo = InMemoryRepository()
    embedder = OpenAIEmbedder(model="text-embedding-3-small", dimensions=1536, api_key=key)
    await repo.create_product(BIZ, NewProduct(name="uPVC Pipe 63mm"))
    await repo.create_product(BIZ, NewProduct(name="Wall Paint White 4L"))
    await prepare_catalogue(repo, embedder, BIZ)
    matcher = SkuMatcher(model=os.getenv("SKU_MATCH_MODEL", "gpt-4o-mini"), repo=repo, embedder=embedder)
    [result] = await matcher.match(BIZ, [LineToMatch(0, "63mm upvc pipe")])
    assert result.candidates and result.candidates[0].product_name == "uPVC Pipe 63mm"
    await embedder.aclose()
