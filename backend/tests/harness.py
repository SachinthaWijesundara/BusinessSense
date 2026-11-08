"""A running app (TestClient) wired to scripted agents, an offline embedder and memory storage."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from config import Settings
from database.repository import InMemoryRepository, Repository
from database.storage import MemoryImageStore
from main import create_app
from services.container import AppServices, build_services
from services.embeddings import HashingEmbedder
from tests.helpers import SUPPLIER, invoice_image, sku_model, vision_model, vision_step

JPEG = ("invoice.jpg", invoice_image(), "image/jpeg")
# The offline hashing embedder scores spelling variants lower than OpenAI embeddings would,
# so the API tests use a lower threshold; the 0.82 default is covered in test_sku_matching.
BASE_SETTINGS = Settings(sku_match_threshold=0.5)


class Harness:
    def __init__(self, client: TestClient, services: AppServices, vision: Any) -> None:
        self.client = client
        self.services = services
        self.vision = vision
        self.pg_url: str | None = None

    def upload(self, doc: dict[str, Any] | Exception, **form: str):
        self.vision.enqueue(vision_step(doc))
        return self.client.post("/invoices/upload", files={"file": JPEG}, data=form)

    def supplier(self, name: str = SUPPLIER, credit_days: int | None = 30) -> dict:
        response = self.client.post("/suppliers", json={"name": name, "credit_days": credit_days})
        assert response.status_code == 201, response.text
        return response.json()

    def product(self, name: str, **extra: Any) -> dict:
        response = self.client.post("/products", json={"name": name, **extra})
        assert response.status_code == 201, response.text
        return response.json()


@pytest.fixture
def make_harness() -> Iterator[Callable[..., Harness]]:
    opened: list[TestClient] = []

    def make(
        *, settings: Settings = BASE_SETTINGS, sku_pick: str = "top", repo: Repository | None = None
    ) -> Harness:
        vision = vision_model()
        holder: dict[str, AppServices] = {}

        async def factory(s: Settings) -> AppServices:
            holder["services"] = await build_services(
                s,
                repo=repo or InMemoryRepository(),
                images=MemoryImageStore(),
                embedder=HashingEmbedder(),
                vision_model=vision,
                sku_model=sku_model(pick=sku_pick),
            )
            return holder["services"]

        client = TestClient(create_app(settings, factory))
        client.__enter__()
        opened.append(client)
        return Harness(client, holder["services"], vision)

    yield make
    for client in opened:
        client.__exit__(None, None, None)
