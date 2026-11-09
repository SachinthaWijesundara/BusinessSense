"""Tests for database/client.py and the repo-root database/*.sql files, on a real Postgres with
pgvector (Supabase's auth/storage are stubbed), plus the Supabase Storage client."""

from __future__ import annotations

import json
from datetime import date

import httpx
import psycopg
import pytest

from database.client import (
    DuplicateError,
    ExtractionRunLog,
    InvoiceQuery,
    LineMatchUpdate,
    NewInvoice,
    NewInvoiceLine,
    NewProduct,
    NewSupplier,
    NotFoundError,
    RepositoryError,
    StorageError,
    SupabaseStorage,
)
from tests.conftest import BIZ, SQL_FILES, HashingEmbedder, reset_database, run_sql

OTHER = "00000000-0000-0000-0000-000000000002"
MISSING = "99999999-9999-9999-9999-999999999999"
OWNER = "aaaaaaaa-0000-0000-0000-000000000001"


@pytest.fixture
def other_business(pg_url):
    """A second business, to prove rows never cross businesses."""
    run_sql(pg_url, "insert into businesses (id, name) values (%s, 'Other shop')", (OTHER,))


def lines(*items: tuple[str, float, float]) -> list[NewInvoiceLine]:
    """Invoice lines whose totals add up."""
    return [NewInvoiceLine(sort_order=i, item_name=name, quantity=qty, mro=rate, line_total=round(qty * rate, 2))
            for i, (name, qty, rate) in enumerate(items)]


# ---- Database: suppliers, products, aliases --------------------------------------------------
@pytest.mark.postgres
@pytest.mark.anyio
async def test_suppliers(db, other_business):
    """Create, list, find by name, update; other businesses never see them."""
    a = await db.create_supplier(BIZ, NewSupplier(name="Lanka Pipe Traders", credit_days=30))
    await db.create_supplier(BIZ, NewSupplier(name="Ceylon Paints"))
    await db.create_supplier(OTHER, NewSupplier(name="Lanka Pipe Traders"))
    assert [s.name for s in await db.list_suppliers(BIZ)] == ["Ceylon Paints", "Lanka Pipe Traders"]
    assert (await db.get_supplier(BIZ, a.id)).credit_days == 30 and await db.get_supplier(OTHER, a.id) is None
    assert [s.id for s in await db.find_suppliers_by_name(BIZ, "  lanka pipe TRADERS ")] == [a.id]
    updated = await db.update_supplier(BIZ, a.id, {"credit_days": 45, "phone1": "011 000 0000"})
    assert (updated.credit_days, updated.phone1) == (45, "011 000 0000")
    with pytest.raises(NotFoundError):
        await db.update_supplier(OTHER, a.id, {"credit_days": 1})
    with pytest.raises(RepositoryError):
        await db.update_supplier(BIZ, a.id, {"business_id": OTHER})


@pytest.mark.postgres
@pytest.mark.anyio
async def test_products_and_aliases(db, other_business):
    """Products are unique by name; aliases upsert, get embeddings and are searched by similarity."""
    embedder = HashingEmbedder()
    pipe = await db.create_product(BIZ, NewProduct(name="uPVC Pipe 63mm", sku="PP-063", unit="length"))
    paint = await db.create_product(BIZ, NewProduct(name="Wall Paint 4L"))
    with pytest.raises(DuplicateError):
        await db.create_product(BIZ, NewProduct(name="UPVC PIPE 63MM"))
    assert [p.name for p in await db.list_products(BIZ)] == ["uPVC Pipe 63mm", "Wall Paint 4L"]
    assert (await db.get_product(BIZ, pipe.id)).sku == "PP-063" and await db.get_product(OTHER, pipe.id) is None
    assert await db.has_products(BIZ) and not await db.has_products(OTHER)
    assert {p.id for p in await db.products_without_aliases(BIZ)} == {pipe.id, paint.id}

    pipe_vec, paint_vec, query_vec = await embedder.embed(["uPVC Pipe 63mm", "Wall Paint 4L", "upvc pipe 63"])
    await db.add_alias(BIZ, product_id=pipe.id, alias_text="uPVC Pipe 63mm", normalized_text="upvc pipe 63mm",
                       embedding=pipe_vec, source="catalogue", confirmed=True)
    await db.add_alias(BIZ, product_id=paint.id, alias_text="Wall Paint 4L", normalized_text="wall paint 4l",
                       embedding=None, source="catalogue", confirmed=True)
    assert await db.products_without_aliases(BIZ) == []
    [pending] = await db.aliases_without_embeddings(BIZ)
    await db.set_alias_embedding(BIZ, pending.id, paint_vec)
    assert await db.aliases_without_embeddings(BIZ) == []
    await db.add_alias(BIZ, product_id=pipe.id, alias_text="UPVC pipe 63MM", normalized_text="upvc pipe 63mm",
                       embedding=None, source="owner_confirmed", confirmed=True)
    [exact] = await db.find_aliases_exact(BIZ, "upvc pipe 63mm")
    assert (exact.product_id, exact.similarity, exact.product_sku) == (pipe.id, 1.0, "PP-063")
    hits = await db.search_aliases(BIZ, query_vec, 5)
    assert hits[0].product_id == pipe.id and hits[0].similarity > hits[-1].similarity
    assert await db.search_aliases(OTHER, query_vec, 5) == []
    with pytest.raises(NotFoundError):
        await db.add_alias(OTHER, product_id=pipe.id, alias_text="x", normalized_text="x", embedding=None,
                           source="manual", confirmed=True)
    [vec] = await embedder.embed(["guess"])
    await db.add_alias(BIZ, product_id=pipe.id, alias_text="guess", normalized_text="guess", embedding=vec,
                       source="agent_linked", confirmed=False)
    assert await db.find_aliases_exact(BIZ, "guess") == []  # unconfirmed aliases are never used


