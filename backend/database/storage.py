"""Invoice image storage (Supabase Storage, private bucket, signed URLs)."""

from __future__ import annotations

from typing import Protocol
from urllib.parse import quote

import httpx


class StorageError(Exception):
    pass


class ImageStore(Protocol):
    enabled: bool

    async def put(self, path: str, data: bytes, content_type: str) -> None: ...
    async def signed_url(self, path: str, expires_in: int = 3600) -> str | None: ...
    async def aclose(self) -> None: ...


class DisabledImageStore:
    enabled = False

    async def put(self, path: str, data: bytes, content_type: str) -> None:
        return None

    async def signed_url(self, path: str, expires_in: int = 3600) -> str | None:
        return None

    async def aclose(self) -> None:
        return None


class MemoryImageStore:
    enabled = True

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put(self, path: str, data: bytes, content_type: str) -> None:
        self.objects[path] = (data, content_type)

    async def signed_url(self, path: str, expires_in: int = 3600) -> str | None:
        return f"memory://{path}" if path in self.objects else None

    async def aclose(self) -> None:
        return None


class SupabaseImageStore:
    """Talks to the Supabase Storage REST API with the service-role key (server-side only)."""

    enabled = True

    def __init__(
        self,
        *,
        url: str,
        service_key: str,
        bucket: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = url.rstrip("/") + "/storage/v1"
        self._bucket = bucket
        self._headers = {"apikey": service_key}
        # Legacy service_role keys are JWTs and go in Authorization; new sb_secret_ keys do not.
        if service_key.startswith("eyJ"):
            self._headers["Authorization"] = f"Bearer {service_key}"
        self._client = client or httpx.AsyncClient(timeout=30)

    def _object(self, path: str) -> str:
        return f"{self._bucket}/{quote(path, safe='/')}"

    async def put(self, path: str, data: bytes, content_type: str) -> None:
        response = await self._client.post(
            f"{self._base}/object/{self._object(path)}",
            content=data,
            headers={**self._headers, "Content-Type": content_type, "x-upsert": "true"},
        )
        if response.status_code >= 400:
            raise StorageError(f"upload failed ({response.status_code}): {response.text[:200]}")

    async def signed_url(self, path: str, expires_in: int = 3600) -> str | None:
        response = await self._client.post(
            f"{self._base}/object/sign/{self._object(path)}",
            json={"expiresIn": expires_in},
            headers=self._headers,
        )
        if response.status_code >= 400:
            return None
        payload = response.json()
        signed = payload.get("signedURL") or payload.get("signedUrl")
        return f"{self._base}{signed}" if signed else None

    async def aclose(self) -> None:
        await self._client.aclose()
