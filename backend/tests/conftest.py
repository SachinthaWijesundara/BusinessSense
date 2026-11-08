from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

os.environ.setdefault("OPENAI_AGENTS_DISABLE_TRACING", "1")

from agents import set_tracing_disabled  # noqa: E402

set_tracing_disabled(True)

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent
MIGRATIONS = sorted((REPO_DIR / "supabase" / "migrations").glob("*.sql"))
PGLITE_DIR = BACKEND_DIR / "tests" / "pglite"

# Stand-ins for what Supabase provides (auth.uid(), storage.buckets, API roles) so the
# migrations run on plain Postgres exactly as they would on Supabase.
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


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def pg_server_url() -> Iterator[str]:
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        if os.getenv("TEST_DATABASE_RESET_OK") != "1":
            pytest.skip("TEST_DATABASE_URL is wiped by the tests; set TEST_DATABASE_RESET_OK=1 to allow it")
        yield url
        return
    node = shutil.which("node")
    if not node or not (PGLITE_DIR / "node_modules").exists():
        pytest.skip("No test Postgres: run `npm install` in backend/tests/pglite or set TEST_DATABASE_URL")
    port = _free_port()
    proc = subprocess.Popen(
        [node, "server.mjs", str(port)],
        cwd=PGLITE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + 60
    assert proc.stdout is not None
    while True:
        line = proc.stdout.readline()
        if "PGLITE_READY" in line:
            break
        if proc.poll() is not None or time.monotonic() > deadline:
            proc.kill()
            pytest.fail(f"PGlite test server did not start: {line}")
    try:
        yield f"postgresql://postgres:postgres@127.0.0.1:{port}/postgres?sslmode=disable"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def reset_database(url: str, *, migrations: list[Path] | None = None, stubs: bool = True) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        for schema in ("public", "extensions", "auth", "storage"):
            conn.execute(f"drop schema if exists {schema} cascade")
        conn.execute("create schema public")
        if stubs:
            conn.execute(SUPABASE_STUBS)
        for path in MIGRATIONS if migrations is None else migrations:
            conn.execute(path.read_text(encoding="utf-8"))


@pytest.fixture
def pg_url(pg_server_url: str) -> str:
    reset_database(pg_server_url)
    return pg_server_url


# The API harness is shared by the in-memory and the Postgres API tests.
from tests.harness import make_harness  # noqa: E402,F401
