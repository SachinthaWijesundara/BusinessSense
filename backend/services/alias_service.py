"""Product catalogue search behind SkuMatchAgent's ``find_sku_candidates`` tool.

Every product has at least one alias (its own name, source "catalogue"); each owner confirmation
adds another ("63mm upvc pipe" -> "uPVC Pipe 63mm"). A printed description is looked up by exact
normalised text first, then by meaning: text-embedding-3-small vectors compared in pgvector.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Protocol

from openai import AsyncOpenAI

if TYPE_CHECKING:
    from database.client import Database

_NON_ALNUM = re.compile(r"[^a-z0-9.]+")


def normalize_text(value: str | None) -> str:
    """Tidy a printed description for exact matching: ``"63MM  uPVC-Pipe"`` -> ``"63mm upvc pipe"``.

    Lower-case, keep letters, digits and dots (sizes like "1.5mm"), everything else becomes one space.
    """
    if not value:
        return ""
    return _NON_ALNUM.sub(" ", value.lower()).strip()


class Embedder(Protocol):
    """Anything that turns texts into vectors (OpenAI in production, an offline fake in tests)."""

    dimensions: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one vector per text."""
        ...


class OpenAIEmbedder:
    """Embeddings from OpenAI (text-embedding-3-small, as in the proposal)."""

    def __init__(self, *, model: str, dimensions: int, api_key: str | None) -> None:
        """Remember the model; the OpenAI client is created on first use."""
        self.model = model
        self.dimensions = dimensions
        self._api_key = api_key
        self._client: AsyncOpenAI | None = None

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one vector per text, in the same order."""
        if not texts:
            return []
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        if self._client is None:
            self._client = AsyncOpenAI(api_key=self._api_key)
        response = await self._client.embeddings.create(
            model=self.model, input=list(texts), dimensions=self.dimensions
        )
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]

    async def aclose(self) -> None:
        """Close the OpenAI client."""
        if self._client is not None:
            await self._client.close()


@dataclass(frozen=True)
class Candidate:
    """A product that may be the one behind a printed description."""

    product_id: str
    product_name: str
    product_sku: str | None
    alias_text: str
    similarity: float

    def to_dict(self) -> dict:
        """The candidate as JSON, with the similarity rounded."""
        data = asdict(self)
        data["similarity"] = round(self.similarity, 4)
        return data


async def prepare_catalogue(db: Database, embedder: Embedder, business_id: str) -> int:
    """Give every product a catalogue alias and every alias an embedding.

    Products can be added outside the backend (the data-entry app, SQL imports), so this runs
    before each matching pass. Returns how many aliases were added or embedded.
    """
    changed = 0
    products = await db.products_without_aliases(business_id)
    if products:
        vectors = await embedder.embed([product.name for product in products])
        for product, vector in zip(products, vectors, strict=True):
            await db.add_alias(
                business_id,
                product_id=product.id,
                alias_text=product.name,
                normalized_text=normalize_text(product.name),
                embedding=vector,
                source="catalogue",
                confirmed=True,
            )
            changed += 1
    pending = await db.aliases_without_embeddings(business_id)
    if pending:
        vectors = await embedder.embed([alias.alias_text for alias in pending])
        for alias, vector in zip(pending, vectors, strict=True):
            await db.set_alias_embedding(business_id, alias.id, vector)
            changed += 1
    return changed


def _best_per_product(candidates: list[Candidate]) -> list[Candidate]:
    """Keep the best-scoring alias of each product, best first."""
    best: dict[str, Candidate] = {}
    for candidate in candidates:
        current = best.get(candidate.product_id)
        if current is None or candidate.similarity > current.similarity:
            best[candidate.product_id] = candidate
    return sorted(best.values(), key=lambda c: c.similarity, reverse=True)


async def exact_candidates(db: Database, business_id: str, raw_text: str) -> list[Candidate]:
    """Products with a confirmed alias whose normalised text is exactly this one."""
    normalized = normalize_text(raw_text)
    if not normalized:
        return []
    matches = await db.find_aliases_exact(business_id, normalized)
    return _best_per_product(
        [Candidate(m.product_id, m.product_name, m.product_sku, m.alias_text, 1.0) for m in matches]
    )


async def find_candidates(
    db: Database, embedder: Embedder, business_id: str, raw_text: str, *, limit: int = 5
) -> list[Candidate]:
    """The products most likely behind a printed description: exact alias first, else by meaning."""
    exact = await exact_candidates(db, business_id, raw_text)
    if exact:
        return exact[:limit]
    if not normalize_text(raw_text):
        return []
    [vector] = await embedder.embed([raw_text])
    matches = await db.search_aliases(business_id, vector, limit * 3)
    candidates = [
        Candidate(m.product_id, m.product_name, m.product_sku, m.alias_text, float(m.similarity))
        for m in matches
    ]
    return _best_per_product(candidates)[:limit]


async def remember_alias(
    db: Database,
    embedder: Embedder,
    business_id: str,
    *,
    product_id: str,
    raw_text: str,
    source: str = "owner_confirmed",
) -> None:
    """Keep a confirmed printed description so the next invoice links it automatically."""
    normalized = normalize_text(raw_text)
    if not normalized:
        return
    [vector] = await embedder.embed([raw_text])
    await db.add_alias(
        business_id,
        product_id=product_id,
        alias_text=raw_text.strip(),
        normalized_text=normalized,
        embedding=vector,
        source=source,
        confirmed=True,
    )
