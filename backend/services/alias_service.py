"""Product catalogue search used by SkuMatchAgent's ``find_sku_candidates`` tool.

Every product has at least one alias (its own name, source "catalogue"); owner confirmations add
more aliases ("63mm upvc pipe" -> "PVC Pipe 63mm"). Search is exact on the normalised text first,
then cosine similarity over text-embedding-3-small vectors stored in pgvector.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from database.repository import Repository

from .embeddings import Embedder
from .text import normalize_text


@dataclass(frozen=True)
class Candidate:
    product_id: str
    product_name: str
    product_sku: str | None
    alias_text: str
    similarity: float

    def to_dict(self) -> dict:
        data = asdict(self)
        data["similarity"] = round(self.similarity, 4)
        return data


async def prepare_catalogue(repo: Repository, embedder: Embedder, business_id: str) -> int:
    """Give every product a catalogue alias and every alias an embedding.

    Products can be created outside the backend (the frontend's item list, SQL imports), so this
    runs before each matching pass. Returns how many aliases were added or embedded.
    """
    changed = 0
    products = await repo.products_without_aliases(business_id)
    if products:
        vectors = await embedder.embed([product.name for product in products])
        for product, vector in zip(products, vectors, strict=True):
            await repo.add_alias(
                business_id,
                product_id=product.id,
                alias_text=product.name,
                normalized_text=normalize_text(product.name),
                embedding=vector,
                source="catalogue",
                confirmed=True,
            )
            changed += 1
    pending = await repo.aliases_without_embeddings(business_id)
    if pending:
        vectors = await embedder.embed([alias.alias_text for alias in pending])
        for alias, vector in zip(pending, vectors, strict=True):
            await repo.set_alias_embedding(business_id, alias.id, vector)
            changed += 1
    return changed


def _best_per_product(candidates: list[Candidate]) -> list[Candidate]:
    best: dict[str, Candidate] = {}
    for candidate in candidates:
        current = best.get(candidate.product_id)
        if current is None or candidate.similarity > current.similarity:
            best[candidate.product_id] = candidate
    return sorted(best.values(), key=lambda c: c.similarity, reverse=True)


async def exact_candidates(repo: Repository, business_id: str, raw_text: str) -> list[Candidate]:
    normalized = normalize_text(raw_text)
    if not normalized:
        return []
    matches = await repo.find_aliases_exact(business_id, normalized)
    return _best_per_product(
        [
            Candidate(m.product_id, m.product_name, m.product_sku, m.alias_text, 1.0)
            for m in matches
        ]
    )


async def find_candidates(
    repo: Repository,
    embedder: Embedder,
    business_id: str,
    raw_text: str,
    *,
    limit: int = 5,
) -> list[Candidate]:
    exact = await exact_candidates(repo, business_id, raw_text)
    if exact:
        return exact[:limit]
    if not normalize_text(raw_text):
        return []
    [vector] = await embedder.embed([raw_text])
    matches = await repo.search_aliases(business_id, vector, limit * 3)
    candidates = [
        Candidate(m.product_id, m.product_name, m.product_sku, m.alias_text, float(m.similarity))
        for m in matches
    ]
    return _best_per_product(candidates)[:limit]


async def remember_alias(
    repo: Repository,
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
    await repo.add_alias(
        business_id,
        product_id=product_id,
        alias_text=raw_text.strip(),
        normalized_text=normalized,
        embedding=vector,
        source=source,
        confirmed=True,
    )
