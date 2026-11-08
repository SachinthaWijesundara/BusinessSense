"""Build the repository and image store from settings."""

from __future__ import annotations

import logging

from config import Settings

from .postgres import PostgresRepository
from .repository import InMemoryRepository, Repository
from .storage import DisabledImageStore, ImageStore, MemoryImageStore, SupabaseImageStore

logger = logging.getLogger(__name__)


async def build_repository(settings: Settings) -> Repository:
    if settings.database_url:
        repo = PostgresRepository(settings.database_url, max_size=settings.db_pool_max_size)
        await repo.open()
        return repo
    if settings.require_database:
        raise RuntimeError("DATABASE_URL is not set but REQUIRE_DATABASE is true")
    logger.warning("DATABASE_URL is not set: using the in-memory repository (data is not saved)")
    return InMemoryRepository()


def build_image_store(settings: Settings) -> ImageStore:
    if not settings.store_invoice_images:
        return DisabledImageStore()
    if settings.storage_configured:
        assert settings.supabase_url and settings.supabase_service_role_key
        return SupabaseImageStore(
            url=settings.supabase_url,
            service_key=settings.supabase_service_role_key,
            bucket=settings.invoice_bucket,
        )
    if settings.database_url:
        logger.warning("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set: invoice images are not stored")
        return DisabledImageStore()
    return MemoryImageStore()