# ---- Database: invoices ------------------------------------------------------------------------
@pytest.mark.postgres
@pytest.mark.anyio
async def test_invoice_lifecycle(db, other_business):
    """Create with lines, read back with names, duplicate rule, update header and lines together."""
    supplier = await db.create_supplier(BIZ, NewSupplier(name="Lanka Pipe Traders"))
    product = await db.create_product(BIZ, NewProduct(name="uPVC Pipe 63mm"))
    new = NewInvoice(supplier_id=supplier.id, invoice_number="INV-1001", invoice_date=date(2026, 10, 20), total=6750.0,
                     source="agent", math_ok=True, low_confidence_fields=["invoice.lines[0].quantity"])
    created = await db.create_invoice(BIZ, new, lines(("63mm uPVC pipe", 10, 585.0), ("Solvent cement", 2, 450.0)))
    assert (created.status, created.payment_status, created.supplier_name) == ("draft", "unpaid", "Lanka Pipe Traders")
    assert [l.item_name for l in created.lines] == ["63mm uPVC pipe", "Solvent cement"]
    assert created.lines[0].unit_rate == 585.0 and created.low_confidence_fields == ["invoice.lines[0].quantity"]
    assert (await db.get_invoice(BIZ, created.id)).id == created.id and await db.get_invoice(OTHER, created.id) is None

    with pytest.raises(DuplicateError):
        await db.create_invoice(BIZ, NewInvoice(supplier_id=supplier.id, invoice_number="INV-1001"), [])
    assert (await db.find_invoice_by_number(BIZ, supplier.id, "INV-1001")).id == created.id
    assert await db.find_invoice_by_number(BIZ, supplier.id, "INV-1001", exclude_id=created.id) is None

    linked_line = NewInvoiceLine(item_name="63mm uPVC pipe", quantity=12, mro=585.0, line_total=7020.0, product_id=product.id,
                                 match_status="linked")
    updated = await db.update_invoice(BIZ, created.id, {"total": 7020.0, "math_issues": [{"code": "x"}]}, [linked_line])
    assert updated.total == 7020.0 and updated.math_issues == [{"code": "x"}]
    assert [(l.quantity, l.product_name) for l in updated.lines] == [(12.0, "uPVC Pipe 63mm")]
    assert len((await db.update_invoice(BIZ, created.id, {"payment_status": "paid"})).lines) == 1
    with pytest.raises(RepositoryError):
        await db.update_invoice(BIZ, created.id, {"business_id": OTHER})
    with pytest.raises(NotFoundError):
        await db.update_invoice(OTHER, created.id, {"total": 1.0})
    with pytest.raises(NotFoundError):
        await db.create_invoice(BIZ, NewInvoice(supplier_id=MISSING), [])


@pytest.mark.postgres
@pytest.mark.anyio
async def test_failed_update_changes_nothing(db):
    """Header and lines change in one transaction: a bad line leaves the invoice as it was."""
    created = await db.create_invoice(BIZ, NewInvoice(total=5850.0), lines(("63mm uPVC pipe", 10, 585.0)))
    with pytest.raises(NotFoundError):
        await db.update_invoice(BIZ, created.id, {"total": 1.0}, [NewInvoiceLine(item_name="Ghost", product_id=MISSING)])
    after = await db.get_invoice(BIZ, created.id)
    assert after.total == 5850.0 and [l.item_name for l in after.lines] == ["63mm uPVC pipe"]


