"""Wires settings, storage, agents and embeddings into one object the API shares."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agents import set_default_openai_key
from agents.models.interface import Model

from agentic.sku_match_agent import SkuMatcher
from agentic.vision_agent import InvoiceVisionReader
from config import Settings
from database.client import build_image_store, build_repository
from database.repository import Repository
from database.storage import ImageStore

from .embeddings import Embedder, HashingEmbedder, OpenAIEmbedder

logger = logging.getLogger(__name__)


@dataclass
class AppServices:
    settings: Settings
    repo: Repository
    images: ImageStore
    embedder: Embedder
    vision: InvoiceVisionReader
    sku_matcher: SkuMatcher

    async def aclose(self) -> None:
        await self.repo.close()
        await self.images.aclose()
        close = getattr(self.embedder, "aclose", None)
        if close is not None:
            await close()


def _default_embedder(settings: Settings) -> Embedder:
    if settings.openai_api_key or settings.database_url:
        # With a real database, never fall back to the offline embedder: its vectors would be
        # mixed with OpenAI vectors in product_aliases. Without a key this fails loudly instead.
        return OpenAIEmbedder(
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            api_key=settings.openai_api_key,
        )
    logger.warning("OPENAI_API_KEY is not set: using the offline hashing embedder")
    return HashingEmbedder(settings.embedding_dimensions)


async def build_services(
    settings: Settings,
    *,
    repo: Repository | None = None,
    images: ImageStore | None = None,
    embedder: Embedder | None = None,
    vision_model: str | Model | None = None,
    sku_model: str | Model | None = None,
) -> AppServices:
    """Tests pass scripted models, an in-memory repo and an offline embedder here."""
    if settings.openai_api_key:
        set_default_openai_key(settings.openai_api_key)
    key_configured = bool(settings.openai_api_key)
    repo = repo or await build_repository(settings)
    images = images or build_image_store(settings)
    embedder = embedder or _default_embedder(settings)
    vision = InvoiceVisionReader(
        vision_model or settings.vision_model,
        api_key_configured=key_configured,
        trace_sensitive_data=settings.trace_sensitive_data,
    )
    sku_matcher = SkuMatcher(
        model=sku_model or settings.sku_match_model,
        repo=repo,
        embedder=embedder,
        threshold=settings.sku_match_threshold,
        candidate_limit=settings.sku_candidate_count,
        api_key_configured=key_configured,
        trace_sensitive_data=settings.trace_sensitive_data,
    )
    return AppServices(
        settings=settings,
        repo=repo,
        images=images,
        embedder=embedder,
        vision=vision,
        sku_matcher=sku_matcher,
    )
