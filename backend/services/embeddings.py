"""Text embeddings for product matching.

``OpenAIEmbedder`` uses text-embedding-3-small (the proposal's choice). ``HashingEmbedder`` is a
deterministic, offline stand-in for tests and local runs without an API key: character trigrams
hashed into a fixed-size vector. It catches spelling variants but understands no meaning.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Protocol

from openai import AsyncOpenAI

from .text import normalize_text


class Embedder(Protocol):
    dimensions: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    def __init__(self, *, model: str, dimensions: int, api_key: str | None) -> None:
        self.model = model
        self.dimensions = dimensions
        self._api_key = api_key
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        if self._client is None:
            self._client = AsyncOpenAI(api_key=self._api_key)
        return self._client

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        response = await self._get_client().embeddings.create(
            model=self.model, input=list(texts), dimensions=self.dimensions
        )
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()


class HashingEmbedder:
    def __init__(self, dimensions: int = 1536) -> None:
        self.dimensions = dimensions

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        padded = f"  {normalize_text(text)}  "
        for start in range(len(padded) - 2):
            gram = padded[start : start + 3].encode("utf-8")
            digest = hashlib.blake2b(gram, digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
