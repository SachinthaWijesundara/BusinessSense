"""Runtime settings, read from environment variables.

Locally these come from ``backend/.env`` or the repo-root ``.env`` (loaded in ``main.py``).
On Render they are set in the service's Environment tab. Secrets never reach the browser.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Single-shop prototype: every row belongs to this business until owner login is wired in.
DEFAULT_BUSINESS_ID = "00000000-0000-0000-0000-000000000001"

_DEFAULT_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
)


def _str(name: str, default: str | None = None) -> str | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


def _bool(name: str, default: bool) -> bool:
    raw = _str(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    raw = _str(name)
    return default if raw is None else float(raw)


def _int(name: str, default: int) -> int:
    raw = _str(name)
    return default if raw is None else int(raw)


def _origins(name: str) -> tuple[str, ...]:
    raw = _str(name)
    if raw is None:
        return _DEFAULT_ORIGINS
    return tuple(item.strip().rstrip("/") for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    # OpenAI (agents + embeddings)
    openai_api_key: str | None = None
    vision_model: str = "gpt-4o"
    sku_match_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    trace_sensitive_data: bool = False

    # Business rules
    sku_match_threshold: float = 0.82
    sku_candidate_count: int = 5
    math_tolerance: float = 1.0

    # Supabase Postgres (direct connection string) and Storage
    database_url: str | None = None
    require_database: bool = False
    db_pool_max_size: int = 5
    supabase_url: str | None = None
    supabase_service_role_key: str | None = None
    invoice_bucket: str = "invoice-images"
    store_invoice_images: bool = True

    # HTTP
    allowed_origins: tuple[str, ...] = _DEFAULT_ORIGINS
    allowed_origin_regex: str | None = None
    max_upload_mb: float = 10.0
    extract_region_previews: bool = True
    debug_previews_dir: str | None = None

    # Tenancy (replace with the logged-in owner's business once auth lands)
    default_business_id: str = DEFAULT_BUSINESS_ID

    @property
    def repository_kind(self) -> str:
        return "postgres" if self.database_url else "memory"

    @property
    def storage_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb * 1024 * 1024)

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            openai_api_key=_str("OPENAI_API_KEY"),
            # OPENAI_MODEL is the variable the first prototype used; keep honouring it.
            vision_model=_str("INVOICE_VISION_MODEL") or _str("OPENAI_MODEL") or "gpt-4o",
            sku_match_model=_str("SKU_MATCH_MODEL", "gpt-4o-mini") or "gpt-4o-mini",
            embedding_model=_str("EMBEDDING_MODEL", "text-embedding-3-small")
            or "text-embedding-3-small",
            embedding_dimensions=_int("EMBEDDING_DIMENSIONS", 1536),
            trace_sensitive_data=_bool("AGENTS_TRACE_SENSITIVE_DATA", False),
            sku_match_threshold=_float("SKU_MATCH_THRESHOLD", 0.82),
            sku_candidate_count=_int("SKU_CANDIDATE_COUNT", 5),
            math_tolerance=_float("MATH_AUDIT_TOLERANCE", 1.0),
            database_url=_str("DATABASE_URL"),
            require_database=_bool("REQUIRE_DATABASE", False),
            db_pool_max_size=_int("DB_POOL_MAX_SIZE", 5),
            supabase_url=(_str("SUPABASE_URL") or "").rstrip("/") or None,
            supabase_service_role_key=_str("SUPABASE_SERVICE_ROLE_KEY"),
            invoice_bucket=_str("SUPABASE_INVOICE_BUCKET", "invoice-images") or "invoice-images",
            store_invoice_images=_bool("STORE_INVOICE_IMAGES", True),
            allowed_origins=_origins("ALLOWED_ORIGINS"),
            allowed_origin_regex=_str("ALLOWED_ORIGIN_REGEX"),
            max_upload_mb=_float("MAX_UPLOAD_MB", 10.0),
            extract_region_previews=_bool("EXTRACT_REGION_PREVIEWS", True),
            debug_previews_dir=_str("DEBUG_PREVIEWS_DIR"),
            default_business_id=_str("DEFAULT_BUSINESS_ID", DEFAULT_BUSINESS_ID)
            or DEFAULT_BUSINESS_ID,
        )
