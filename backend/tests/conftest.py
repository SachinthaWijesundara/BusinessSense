"""Shared test setup.

* A throwaway Postgres with pgvector (PGlite: Postgres compiled to WebAssembly, in tests/pglite),
  started once per run and rebuilt from the repo-root database/*.sql files before each test.
* Fakes for the paid or remote parts: scripted Agents SDK models instead of OpenAI, an offline
  hashing embedder, and an in-memory photo store.
* Builders for fictional invoices and synthetic invoice photos. Never put the participating
  shop's real names, invoices or photos here: the repository is public.
* ``make_harness``: the real API (TestClient) wired to all of the above.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import shutil
import socket
import subprocess
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import psycopg
import pytest

os.environ.setdefault("OPENAI_AGENTS_DISABLE_TRACING", "1")

from agents import set_tracing_disabled  # noqa: E402
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from database.client import Database  # noqa: E402
from main import AppServices, Settings, build_services, create_app  # noqa: E402
from services.alias_service import normalize_text  # noqa: E402

set_tracing_disabled(True)

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent
SQL_FILES = sorted((REPO_DIR / "database").glob("*.sql"))
PGLITE_DIR = BACKEND_DIR / "tests" / "pglite"
BIZ = "00000000-0000-0000-0000-000000000001"
SUPPLIER = "Lanka Pipe Traders"

# Stand-ins for what Supabase provides (auth.uid(), storage.buckets, API roles), so the SQL files
# run on plain Postgres exactly as they would on Supabase.
SUPABASE_STUBS = """
create schema auth;
create function auth.uid() returns uuid language sql stable as
  $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
create schema storage;
create table storage.buckets (id text primary key, name text not null, public boolean default false);
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated; end if;
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon; end if;
end $$;
"""


# ---- database ----------------------------------------------------------------------------------
@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on asyncio."""
    return "asyncio"


def _free_port() -> int:
    """A free local TCP port for the test database."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def pg_server_url() -> Iterator[str]:
    """Start the PGlite test database once (or use TEST_DATABASE_URL, which gets wiped)."""
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        if os.getenv("TEST_DATABASE_RESET_OK") != "1":
            pytest.skip("TEST_DATABASE_URL is wiped by the tests; set TEST_DATABASE_RESET_OK=1 to allow it")
        yield url
        return
    node = shutil.which("node")
    if not node or not (PGLITE_DIR / "node_modules").exists():
        pytest.skip("No test database: run `npm install` in backend/tests/pglite or set TEST_DATABASE_URL")
    port = _free_port()
    proc = subprocess.Popen([node, "server.mjs", str(port)], cwd=PGLITE_DIR,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.monotonic() + 60
    assert proc.stdout is not None
    while True:
        line = proc.stdout.readline()
        if "PGLITE_READY" in line:
            break
        if proc.poll() is not None or time.monotonic() > deadline:
            proc.kill()
            pytest.fail(f"PGlite test database did not start: {line}")
    try:
        yield f"postgresql://postgres:postgres@127.0.0.1:{port}/postgres?sslmode=disable"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def reset_database(url: str, *, sql_files: Sequence[Path] | None = None, stubs: bool = True) -> None:
    """Wipe the test database and rebuild it from the SQL files (with Supabase stand-ins)."""
    with psycopg.connect(url, autocommit=True) as conn:
        for schema in ("public", "extensions", "auth", "storage"):
            conn.execute(f"drop schema if exists {schema} cascade")
        conn.execute("create schema public")
        if stubs:
            conn.execute(SUPABASE_STUBS)
        for path in SQL_FILES if sql_files is None else sql_files:
            conn.execute(path.read_text(encoding="utf-8"))


def run_sql(url: str, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
    """Run one query directly against the test database (to check what the API saved)."""
    with psycopg.connect(url, autocommit=True) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchall() if cursor.description else []


@pytest.fixture
def pg_url(pg_server_url: str) -> str:
    """A freshly rebuilt test database."""
    reset_database(pg_server_url)
    return pg_server_url


@pytest.fixture
def db(pg_url: str) -> Iterator[Database]:
    """The backend's Database class connected to a fresh test database."""
    database = Database(pg_url, max_size=1)  # PGlite shares one session across connections
    asyncio.run(database.open())
    yield database
    asyncio.run(database.close())


# ---- fakes for OpenAI and Storage -------------------------------------------------------------
class BrokenEmbedder:
    """Stand-in for an embedding model the OpenAI key may not use: every call fails."""

    dimensions = 1536

    async def embed(self, texts):
        """Fail like a blocked model (403) would."""
        raise RuntimeError("embedding model not allowed for this key")


