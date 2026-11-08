"""BusinessSense API (FastAPI on Render).

Local:   uvicorn main:app --reload --port 8000
Render:  uvicorn main:app --host 0.0.0.0 --port $PORT   (see render.yaml)
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

_BACKEND_DIR = Path(__file__).resolve().parent
# Local development only: real environment variables (Render) always win.
load_dotenv(_BACKEND_DIR / ".env")
load_dotenv(_BACKEND_DIR.parent / ".env")

from api.catalogue import router as catalogue_router  # noqa: E402
from api.extract import router as extract_router  # noqa: E402
from api.invoices import router as invoices_router  # noqa: E402
from config import Settings  # noqa: E402
from services.container import AppServices, build_services  # noqa: E402
from services.errors import WorkflowError  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

ServicesFactory = Callable[[Settings], Awaitable[AppServices]]


def create_app(
    settings: Settings | None = None, services_factory: ServicesFactory | None = None
) -> FastAPI:
    settings = settings or Settings.from_env()
    factory = services_factory or build_services

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        services = await factory(settings)
        app.state.services = services
        try:
            yield
        finally:
            await services.aclose()

    app = FastAPI(title="BusinessSense API", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_origin_regex=settings.allowed_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(WorkflowError)
    async def _workflow_error(_: Request, exc: WorkflowError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.body())

    @app.get("/health", tags=["health"])
    async def health(request: Request) -> dict:
        services: AppServices = request.app.state.services
        return {
            "ok": True,
            "repository": services.repo.kind,
            "openai_configured": bool(settings.openai_api_key),
            "image_storage": services.images.enabled,
        }

    app.include_router(extract_router)
    app.include_router(invoices_router)
    app.include_router(catalogue_router)
    return app


app = create_app()
