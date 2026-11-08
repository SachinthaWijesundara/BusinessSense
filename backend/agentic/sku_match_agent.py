"""SkuMatchAgent: links each printed invoice line to a master product in the shop's catalogue.

The agent calls ``find_sku_candidates`` and picks the right candidate (or says "new product").
The decision to link is then made in code: a line is linked only if the chosen product came back
from the tool with similarity >= the threshold (0.82 to start, tuned on owner-verified matches).
Everything else waits for the owner's "Is this the same as ...?" confirmation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from agents import Agent, ModelSettings, RunConfig, Runner
from agents.models.interface import Model
from pydantic import BaseModel, Field

from database.repository import Repository
from services.alias_service import Candidate, exact_candidates, find_candidates, prepare_catalogue
from services.embeddings import Embedder
from services.text import normalize_text

from .tools import SkuMatchContext, find_sku_candidates

INSTRUCTIONS = """You match printed invoice item descriptions from a Sri Lankan hardware shop to the
shop's master product catalogue.

For EVERY input line, call find_sku_candidates with that line's raw_text exactly as given before
you decide. Then return one match per input line, with the same line_index.

- product_id must be one of the candidate product_ids the tool returned for that line.
- If no candidate is the same product, set is_new_product to true and product_id to null.
- A different size, gauge, material, colour, brand or pack size is a DIFFERENT product
  (e.g. "63mm upvc pipe" is not "50mm upvc pipe"; a box of 100 is not a single item).