@pytest.mark.postgres
@pytest.mark.anyio
async def test_line_matches(db, other_business):
    """A line's product match is stored and read back with the product name."""
    pipe = await db.create_product(BIZ, NewProduct(name="uPVC Pipe 63mm"))
    created = await db.create_invoice(BIZ, NewInvoice(), lines(("63mm upvc pipe", 1, 585.0)))
    line = created.lines[0]
    await db.update_line_match(BIZ, created.id, line.id, LineMatchUpdate(
        product_id=pipe.id, match_status="linked", match_similarity=0.91,
        match_candidates=[{"product_id": pipe.id, "similarity": 0.91}], match_reason="ok"))
    [linked] = (await db.get_invoice(BIZ, created.id)).lines
    assert (linked.product_id, linked.product_name, linked.match_status, linked.match_reason) == (pipe.id, "uPVC Pipe 63mm", "linked", "ok")
    assert linked.match_similarity == pytest.approx(0.91)
    with pytest.raises(NotFoundError):
        await db.update_line_match(OTHER, created.id, line.id, LineMatchUpdate(match_status="pending"))
    with pytest.raises(NotFoundError):
        await db.update_line_match(BIZ, created.id, MISSING, LineMatchUpdate(match_status="pending"))


@pytest.mark.postgres
@pytest.mark.anyio
async def test_search_all_invoices_and_supplier_lines(db, other_business):
    """Every invoice filter, the unlimited list, and the verified lines of one supplier."""
    a = await db.create_supplier(BIZ, NewSupplier(name="A"))
    b = await db.create_supplier(BIZ, NewSupplier(name="B"))
    await db.create_invoice(BIZ, NewInvoice(supplier_id=a.id, invoice_number="A-1", invoice_date=date(2026, 1, 5)),
                            lines(("Nails", 1, 10.0)))
    await db.create_invoice(BIZ, NewInvoice(supplier_id=a.id, invoice_number="A-2", invoice_date=date(2026, 3, 5),
                                            status="verified", payment_status="paid"), lines(("Pipe", 2, 50.0)))
    await db.create_invoice(BIZ, NewInvoice(supplier_id=b.id, invoice_number="B-1"), [])
    await db.create_invoice(OTHER, NewInvoice(invoice_number="X-1"), [])

    async def numbers(**filters):
        """Invoice numbers matching the filters."""
        return [r.invoice_number for r in await db.list_invoices(BIZ, InvoiceQuery(**filters))]

    assert await numbers() == ["A-2", "A-1", "B-1"]
    assert await numbers(supplier_id=a.id) == ["A-2", "A-1"]
    assert await numbers(status="verified") == ["A-2"]
    assert await numbers(payment_status="unpaid") == ["A-1", "B-1"]
    assert await numbers(invoice_number="b-") == ["B-1"]
    assert await numbers(date_from=date(2026, 2, 1)) == ["A-2"]
    assert await numbers(date_to=date(2026, 2, 1)) == ["A-1"]
    assert await numbers(limit=1) == ["A-2"]
    rows = await db.list_invoices(BIZ, InvoiceQuery())
    assert rows[0].supplier_name == "A" and not hasattr(rows[0], "lines")
    assert [r.invoice_number for r in await db.list_all_invoices(BIZ, supplier_id=a.id)] == ["A-2", "A-1"]
    assert len(await db.list_all_invoices(BIZ)) == 3
    [verified_line] = await db.list_supplier_lines(BIZ, a.id)
    assert (verified_line.invoice_number, verified_line.item_name, verified_line.unit_rate) == ("A-2", "Pipe", 50.0)


@pytest.mark.postgres
@pytest.mark.anyio
async def test_logs(db, pg_url, other_business):
    """Raw agent outputs and audit events are kept per invoice and business."""
    created = await db.create_invoice(BIZ, NewInvoice(), [])
    await db.log_extraction_run(BIZ, ExtractionRunLog(invoice_id=created.id, agent="InvoiceVisionAgent", model="gpt-4o",
                                                      guardrail_passed=True, raw_output={"doc_type": "invoice"}, duration_ms=12))
    await db.log_event(BIZ, created.id, "invoice_extracted", {"math_ok": True})
    await db.log_event(BIZ, created.id, "invoice_verified", {"due_date": "2026-11-19"})
    events = await db.list_events(BIZ, created.id)
    assert [e.event_type for e in events] == ["invoice_extracted", "invoice_verified"] and events[0].payload == {"math_ok": True}
    assert await db.list_events(OTHER, created.id) == []
    assert run_sql(pg_url, "select agent, raw_output->>'doc_type' from extraction_runs") == [("InvoiceVisionAgent", "invoice")]


