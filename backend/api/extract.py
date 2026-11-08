from __future__ import annotations

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse

from agentic.pipeline import extract_document

router = APIRouter()


@router.post("/extract")
async def extract(file: UploadFile = File(...)):
    data = await file.read()
    result = extract_document(
        data,
        filename=file.filename,
        content_type=file.content_type,
    )
    if not result.ok:
        status = 422 if result.error_type == "reading_unsuccessful" else 502
        body: dict = {
            "error_type": result.error_type,
            "message": result.message,
            "missing_fields": result.missing or [],
        }
        if result.preview_png_base64:
            body["preview_png_base64"] = result.preview_png_base64
        if result.region_previews:
            body["region_previews"] = result.region_previews
        return JSONResponse(status_code=status, content=body)
    assert result.document is not None
    return {
        "document": result.document.model_dump(by_alias=True),
        "missing_fields": result.missing or [],
        "partial": result.partial,
        "message": result.message,
        "preview_png_base64": result.preview_png_base64,
        "region_previews": result.region_previews,
    }
