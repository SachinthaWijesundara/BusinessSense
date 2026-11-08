from __future__ import annotations

from fastapi import Depends, Request, UploadFile

from config import Settings
from services.container import AppServices
from services.errors import WorkflowError

_ACCEPTED_TYPES = ("image/", "application/octet-stream")


def get_services(request: Request) -> AppServices:
    return request.app.state.services


def get_business_id(services: AppServices = Depends(get_services)) -> str:
    """The business every request acts for.

    Single-shop prototype: the configured DEFAULT_BUSINESS_ID. When Supabase Auth is added, resolve
    it from the owner's access token here; the agents and repositories already take it as input.
    """
    return services.settings.default_business_id


async def read_upload(file: UploadFile, settings: Settings) -> bytes:
    content_type = (file.content_type or "").lower()
    if content_type and not content_type.startswith(_ACCEPTED_TYPES):
        raise WorkflowError(415, "unsupported_media_type", "Upload a photo (JPEG, PNG or WebP).")
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise WorkflowError(
            413, "file_too_large", f"The photo is larger than {settings.max_upload_mb:g} MB."
        )
    return data