# ---- the SQL files -----------------------------------------------------------------------------
# Roughly what the first prototype's Supabase project has today (inferred from its frontend).
LEGACY_SCHEMA = """
create table suppliers (id uuid primary key default gen_random_uuid(), name text not null, credit_days integer,
  phone1 text, phone2 text, email text, address text, related_company_1 text, related_company_2 text,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now());
create table items (id uuid primary key default gen_random_uuid(), sku text, name text not null unique);
create table invoices (id uuid primary key default gen_random_uuid(), supplier_id uuid not null references suppliers(id),
  invoice_number text not null, invoice_date date, delivery_date date, special_discount numeric(12,2) not null default 0,
  total numeric(12,2) not null default 0, image_url text, created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(), unique (supplier_id, invoice_number));
create table invoice_items (id uuid primary key default gen_random_uuid(),
  invoice_id uuid not null references invoices(id) on delete cascade, sku text, item_name text not null,
  quantity numeric(12,3) not null, mro numeric(12,2) not null, discount numeric(12,2) not null default 0,
  line_total numeric(12,2) not null, sort_order integer not null default 0);
insert into suppliers (id, name, credit_days) values ('11111111-1111-1111-1111-111111111111', 'Lanka Pipe Traders', 30);
insert into items (name, sku) values ('63mm uPVC pipe', 'PP-063'), ('Wall Paint 4L', null);
insert into invoices (id, supplier_id, invoice_number, invoice_date, total)
  values ('22222222-2222-2222-2222-222222222222', '11111111-1111-1111-1111-111111111111', 'INV-1', '2026-01-02', 5850);
insert into invoice_items (invoice_id, item_name, quantity, mro, line_total)
  values ('22222222-2222-2222-2222-222222222222', '63mm uPVC pipe', 10, 585, 5850);
"""


def _run_all(conn: psycopg.Connection) -> None:
    """Apply the three SQL files in order."""
    for path in SQL_FILES:
        conn.execute(path.read_text(encoding="utf-8"))


@pytest.mark.postgres
def test_fresh_install_and_rerun(pg_url):
    """All tables exist, the bucket is private, RLS is on, and running everything again changes nothing."""
    assert [p.name for p in SQL_FILES] == ["01_schema.sql", "02_storage.sql", "03_row_level_security.sql"]
    with psycopg.connect(pg_url, autocommit=True) as conn:
        tables = {r[0] for r in conn.execute("select table_name from information_schema.tables where table_schema = 'public'")}
        assert {"businesses", "suppliers", "products", "product_aliases", "invoices", "invoice_items", "supplier_payments",
                "expenses", "cash_records", "model_predictions", "extraction_runs", "audit_log", "items"} <= tables
        assert conn.execute("select public from storage.buckets where id = 'invoice-images'").fetchone() == (False,)
        assert conn.execute("select relrowsecurity from pg_class where relname = 'invoices'").fetchone() == (True,)
        _run_all(conn)
        _run_all(conn)
        assert conn.execute("select count(*) from businesses").fetchone()[0] == 1


@pytest.mark.postgres
def test_upgrades_the_prototype_database(pg_server_url):
    """On the prototype's tables: rows are kept, new columns get defaults, items are copied into products."""
    reset_database(pg_server_url, sql_files=[])
    with psycopg.connect(pg_server_url, autocommit=True) as conn:
        conn.execute(LEGACY_SCHEMA)
        _run_all(conn)
        _run_all(conn)  # the items copy is idempotent
        assert conn.execute("select name, credit_days, business_id::text from suppliers").fetchone() == ("Lanka Pipe Traders", 30, BIZ)
        assert conn.execute("select invoice_number, total, status, payment_status from invoices").fetchone() == ("INV-1", 5850, "draft", "unpaid")
        assert conn.execute("select match_status, product_id from invoice_items").fetchone() == ("unmatched", None)
        assert conn.execute("select relkind from pg_class where relname = 'items'").fetchone()[0] == "r"
        assert sorted(conn.execute("select name, sku from products").fetchall()) == [("63mm uPVC pipe", "PP-063"), ("Wall Paint 4L", None)]
        conn.execute("insert into invoices (supplier_name_raw) values ('Unknown Co')")  # supplier may now be empty
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("insert into invoices (supplier_id, invoice_number) values ('11111111-1111-1111-1111-111111111111', 'INV-1')")


