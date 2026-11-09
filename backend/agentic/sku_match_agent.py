"""SkuMatchAgent: links each printed invoice line to a master product in the shop's catalogue.

The agent calls ``find_sku_candidates`` and picks the right candidate (or says "new product").
The decision to link is made in code: a line is linked only when the chosen product came back
from the tool with similarity >= the threshold (0.82 to start, tuned on owner-verified matches).
Everything else waits for the owner's "Is this the same as ...?" confirmation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Optional

from agents import Agent, ModelSettings, RunConfig, Runner
from agents.models.interface import Model
from pydantic import BaseModel, Field

from services.alias_service import Candidate, Embedder, exact_candidates, find_candidates, normalize_text, prepare_catalogue

from .tools import SkuMatchContext, find_sku_candidates

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from database.client import Database

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
    """The agent's decision for one line."""

    line_index: int
    product_id: Optional[str] = Field(description="A candidate product_id, or null for a new product.")
    is_new_product: bool
    reason: str


class SkuMatches(BaseModel):
    """The agent's decisions for all lines (its output_type)."""

    matches: list[SkuLineMatch]


def build_sku_match_agent(model: str | Model) -> Agent[SkuMatchContext]:
    """Create SkuMatchAgent with its search tool and strict output schema."""
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
    """A line to match: its position, printed description and item code."""

    line_index: int
    raw_text: str
    sku: str | None = None


@dataclass
class LineMatchResult:
    """The final, code-checked result for one line."""

    line_index: int
    status: Literal["linked", "pending", "new_product"]
    product_id: str | None = None
    suggested_product_id: str | None = None
    similarity: float | None = None
    candidates: list[Candidate] = field(default_factory=list)
    reason: str = ""

    def candidates_as_dicts(self, limit: int = 3) -> list[dict[str, Any]]:
        """The top candidates as JSON, for the owner's confirmation screen."""
        return [candidate.to_dict() for candidate in self.candidates[:limit]]


class SkuMatcher:
    """Runs exact-alias lookups, SkuMatchAgent and the similarity gate for a set of lines."""

    def __init__(
        self,
        *,
        model: str | Model,
        db: Database,
        embedder: Embedder,
        threshold: float = 0.82,
        candidate_limit: int = 5,
        api_key_configured: bool = True,
        trace_sensitive_data: bool = False,
    ) -> None:
        """Build the agent for a model name (or a scripted test model)."""
        self._model = model
        self._agent = build_sku_match_agent(model)
        self._db = db
        self._embedder = embedder
        self.threshold = threshold
        self._candidate_limit = candidate_limit
        self._api_key_configured = api_key_configured
        self._trace_sensitive_data = trace_sensitive_data

    async def match(self, business_id: str, lines: list[LineToMatch], *, group_id: str | None = None) -> list[LineMatchResult]:
        """Match every line, in the same order. Exact confirmed aliases link without the agent."""
        if not lines:
            return []
        try:
            await prepare_catalogue(self._db, self._embedder, business_id)
        except Exception:  # noqa: BLE001 - embeddings unavailable: exact matches below still work
            logger.exception("could not embed the catalogue; continuing with exact matches only")
        if not await self._db.has_products(business_id):
            return [
                LineMatchResult(line.line_index, "new_product", reason="The catalogue is empty. Create the product or pick one.")
                for line in lines
            ]

        results: dict[int, LineMatchResult] = {}
        for_agent: list[LineToMatch] = []
        for line in lines:
            exact = await exact_candidates(self._db, business_id, line.raw_text)
            if len(exact) > 1:
                # The same printed text is confirmed for several products: the owner must choose.
                results[line.line_index] = LineMatchResult(
                    line.line_index, "pending", suggested_product_id=exact[0].product_id, similarity=1.0,
                    candidates=exact, reason="This text matches more than one product; pick the right one.")
            elif len(exact) == 1:
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
        """The tool's candidates for a line; searched in code if the agent skipped the tool."""
        recorded = context.searches.get(normalize_text(line.raw_text))
        if recorded is not None:
            return recorded
        try:
            candidates = await find_candidates(
                self._db, self._embedder, context.business_id, line.raw_text, limit=self._candidate_limit
            )
        except Exception:  # noqa: BLE001 - search unavailable (e.g. embeddings blocked): no suggestions
            logger.exception("product search failed")
            candidates = []
        context.searches[normalize_text(line.raw_text)] = candidates
        return candidates

    async def _match_with_agent(self, business_id: str, lines: list[LineToMatch], group_id: str | None) -> list[LineMatchResult]:
        """Ask SkuMatchAgent, then gate each answer. If the agent fails, every line waits for the owner."""
        context = SkuMatchContext(
            db=self._db, embedder=self._embedder, business_id=business_id, candidate_limit=self._candidate_limit
        )
        payload = json.dumps([{"line_index": l.line_index, "raw_text": l.raw_text, "sku": l.sku} for l in lines])
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
                if isinstance(result.final_output, SkuMatches):
                    agent_matches = {match.line_index: match for match in result.final_output.matches}
            except Exception as exc:  # noqa: BLE001 - fall back to owner confirmation
                logger.exception("SkuMatchAgent failed")
                failure = type(exc).__name__

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
                        reason="Automatic product matching is unavailable right now; pick the product.",
                    )
                )
            else:
                results.append(self._gate(line, agent_matches.get(line.line_index), candidates))
        return results

    def _gate(self, line: LineToMatch, match: SkuLineMatch | None, candidates: list[Candidate]) -> LineMatchResult:
        """The 0.82 rule: link only a tool-returned product whose tool similarity reaches the threshold."""
        top = candidates[0].product_id if candidates else None
        if match is None:
            return LineMatchResult(line.line_index, "pending", suggested_product_id=top, candidates=candidates,
                                   reason="SkuMatchAgent returned no decision for this line.")
        if match.is_new_product or not match.product_id:
            return LineMatchResult(line.line_index, "new_product", candidates=candidates, reason=match.reason)
        chosen = next((c for c in candidates if c.product_id == match.product_id), None)
        if chosen is None:
            return LineMatchResult(line.line_index, "pending", suggested_product_id=top, candidates=candidates,
                                   reason="SkuMatchAgent picked a product the search tool did not return.")
        if chosen.similarity >= self.threshold:
            return LineMatchResult(line.line_index, "linked", product_id=chosen.product_id,
                                   similarity=chosen.similarity, candidates=candidates, reason=match.reason)
        return LineMatchResult(
            line.line_index,
            "pending",
            suggested_product_id=chosen.product_id,
            similarity=chosen.similarity,
            candidates=candidates,
            reason=f"Similarity {chosen.similarity:.2f} is below the {self.threshold:.2f} threshold. {match.reason}".strip(),
        )