- Abbreviations are normal on these invoices ("upvc" = "uPVC", "GI" = galvanised iron).
- Never invent product ids. Do not judge prices or quantities.
- reason: one short sentence on why you chose that product or a new product.
"""


class SkuLineMatch(BaseModel):
    line_index: int
    product_id: Optional[str] = Field(description="A candidate product_id, or null for a new product.")
    is_new_product: bool
    reason: str


class SkuMatches(BaseModel):
    matches: list[SkuLineMatch]


def build_sku_match_agent(model: str | Model) -> Agent[SkuMatchContext]:
    return Agent[SkuMatchContext](
        name="SkuMatchAgent",
        instructions=INSTRUCTIONS,
        model=model,
        model_settings=ModelSettings(temperature=0),
        tools=[find_sku_candidates],
        output_type=SkuMatches,
    )


@dataclass(frozen=True)
class LineToMatch:
    line_index: int
    raw_text: str
    sku: str | None = None


@dataclass
class LineMatchResult:
    line_index: int
    status: Literal["linked", "pending", "new_product"]
    product_id: str | None = None
    suggested_product_id: str | None = None
    similarity: float | None = None
    candidates: list[Candidate] = field(default_factory=list)
    reason: str = ""

    def candidates_as_dicts(self, limit: int = 3) -> list[dict[str, Any]]:
        return [candidate.to_dict() for candidate in self.candidates[:limit]]


class SkuMatcher:
    def __init__(
        self,
        *,
        model: str | Model,
        repo: Repository,
        embedder: Embedder,
        threshold: float = 0.82,
        candidate_limit: int = 5,
        api_key_configured: bool = True,
        trace_sensitive_data: bool = False,
    ) -> None:
        self._model = model
        self._agent = build_sku_match_agent(model)
        self._repo = repo
        self._embedder = embedder
        self.threshold = threshold
        self._candidate_limit = candidate_limit
        self._api_key_configured = api_key_configured
        self._trace_sensitive_data = trace_sensitive_data

    @property
    def model_name(self) -> str:
        return self._model if isinstance(self._model, str) else type(self._model).__name__

    async def match(
        self, business_id: str, lines: list[LineToMatch], *, group_id: str | None = None
    ) -> list[LineMatchResult]:
        if not lines:
            return []
        await prepare_catalogue(self._repo, self._embedder, business_id)
        if not await self._repo.has_products(business_id):
            return [
                LineMatchResult(
                    line.line_index,
                    "new_product",
                    reason="The catalogue is empty. Create the product or pick one.",
                )
                for line in lines
            ]

        results: dict[int, LineMatchResult] = {}
        for_agent: list[LineToMatch] = []
        for line in lines:
            exact = await exact_candidates(self._repo, business_id, line.raw_text)
            if len(exact) == 1:
                results[line.line_index] = LineMatchResult(
                    line.line_index,
                    "linked",
                    product_id=exact[0].product_id,
                    similarity=1.0,
                    candidates=exact,
                    reason="Exact match to a confirmed alias.",
                )
            else:
                for_agent.append(line)

        if for_agent:
            for result in await self._match_with_agent(business_id, for_agent, group_id):
                results[result.line_index] = result
        return [results[line.line_index] for line in lines]

    async def _candidates_for(self, context: SkuMatchContext, line: LineToMatch) -> list[Candidate]:
        recorded = context.searches.get(normalize_text(line.raw_text))
        if recorded is not None:
            return recorded
        # The agent skipped the tool for this line: search in code so the gate still has scores.
        candidates = await find_candidates(
            self._repo, self._embedder, context.business_id, line.raw_text, limit=self._candidate_limit
        )
        context.searches[normalize_text(line.raw_text)] = candidates
        return candidates

    async def _match_with_agent(
        self, business_id: str, lines: list[LineToMatch], group_id: str | None
    ) -> list[LineMatchResult]:
        context = SkuMatchContext(
            repo=self._repo,
            embedder=self._embedder,
            business_id=business_id,
            candidate_limit=self._candidate_limit,
        )
        payload = json.dumps(
            [{"line_index": line.line_index, "raw_text": line.raw_text, "sku": line.sku} for line in lines]
        )
        agent_matches: dict[int, SkuLineMatch] = {}
        failure: str | None = None
        if isinstance(self._model, str) and not self._api_key_configured:
            failure = "OPENAI_API_KEY is not set"
        else:
            try:
                result = await Runner.run(
                    self._agent,
                    payload,
                    context=context,
                    max_turns=len(lines) + 3,
                    run_config=RunConfig(
                        workflow_name="SkuMatch",
                        group_id=group_id,
                        trace_include_sensitive_data=self._trace_sensitive_data,
                    ),
                )
                output = result.final_output
                if isinstance(output, SkuMatches):
                    agent_matches = {match.line_index: match for match in output.matches}
            except Exception as exc:  # noqa: BLE001 - fall back to owner confirmation
                failure = str(exc) or type(exc).__name__

        results = []
        for line in lines:
            candidates = await self._candidates_for(context, line)
            if failure is not None:
                results.append(
                    LineMatchResult(
                        line.line_index,
                        "pending",
                        suggested_product_id=candidates[0].product_id if candidates else None,
                        similarity=candidates[0].similarity if candidates else None,
                        candidates=candidates,
                        reason=f"SkuMatchAgent unavailable ({failure}); confirm the product.",
                    )
                )
                continue
            results.append(self._gate(line, agent_matches.get(line.line_index), candidates))
        return results

    def _gate(
        self, line: LineToMatch, match: SkuLineMatch | None, candidates: list[Candidate]
    ) -> LineMatchResult:
        if match is None:
            return LineMatchResult(
                line.line_index,
                "pending",
                suggested_product_id=candidates[0].product_id if candidates else None,
                candidates=candidates,
                reason="SkuMatchAgent returned no decision for this line.",
            )
        if match.is_new_product or not match.product_id:
            return LineMatchResult(
                line.line_index, "new_product", candidates=candidates, reason=match.reason
            )
        chosen = next((c for c in candidates if c.product_id == match.product_id), None)
        if chosen is None:
            return LineMatchResult(
                line.line_index,
                "pending",
                suggested_product_id=candidates[0].product_id if candidates else None,
                candidates=candidates,
                reason="SkuMatchAgent picked a product the search tool did not return.",
            )
        if chosen.similarity >= self.threshold:
            return LineMatchResult(
                line.line_index,
                "linked",
                product_id=chosen.product_id,
                similarity=chosen.similarity,
                candidates=candidates,
                reason=match.reason,
            )
        return LineMatchResult(
            line.line_index,
            "pending",
            suggested_product_id=chosen.product_id,
            similarity=chosen.similarity,
            candidates=candidates,
            reason=(
                f"Similarity {chosen.similarity:.2f} is below the {self.threshold:.2f} threshold. "
                f"{match.reason}"
            ).strip(),
        )
