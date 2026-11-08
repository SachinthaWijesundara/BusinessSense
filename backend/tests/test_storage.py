from __future__ import annotations

import json

import httpx
import pytest

from config import Settings
from database.client import build_image_store
from database.storage import DisabledImageStore, MemoryImageStore, StorageError, SupabaseImageStore

pytestmark = pytest.mark.anyio
URL = "https://abc.supabase.co"


def store_with(handler, key: str = "eyJhbGciOi.fake.jwt") -> SupabaseImageStore:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SupabaseImageStore(url=URL, service_key=key, bucket="invoice-images", client=client)


async def test_upload_request():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"Key": "invoice-images/biz/inv/original.jpg"})

    await store_with(handler).put("biz/inv/original.jpg", b"jpeg-bytes", "image/jpeg")
    [request] = seen
    assert request.method == "POST"
    assert str(request.url) == f"{URL}/storage/v1/object/invoice-images/biz/inv/original.jpg"
    assert request.headers["authorization"] == "Bearer eyJhbGciOi.fake.jwt"
    assert request.headers["apikey"] == "eyJhbGciOi.fake.jwt"
    assert request.headers["x-upsert"] == "true"
    assert request.headers["content-type"] == "image/jpeg"
    assert request.content == b"jpeg-bytes"


async def test_new_secret_keys_are_not_sent_as_bearer():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    await store_with(handler, key="sb_secret_abc").put("a.png", b"x", "image/png")
    assert "authorization" not in seen[0].headers
    assert seen[0].headers["apikey"] == "sb_secret_abc"


async def test_upload_failure_raises():
    store = store_with(lambda request: httpx.Response(400, json={"error": "Bucket not found"}))
    with pytest.raises(StorageError, match="Bucket not found"):
        await store.put("a.png", b"x", "image/png")


async def test_signed_url():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{URL}/storage/v1/object/sign/invoice-images/biz/inv/clahe.png"
        assert json.loads(request.content) == {"expiresIn": 600}
        return httpx.Response(200, json={"signedURL": "/object/sign/invoice-images/biz/inv/clahe.png?token=t"})

    url = await store_with(handler).signed_url("biz/inv/clahe.png", expires_in=600)
    assert url == f"{URL}/storage/v1/object/sign/invoice-images/biz/inv/clahe.png?token=t"


async def test_signed_url_failure_returns_none():
    assert await store_with(lambda request: httpx.Response(404, json={})).signed_url("missing.png") is None


def test_store_selection():
    assert isinstance(build_image_store(Settings()), MemoryImageStore)
    assert isinstance(build_image_store(Settings(store_invoice_images=False)), DisabledImageStore)
    assert isinstance(build_image_store(Settings(database_url="postgresql://x")), DisabledImageStore)
    configured = Settings(supabase_url=URL, supabase_service_role_key="sb_secret_x")
    assert isinstance(build_image_store(configured), SupabaseImageStore)