@pytest.mark.postgres
def test_items_view_and_check_constraints(pg_url):
    """On a fresh project "items" is a writable view of products; bad values are refused."""
    with psycopg.connect(pg_url, autocommit=True) as conn:
        assert conn.execute("select relkind from pg_class where relname = 'items'").fetchone()[0] == "v"
        conn.execute("insert into items (name, sku) values ('Wall Paint 4L', null)")
        conn.execute("update items set sku = 'WP-4' where name = 'Wall Paint 4L'")
        assert conn.execute("select name, sku from products").fetchone() == ("Wall Paint 4L", "WP-4")
        for sql in ("insert into invoices (status) values ('approved')",
                    "insert into invoices (payment_status) values ('maybe')",
                    "insert into suppliers (name, credit_days) values ('x', -1)"):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(sql)


@pytest.mark.postgres
def test_row_level_security(pg_url):
    """A logged-in owner sees only their business; no login or the anon key sees nothing."""
    run_sql(pg_url, "update businesses set owner_user_id = %s where id = %s", (OWNER, BIZ))
    run_sql(pg_url, "insert into businesses (id, name) values (%s, 'Other shop')", (OTHER,))
    run_sql(pg_url, "insert into suppliers (business_id, name) values (%s, 'Mine'), (%s, 'Theirs')", (BIZ, OTHER))
    with psycopg.connect(pg_url, autocommit=True) as conn:
        conn.execute("grant usage on schema public to authenticated, anon")
        conn.execute("grant select on all tables in schema public to authenticated, anon")
        conn.execute("set role authenticated")
        conn.execute("select set_config('request.jwt.claim.sub', %s, false)", (OWNER,))
        assert [r[0] for r in conn.execute("select name from suppliers")] == ["Mine"]
        conn.execute("select set_config('request.jwt.claim.sub', '', false)")
        assert conn.execute("select count(*) from suppliers").fetchone()[0] == 0
        conn.execute("reset role")
        conn.execute("set role anon")
        assert conn.execute("select count(*) from suppliers").fetchone()[0] == 0
        conn.execute("reset role")


# ---- Supabase Storage client ------------------------------------------------------------------
URL = "https://abc.supabase.co"


def _storage(handler, key: str = "eyJhbGciOi.fake.jwt") -> SupabaseStorage:
    """A SupabaseStorage whose HTTP calls go to ``handler`` instead of the internet."""
    return SupabaseStorage(url=URL, service_key=key, bucket="invoice-images",
                           client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.anyio
async def test_storage_upload_and_signed_link():
    """Uploads go to the private bucket with the key; signed links are built from Supabase's answer."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Record the request and answer like Supabase."""
        seen.append(request)
        if "/sign/" in str(request.url):
            assert json.loads(request.content) == {"expiresIn": 600}
            return httpx.Response(200, json={"signedURL": "/object/sign/invoice-images/b/i/clahe.png?token=t"})
        return httpx.Response(200, json={})

    store = _storage(handler)
    await store.put("b/i/original.jpg", b"jpeg-bytes", "image/jpeg")
    upload = seen[0]
    assert str(upload.url) == f"{URL}/storage/v1/object/invoice-images/b/i/original.jpg"
    assert upload.headers["authorization"] == "Bearer eyJhbGciOi.fake.jwt" and upload.headers["x-upsert"] == "true"
    assert upload.content == b"jpeg-bytes"
    assert await store.signed_url("b/i/clahe.png", expires_in=600) == f"{URL}/storage/v1/object/sign/invoice-images/b/i/clahe.png?token=t"
    await store.aclose()


@pytest.mark.anyio
async def test_storage_keys_and_failures():
    """New sb_secret_ keys are not sent as Bearer; failed uploads raise; failed links give None."""
    seen = []

    def ok(request: httpx.Request) -> httpx.Response:
        """Record the request and accept it."""
        seen.append(request)
        return httpx.Response(200, json={})

    await _storage(ok, key="sb_secret_abc").put("a.png", b"x", "image/png")
    assert "authorization" not in seen[0].headers and seen[0].headers["apikey"] == "sb_secret_abc"
    failing = _storage(lambda request: httpx.Response(400, json={"error": "Bucket not found"}))
    with pytest.raises(StorageError, match="Bucket not found"):
        await failing.put("a.png", b"x", "image/png")
    assert await failing.signed_url("missing.png") is None
