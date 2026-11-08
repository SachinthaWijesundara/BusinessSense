"""Function tools the agents may call."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from agents import RunContextWrapper, function_tool

from database.repository import Repository
from services.alias_service import Candidate, find_candidates
from services.embeddings import Embedder
from services.text import normalize_text


@dataclass
class SkuMatchContext:
    """Run context for SkuMatchAgent.

    ``business_id`` comes from the authenticated request, never from the model, so a tool call
    can only ever search this shop's own catalogue. ``searches`` records what the tool returned
    so the 0.82 gate in code checks the tool's similarity score, not a number the model reports.
    """

    repo: Repository
    embedder: Embedder
    business_id: str
    candidate_limit: int = 5
    searches: dict[str, list[Candidate]] = field(default_factory=dict)


@function_tool
async def find_sku_candidates(ctx: RunContextWrapper[SkuMatchContext], raw_text: str) -> str:
    """Search this shop's product catalogue for master products matching one printed item description.

    Args:
        raw_text: The item description exactly as printed on the invoice line.
    """
    run = ctx.context
    candidates = await find_candidates(
        run.repo, run.embedder, run.business_id, raw_text, limit=run.candidate_limit
    )
    run.searches[normalize_text(raw_text)] = candidates
    return json.dumps(
        {"raw_text": raw_text, "candidates": [candidate.to_dict() for candidate in candidates]}
    )
