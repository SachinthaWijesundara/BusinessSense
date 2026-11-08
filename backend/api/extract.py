"""POST /extract - read a photo into a draft without saving anything.

Kept with the exact response shape of the first prototype because the current Invoice Manager
screen calls it. New clients should use POST /invoices/upload, which also audits, matches and
saves the draft.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import JSONResponse

from agentic.pipeline import log_read, read_document
from services.container import AppServices

from .deps import get_business_id, get_services, read_upload

router = APIRouter(tags=["extract"])


@router.post("/extract")
async def extract(
    file: UploadFile = File(...),
    services: AppServices = Depends(get_services),
    business_id: str = Depends(get_business_id),
) -> Any:
    data = await read_upload(file, services.settings)
    outcome = await read_document(data, services)
    await log_read(services, business_id, outcome)

    if not outcome.ok:
        body: dict[str, Any] = {
            "error_type": outcome.error_type,
            "message": outcome.message,
            "missing_fields": outcome.missing,
        }
        if outcome.preview_png_base64:
            body["preview_png_base64"] = outcome.preview_png_base64
        if outcome.region_previews_base64:
            body["region_previews"] = outcome.region_previews_base64
        status = 422 if outcome.error_type == "reading_unsuccessful" else 502
        return JSONResponse(status_code=status, content=body)

    assert outcome.document is not None
    return {
        "document": outcome.document.model_dump(by_alias=True),
        "missing_fields": outcome.missing,
        "partial": outcome.partial,
        "message": outcome.message,
        "preview_png_base64": outcome.preview_png_base64,
        "region_previews": outcome.region_previews_base64,
    }
