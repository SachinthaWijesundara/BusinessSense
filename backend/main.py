"""BusinessSense API: the FastAPI backend on Render (proposal section 7.1).

This file holds, in order:
  1. Settings      every setting, read from environment variables (backend/.env locally, the
                   Render dashboard in production). Secrets never reach the browser.
  2. AppServices   the database, photo storage, owner login, embedder and both agents, built once
                   at startup by ``build_services`` (tests pass fakes for the OpenAI parts).
  3. The app       CORS for the frontend's website, JSON errors for refused requests, /health,
                   the login routes, and the data routes in api/ (each one needs a valid login).

Run locally:  uvicorn main:app --reload --port 8765
On Render:    uvicorn main:app --host 0.0.0.0 --port $PORT   (see render.yaml)
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents import set_default_openai_key
from agents.models.interface import Model
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agentic.sku_match_agent import SkuMatcher
from agentic.vision_agent import InvoiceVisionReader
from api.auth import require_owner
from api.auth import router as auth_router
from api.analytics import router as analytics_router
from api.dashboard import router as dashboard_router
from api.invoices import router as invoices_router
from api.suppliers import payments_router
from api.suppliers import router as suppliers_router
from database.client import Database, RepositoryError, SupabaseAuth, SupabaseStorage
from ml.inference import ItemPriceModel
from ml.spend import SpendForecastModel
from services.alias_service import Embedder, OpenAIEmbedder
from services.invoice_service import WorkflowError

# Local development only: values already set in the environment (Render) always win.
load_dotenv(Path(__file__).resolve().parent / ".env")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# =============================================================================================
# 1. Settings
# =============================================================================================

# Used only when AUTH_REQUIRED=false (local tests); with login, the business comes from the login.
DEFAULT_BUSINESS_ID = "00000000-0000-0000-0000-000000000001"
_DEFAULT_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:3001", "http://127.0.0.1:3001")


def _str(name: str, default: str | None = None) -> str | None:
    """Read a text setting; empty values count as not set."""
    raw = os.getenv(name)
    return default if raw is None or not raw.strip() else raw.strip()


def _bool(name: str, default: bool) -> bool:
    """Read a true/false setting (1, true, yes, on mean true)."""
    raw = _str(name)
    return default if raw is None else raw.lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    """Read a decimal-number setting."""
    raw = _str(name)
    return default if raw is None else float(raw)


def _int(name: str, default: int) -> int:
    """Read a whole-number setting."""
    raw = _str(name)
    return default if raw is None else int(raw)


def _origins(name: str) -> tuple[str, ...]:
    """Read the comma-separated list of websites allowed to call the API (CORS)."""
    raw = _str(name)
    if raw is None:
        return _DEFAULT_ORIGINS
    return tuple(item.strip().rstrip("/") for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    """All configuration of the API. Defaults follow the proposal."""

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
    # Supabase: Postgres connection string and Storage for invoice photos
    database_url: str | None = None
    db_pool_max_size: int = 5
    supabase_url: str | None = None
    supabase_service_role_key: str | None = None
    invoice_bucket: str = "invoice-images"
    # HTTP
    allowed_origins: tuple[str, ...] = _DEFAULT_ORIGINS
    allowed_origin_regex: str | None = None
    max_upload_mb: float = 10.0
    # Owner login (Supabase Auth). Every data route needs a valid login unless this is false.
    auth_required: bool = True
    default_business_id: str = DEFAULT_BUSINESS_ID

    @property
    def storage_configured(self) -> bool:
        """True when Supabase Storage credentials are present, so photos can be kept."""
        return bool(self.supabase_url and self.supabase_service_role_key)

    @property
    def max_upload_bytes(self) -> int:
        """The upload size limit in bytes."""
        return int(self.max_upload_mb * 1024 * 1024)

    @classmethod
    def from_env(cls) -> Settings:
        """Build the settings from environment variables."""
        return cls(
            openai_api_key=_str("OPENAI_API_KEY"),
            vision_model=_str("INVOICE_VISION_MODEL") or "gpt-4o",
            sku_match_model=_str("SKU_MATCH_MODEL") or "gpt-4o-mini",
            embedding_model=_str("EMBEDDING_MODEL") or "text-embedding-3-small",
            embedding_dimensions=_int("EMBEDDING_DIMENSIONS", 1536),
            trace_sensitive_data=_bool("AGENTS_TRACE_SENSITIVE_DATA", False),
            sku_match_threshold=_float("SKU_MATCH_THRESHOLD", 0.82),
            sku_candidate_count=_int("SKU_CANDIDATE_COUNT", 5),
            math_tolerance=_float("MATH_AUDIT_TOLERANCE", 1.0),
            database_url=_str("DATABASE_URL"),
            db_pool_max_size=_int("DB_POOL_MAX_SIZE", 5),
            supabase_url=(_str("SUPABASE_URL") or "").rstrip("/") or None,
            supabase_service_role_key=_str("SUPABASE_SERVICE_ROLE_KEY"),
            invoice_bucket=_str("SUPABASE_INVOICE_BUCKET") or "invoice-images",
            allowed_origins=_origins("ALLOWED_ORIGINS"),
            allowed_origin_regex=_str("ALLOWED_ORIGIN_REGEX"),
            max_upload_mb=_float("MAX_UPLOAD_MB", 10.0),
            auth_required=_bool("AUTH_REQUIRED", True),
            default_business_id=_str("DEFAULT_BUSINESS_ID") or DEFAULT_BUSINESS_ID,
        )


# =============================================================================================
# 2. Shared services
# =============================================================================================


@dataclass
class AppServices:
    """Everything a request needs, created once when the API starts.

    Routes read it from ``request.app.state.services``.
    """

    settings: Settings
    db: Database
    storage: SupabaseStorage | None  # None: photos are not kept; tests pass an in-memory stand-in
    auth: SupabaseAuth | None  # owner login; tests pass a stand-in
    embedder: Embedder
    vision: InvoiceVisionReader
    sku_matcher: SkuMatcher
    models_storage: Any = None  # the shop's private "models" bucket (trained Model 1); None: not configured
    _price_models: dict[str, ItemPriceModel | None] = field(default_factory=dict)
    _spend_models: dict[str, SpendForecastModel | None] = field(default_factory=dict)

    def business_id_for(self, request: Request) -> str:
        """The business a request acts for: the one the logged-in owner belongs to.

        ``api.auth.require_owner`` (run before every data route) checks the login token and stores
        the business on the request. Every query takes it as input, so a login only ever reads its
        own shop's rows.
        """
        business_id = getattr(request.state, "business_id", None)
        if business_id:
            return business_id
        if self.settings.auth_required:
            raise WorkflowError(401, "not_logged_in", "Log in to see this.")
        return self.settings.default_business_id

    async def price_model(self, business_id: str) -> ItemPriceModel | None:
        """The shop's trained Model 1, loaded from its "models" bucket on first use and kept in memory.

        None until ml/train.py has been run for the shop; the screens then show baselines. After
        retraining, restart the API to load the new model.
        """
        if business_id not in self._price_models:
            if self.models_storage is None:
                return None
            try:
                self._price_models[business_id] = await ItemPriceModel.from_storage(self.models_storage, business_id)
            except Exception:  # noqa: BLE001 - without its model the shop still sees baselines
                logger.exception("could not load Model 1")
                return None
        return self._price_models[business_id]

    async def spend_model(self, business_id: str) -> SpendForecastModel | None:
        """The shop's trained Model 2 (purchase spend), loaded like Model 1 on first use and kept in
        memory; None until ml/train.py has been run for the shop (the planner then shows baselines)."""
        if business_id not in self._spend_models:
            if self.models_storage is None:
                return None
            try:
                self._spend_models[business_id] = await SpendForecastModel.from_storage(self.models_storage, business_id)
            except Exception:  # noqa: BLE001 - without its model the shop still sees baselines
                logger.exception("could not load Model 2")
                return None
        return self._spend_models[business_id]

    async def aclose(self) -> None:
        """Close the database pool and HTTP clients when the API stops."""
        await self.db.close()
        if self.storage is not None:
            await self.storage.aclose()
        if self.models_storage is not None:
            await self.models_storage.aclose()
        if self.auth is not None:
            await self.auth.aclose()
        close = getattr(self.embedder, "aclose", None)
        if close is not None:
            await close()


async def build_services(
    settings: Settings,
    *,
    db: Database | None = None,
    storage: Any = None,
    auth: Any = None,
    models_storage: Any = None,
    embedder: Embedder | None = None,
    vision_model: str | Model | None = None,
    sku_model: str | Model | None = None,
) -> AppServices:
    """Connect to Supabase and create both agents. Tests pass a database, fake storage and login,
    an offline embedder and scripted models instead of the real OpenAI ones."""
    if settings.openai_api_key:
        set_default_openai_key(settings.openai_api_key)
    if db is None:
        if not settings.database_url:
            raise RuntimeError(
                "DATABASE_URL is not set: use the Supabase Postgres connection string "
                "(Supabase > Connect > Transaction pooler)."
            )
        db = Database(settings.database_url, max_size=settings.db_pool_max_size)
        await db.open()
    if storage is None and settings.storage_configured:
        assert settings.supabase_url and settings.supabase_service_role_key
        storage = SupabaseStorage(url=settings.supabase_url, service_key=settings.supabase_service_role_key,
                                  bucket=settings.invoice_bucket)
    if models_storage is None and settings.storage_configured:
        assert settings.supabase_url and settings.supabase_service_role_key
        models_storage = SupabaseStorage(url=settings.supabase_url, service_key=settings.supabase_service_role_key,
                                         bucket="models")
    if auth is None and settings.storage_configured:
        assert settings.supabase_url and settings.supabase_service_role_key
        auth = SupabaseAuth(url=settings.supabase_url, api_key=settings.supabase_service_role_key)
    if auth is None and settings.auth_required:
        raise RuntimeError(
            "Owner login needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (the shop's Supabase project). "
            "Set them, or AUTH_REQUIRED=false for local tests only."
        )
    if embedder is None:
        embedder = OpenAIEmbedder(model=settings.embedding_model, dimensions=settings.embedding_dimensions,
                                  api_key=settings.openai_api_key)
    key_configured = bool(settings.openai_api_key)
    vision = InvoiceVisionReader(vision_model or settings.vision_model, api_key_configured=key_configured,
                                 trace_sensitive_data=settings.trace_sensitive_data)
    sku_matcher = SkuMatcher(
        model=sku_model or settings.sku_match_model, db=db, embedder=embedder,
        threshold=settings.sku_match_threshold, candidate_limit=settings.sku_candidate_count,
        api_key_configured=key_configured, trace_sensitive_data=settings.trace_sensitive_data,
    )
    return AppServices(settings=settings, db=db, storage=storage, auth=auth, embedder=embedder, vision=vision,
                       sku_matcher=sku_matcher, models_storage=models_storage)


# =============================================================================================
# 3. The app
# =============================================================================================

ServicesFactory = Callable[[Settings], Awaitable[AppServices]]


def create_app(settings: Settings | None = None, services_factory: ServicesFactory | None = None) -> FastAPI:
    """Build the FastAPI app. Tests pass their own settings and a factory with fake OpenAI parts."""
    settings = settings or Settings.from_env()
    factory = services_factory or build_services

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Create the shared services at startup and close them at shutdown."""
        services = await factory(settings)
        app.state.services = services
        try:
            yield
        finally:
            await services.aclose()

    app = FastAPI(title="BusinessSense API", version="0.3.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_origin_regex=settings.allowed_origin_regex,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        expose_headers=["X-Total-Count"],  # the invoice list's total, for its page buttons
    )

    @app.exception_handler(RepositoryError)
    async def repository_error(_: Request, exc: RepositoryError) -> JSONResponse:
        """A value the database refused (e.g. a number too big for its column): a 422, not a crash."""
        return JSONResponse(status_code=422, content={
            "error_type": "invalid_value", "message": "A value is out of range. Check the numbers and try again."})

    @app.exception_handler(WorkflowError)
    async def workflow_error(_: Request, exc: WorkflowError) -> JSONResponse:
        """Send a refused request back as {"error_type", "message", ...} with its status code."""
        return JSONResponse(status_code=exc.status_code, content=exc.body())

    @app.get("/health", tags=["health"])
    async def health(request: Request) -> dict:
        """Liveness check: is the API up, and which parts are configured."""
        services: AppServices = request.app.state.services
        return {"ok": True, "database": "postgres", "openai_configured": bool(settings.openai_api_key),
                "image_storage": services.storage is not None, "login_required": settings.auth_required}

    app.include_router(auth_router)
    login = [Depends(require_owner)]  # every data route needs a valid login
    app.include_router(dashboard_router, dependencies=login)
    app.include_router(invoices_router, dependencies=login)
    app.include_router(suppliers_router, dependencies=login)
    app.include_router(payments_router, dependencies=login)
    app.include_router(analytics_router, dependencies=login)
    return app


app = create_app()
