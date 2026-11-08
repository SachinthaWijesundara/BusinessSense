from __future__ import annotations

import json

import pytest
from agents.testing import ScriptedModel, assistant_message, function_call

from agentic.sku_match_agent import LineToMatch, SkuMatcher, build_sku_match_agent
from database.models import NewProduct
from database.repository import InMemoryRepository
from services.alias_service import find_candidates, prepare_catalogue, remember_alias
from services.embeddings import HashingEmbedder, cosine_similarity
from tests.helpers import sku_model

pytestmark = pytest.mark.anyio
BIZ = "00000000-0000-0000-0000-000000000001"
OTHER_BIZ = "00000000-0000-0000-0000-000000000002"


@pytest.fixture
def repo():
    return InMemoryRepository()


@pytest.fixture
def embedder():
    return HashingEmbedder()


async def add_products(repo, embedder, *names):
    products = [await repo.create_product(BIZ, NewProduct(name=name)) for name in names]
    await prepare_catalogue(repo, embedder, BIZ)
    return products


def matcher(repo, embedder, model, threshold=0.82):
    return SkuMatcher(model=model, repo=repo, embedder=embedder, threshold=threshold)


async def test_hashing_embedder_is_deterministic_and_normalised(embedder):
    [a1], [a2], [b] = [await embedder.embed([t]) for t in ("63mm uPVC pipe", "63MM upvc-pipe", "Wall paint 4L")]
    assert a1 == a2
    assert cosine_similarity(a1, a1) == pytest.approx(1.0)
    assert cosine_similarity(a1, b) < 0.5
    assert len(a1) == 1536


async def test_prepare_catalogue_gives_every_product_an_embedded_alias(repo, embedder):
    await repo.create_product(BIZ, NewProduct(name="uPVC Pipe 63mm"))
    assert await prepare_catalogue(repo, embedder, BIZ) == 1
    assert await repo.products_without_aliases(BIZ) == []
    assert await repo.aliases_without_embeddings(BIZ) == []
    assert await prepare_catalogue(repo, embedder, BIZ) == 0


async def test_find_candidates_exact_then_similarity(repo, embedder):
    pipe, paint = await add_products(repo, embedder, "uPVC Pipe 63mm", "Wall Paint 4L")
    exact = await find_candidates(repo, embedder, BIZ, "UPVC pipe  63MM")
    assert [(c.product_id, c.similarity) for c in exact] == [(pipe.id, 1.0)]
    fuzzy = await find_candidates(repo, embedder, BIZ, "upvc pipe 63 mm")
    assert fuzzy[0].product_id == pipe.id and fuzzy[0].similarity < 1.0
    assert await find_candidates(repo, embedder, BIZ, "   ") == []


async def test_candidates_never_cross_businesses(repo, embedder):
    await add_products(repo, embedder, "uPVC Pipe 63mm")
    assert await find_candidates(repo, embedder, OTHER_BIZ, "uPVC Pipe 63mm") == []


async def test_exact_confirmed_alias_links_without_calling_the_agent(repo, embedder):
    [pipe] = await add_products(repo, embedder, "uPVC Pipe 63mm")
    await remember_alias(repo, embedder, BIZ, product_id=pipe.id, raw_text="63mm upvc pipe")
    model = ScriptedModel([])  # any agent call would fail the test
    [result] = await matcher(repo, embedder, model).match(BIZ, [LineToMatch(0, "63MM uPVC Pipe")])
    assert (result.status, result.product_id, result.similarity) == ("linked", pipe.id, 1.0)
    assert model.calls == ()


async def test_agent_pick_above_threshold_is_linked(repo, embedder):
    [pipe] = await add_products(repo, embedder, "uPVC Pipe 63mm")
    model = sku_model(pick="top")
    [result] = await matcher(repo, embedder, model, threshold=0.5).match(BIZ, [LineToMatch(0, "upvc pipe 63 mm")])
    assert result.status == "linked" and result.product_id == pipe.id
    assert 0.5 <= result.similarity < 1.0
    first_call = model.calls[0]
    assert [tool.name for tool in first_call.tools] == ["find_sku_candidates"]