class HashingEmbedder:
    """Offline stand-in for text-embedding-3-small: character trigrams hashed into a vector.
    Catches spelling variants, understands no meaning; deterministic."""

    def __init__(self, dimensions: int = 1536) -> None:
        """Vectors have the same size as OpenAI's, to fit the vector(1536) column."""
        self.dimensions = dimensions

    def _vector(self, text: str) -> list[float]:
        """Hash each 3-letter piece of the normalised text into the vector, then normalise it."""
        vector = [0.0] * self.dimensions
        padded = f"  {normalize_text(text)}  "
        for start in range(len(padded) - 2):
            digest = hashlib.blake2b(padded[start:start + 3].encode(), digest_size=8).digest()
            vector[int.from_bytes(digest[:4], "little") % self.dimensions] += 1.0 if digest[4] % 2 == 0 else -1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text."""
        return [self._vector(text) for text in texts]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two vectors."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / ((math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))) or 1.0)


class MemoryStorage:
    """In-memory stand-in for SupabaseStorage."""

    def __init__(self) -> None:
        """Start with no stored files."""
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put(self, path: str, data: bytes, content_type: str) -> None:
        """Keep a file."""
        self.objects[path] = (data, content_type)

    async def signed_url(self, path: str, expires_in: int = 3600) -> str | None:
        """A fake link for stored files."""
        return f"memory://{path}" if path in self.objects else None

    async def aclose(self) -> None:
        """Nothing to close."""


# ---- fictional invoices and photos ------------------------------------------------------------
def invoice_line(item: str, qty: float, rate: float, *, discount: float = 0.0, sku: str | None = None,
                 line_total: float | None = None) -> dict[str, Any]:
    """One line as InvoiceVisionAgent would return it; the line total adds up unless given."""
    return {"sku": sku, "item_name": item, "quantity": qty, "mro": rate, "discount": discount,
            "line_total": round(qty * rate - discount, 2) if line_total is None else line_total}


def invoice_doc(*, supplier: str | None = SUPPLIER, number: str | None = "INV-1001", date: str | None = "2026-10-20",
                lines: list[dict[str, Any]] | None = None, total: float | None = None,
                special_discount: float | None = None, tax: float | None = None,
                low_confidence: list[str] | None = None) -> dict[str, Any]:
    """A whole agent answer for an invoice; the total adds up unless given."""
    lines = ([invoice_line("63mm uPVC pipe", 10, 585.0, sku="PP-063"), invoice_line("PVC solvent cement 250ml", 2, 450.0)]
             if lines is None else lines)
    if total is None:
        total = round(sum(line["line_total"] or 0 for line in lines) - (special_discount or 0) + (tax or 0), 2)
    return {
        "doc_type": "invoice",
        "supplier": {"name": supplier, "address": "12 Main Street", "phone1": None},
        "invoice": {"invoice_number": number, "invoice_date": date, "delivery_date": None, "payment_type": None,
                    "credit_days": None, "special_discount": special_discount, "tax": tax, "total": total,
                    "lines": lines},
        "receipt": None,
        "low_confidence_fields": low_confidence or [],
    }


def receipt_doc() -> dict[str, Any]:
    """An agent answer for a payment receipt."""
    return {
        "doc_type": "receipt",
        "supplier": {"name": SUPPLIER, "address": None, "phone1": None},
        "invoice": None,
        "receipt": {"receipt_number": "R-77", "receipt_date": "2026-10-22", "payment_method": "cash",
                    "payments": [{"invoice_number": "INV-1001", "amount": 6750.0}], "cheques": []},
        "low_confidence_fields": [],
    }


def invoice_image(width: int = 1240, height: int = 1754, ext: str = ".jpg") -> bytes:
    """A synthetic invoice-like page (letterhead, table, totals)."""
    image = np.full((height, width, 3), 236, dtype=np.uint8)
    ink, font, scale = (60, 60, 60), cv2.FONT_HERSHEY_SIMPLEX, width / 1240
    cv2.putText(image, "LANKA PIPE TRADERS", (int(60 * scale), int(120 * scale)), font, 1.4 * scale, ink, 2)
    cv2.putText(image, "Invoice No INV-1001", (int(760 * scale), int(120 * scale)), font, 1.0 * scale, ink, 2)
    for row in range(6):
        cv2.putText(image, f"PP-06{row}  item {row}   10   585.00   5,850.00",
                    (int(60 * scale), int((700 + row * 70) * scale)), font, 0.9 * scale, ink, 2)
    cv2.putText(image, "Total 6,750.00", (int(760 * scale), int(1500 * scale)), font, 1.0 * scale, ink, 2)
    ok, encoded = cv2.imencode(ext, image)
    assert ok
    return encoded.tobytes()


# ---- scripted agents ---------------------------------------------------------------------------
def vision_step(doc: dict[str, Any] | Exception) -> Any:
    """One scripted InvoiceVisionAgent answer (or an error the model raises)."""
    return doc if isinstance(doc, Exception) else [assistant_message(json.dumps(doc))]


def vision_model(*docs: dict[str, Any] | Exception) -> ScriptedModel:
    """InvoiceVisionAgent stand-in: one scripted answer per photo."""
    return ScriptedModel([vision_step(doc) for doc in docs])


def _get(item: Any, key: str) -> Any:
    """Read a key from a dict or an SDK object."""
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def auto_sku_responder(*, pick: str = "top", wrong_id: str | None = None) -> Callable[[Any], Any]:
    """SkuMatchAgent stand-in that behaves like the real agent: first call find_sku_candidates for
    every line, then pick the top candidate ("top"), a new product ("new") or an unknown id ("wrong")."""

    def respond(call: Any) -> Any:
        """Answer one model call."""
        items = call.input if isinstance(call.input, list) else [{"role": "user", "content": call.input}]
        lines = json.loads(_get(next(item for item in items if _get(item, "role") == "user"), "content"))
        outputs = [item for item in items if _get(item, "type") == "function_call_output"]
        if not outputs:
            return [function_call("find_sku_candidates", {"raw_text": line["raw_text"]}, call_id=f"call_{line['line_index']}")
                    for line in lines]
        found = {data["raw_text"]: data["candidates"] for data in (json.loads(_get(o, "output")) for o in outputs)}
        matches = []
        for line in lines:
            candidates = found.get(line["raw_text"], [])
            if pick == "top" and candidates:
                matches.append({"line_index": line["line_index"], "product_id": candidates[0]["product_id"],
                                "is_new_product": False, "reason": "Top candidate."})
            elif pick == "wrong":
                matches.append({"line_index": line["line_index"], "product_id": wrong_id, "is_new_product": False,
                                "reason": "Guessed."})
            else:
                matches.append({"line_index": line["line_index"], "product_id": None, "is_new_product": True,
                                "reason": "No candidate is the same product."})
        return [assistant_message(json.dumps({"matches": matches}))]

    return respond


def sku_model(*, pick: str = "top", wrong_id: str | None = None, calls: int = 200) -> ScriptedModel:
    """A SkuMatchAgent stand-in good for ``calls`` model calls."""
    responder = auto_sku_responder(pick=pick, wrong_id=wrong_id)
    return ScriptedModel([ModelStep.respond(responder) for _ in range(calls)])


# ---- the running API ---------------------------------------------------------------------------
JPEG = ("invoice.jpg", invoice_image(), "image/jpeg")
# The offline hashing embedder scores spelling variants lower than OpenAI embeddings, so the API
# tests use a lower threshold; the 0.82 default itself is covered in test_agents.py.
BASE_SETTINGS = Settings(sku_match_threshold=0.5)


class Harness:
    """A running API plus handles to its scripted vision model, services and test database."""

    def __init__(self, client: TestClient, services: AppServices, vision: ScriptedModel, pg_url: str) -> None:
        """Keep the client and everything behind it."""
        self.client = client
        self.services = services
        self.vision = vision
        self.pg_url = pg_url

    def upload(self, doc: dict[str, Any] | Exception, **form: str):
        """Upload a photo; the scripted vision model answers with ``doc``."""
        self.vision.enqueue(vision_step(doc))
        return self.client.post("/invoices/upload", files={"file": JPEG}, data=form)

    def supplier(self, name: str = SUPPLIER, credit_days: int | None = 30) -> dict:
        """Create a supplier through the API."""
        response = self.client.post("/suppliers", json={"name": name, "credit_days": credit_days})
        assert response.status_code == 201, response.text
        return response.json()

    def product(self, name: str, **extra: Any) -> dict:
        """Create a product through the API."""
        response = self.client.post("/products", json={"name": name, **extra})
        assert response.status_code == 201, response.text
        return response.json()

    def sql(self, query: str, params: Sequence[Any] = ()) -> list[tuple]:
        """Check what the API saved, straight from the database."""
        return run_sql(self.pg_url, query, params)


@pytest.fixture
def make_harness(pg_url: str) -> Iterator[Callable[..., Harness]]:
    """Factory for a running API on a fresh database (call it with other settings if needed)."""
    opened: list[TestClient] = []

    def make(*, settings: Settings = BASE_SETTINGS, sku_pick: str = "top", with_storage: bool = True,
             embedder: Any = None) -> Harness:
        """Start the API with scripted agents, the offline embedder and memory storage."""
        vision = vision_model()
        holder: dict[str, AppServices] = {}

        async def factory(s: Settings) -> AppServices:
            """Build the services the way main.py does, with test doubles for OpenAI and Storage."""
            database = Database(pg_url, max_size=1)
            await database.open()
            holder["services"] = await build_services(
                s, db=database, storage=MemoryStorage() if with_storage else None, embedder=embedder or HashingEmbedder(),
                vision_model=vision, sku_model=sku_model(pick=sku_pick),
            )
            return holder["services"]

        client = TestClient(create_app(settings, factory))
        client.__enter__()
        opened.append(client)
        return Harness(client, holder["services"], vision, pg_url)

    yield make
    for client in opened:
        client.__exit__(None, None, None)