async def test_agent_pick_below_threshold_waits_for_owner(repo, embedder):
    [pipe] = await add_products(repo, embedder, "uPVC Pipe 63mm")
    [result] = await matcher(repo, embedder, sku_model(pick="top"), threshold=0.99).match(
        BIZ, [LineToMatch(0, "upvc pipe 63 mm")]
    )
    assert result.status == "pending"
    assert result.product_id is None and result.suggested_product_id == pipe.id
    assert "below the 0.99 threshold" in result.reason


async def test_gate_uses_the_tool_score_not_the_models_claim(repo, embedder):
    """Even if the model claims certainty in its reason, the gate reads the tool's similarity."""
    [pipe] = await add_products(repo, embedder, "uPVC Pipe 63mm")
    script = ScriptedModel(
        [
            [function_call("find_sku_candidates", {"raw_text": "pipe"}, call_id="c0")],
            [assistant_message(json.dumps({"matches": [
                {"line_index": 0, "product_id": pipe.id, "is_new_product": False, "reason": "100% certain"}
            ]}))],
        ]
    )
    [result] = await matcher(repo, embedder, script).match(BIZ, [LineToMatch(0, "pipe")])
    assert result.status == "pending"
    assert result.similarity is not None and result.similarity < 0.82


async def test_agent_cannot_link_an_id_the_tool_never_returned(repo, embedder):
    await add_products(repo, embedder, "uPVC Pipe 63mm")
    stranger = "11111111-1111-1111-1111-111111111111"
    [result] = await matcher(repo, embedder, sku_model(pick="wrong", wrong_id=stranger), threshold=0.1).match(
        BIZ, [LineToMatch(0, "upvc pipe 63 mm")]
    )
    assert result.status == "pending" and result.product_id is None
    assert "did not return" in result.reason


async def test_new_product_decision(repo, embedder):
    await add_products(repo, embedder, "uPVC Pipe 63mm")
    [result] = await matcher(repo, embedder, sku_model(pick="new")).match(BIZ, [LineToMatch(0, "Wall paint 4L")])
    assert result.status == "new_product" and result.product_id is None
    assert result.candidates  # the owner still sees the nearest products


async def test_empty_catalogue_skips_the_agent(repo, embedder):
    model = ScriptedModel([])
    results = await matcher(repo, embedder, model).match(BIZ, [LineToMatch(0, "a"), LineToMatch(1, "b")])
    assert [r.status for r in results] == ["new_product", "new_product"]
    assert model.calls == ()


async def test_agent_failure_falls_back_to_owner_confirmation(repo, embedder):
    [pipe] = await add_products(repo, embedder, "uPVC Pipe 63mm")
    broken = ScriptedModel([RuntimeError("timeout")])
    [result] = await matcher(repo, embedder, broken).match(BIZ, [LineToMatch(0, "upvc pipe 63 mm")])
    assert result.status == "pending" and result.suggested_product_id == pipe.id
    assert "timeout" in result.reason


async def test_missing_key_falls_back_without_calling_openai(repo, embedder):
    await add_products(repo, embedder, "uPVC Pipe 63mm")
    no_key = SkuMatcher(model="gpt-4o-mini", repo=repo, embedder=embedder, api_key_configured=False)
    [result] = await no_key.match(BIZ, [LineToMatch(0, "upvc pipe 63 mm")])
    assert result.status == "pending" and "OPENAI_API_KEY" in result.reason


async def test_mixed_lines_keep_their_order(repo, embedder):
    pipe, cement = await add_products(repo, embedder, "uPVC Pipe 63mm", "Cement 50kg")
    await remember_alias(repo, embedder, BIZ, product_id=cement.id, raw_text="cement bag 50")
    results = await matcher(repo, embedder, sku_model(pick="top"), threshold=0.5).match(
        BIZ, [LineToMatch(0, "upvc pipe 63 mm"), LineToMatch(1, "Cement Bag 50"), LineToMatch(2, "zzz qqq")]
    )
    assert [r.line_index for r in results] == [0, 1, 2]
    assert results[0].product_id == pipe.id
    assert results[1].product_id == cement.id and results[1].similarity == 1.0
    assert results[2].status in {"pending", "linked", "new_product"}


def test_agent_definition():
    agent = build_sku_match_agent("gpt-4o-mini")
    assert agent.name == "SkuMatchAgent"
    assert [tool.name for tool in agent.tools] == ["find_sku_candidates"]
