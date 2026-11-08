"""The Supabase migrations, run on real Postgres (with Supabase's auth/storage stubbed)."""

from __future__ import annotations

import psycopg
import pytest

from tests.conftest import MIGRATIONS, reset_database

pytestmark = pytest.mark.postgres

BIZ = "00000000-0000-0000-0000-000000000001"
OWNER = "aaaaaaaa-0000-0000-0000-000000000001"
OTHER_BIZ = "00000000-0000-0000-0000-000000000002"

# Roughly what the first prototype's Supabase project has today (inferred from its frontend).
LEGACY_SCHEMA = """
create table suppliers (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  credit_days integer,
  phone1 text, phone2 text, email text, address text,
  related_company_1 text, related_company_2 text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create table items (id uuid primary key default gen_random_uuid(), sku text, name text not null unique);
create table invoices (
  id uuid primary key default gen_random_uuid(),
  supplier_id uuid not null references suppliers(id),
  invoice_number text not null,
  invoice_date date,
  delivery_date date,
  special_discount numeric(12,2) not null default 0,
  total numeric(12,2) not null default 0,
  image_url text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (supplier_id, invoice_number)
);
create table invoice_items (
  id uuid primary key default gen_random_uuid(),
  invoice_id uuid not null references invoices(id) on delete cascade,
  sku text,
  item_name text not null,
  quantity numeric(12,3) not null,
  mro numeric(12,2) not null,
  discount numeric(12,2) not null default 0,
  line_total numeric(12,2) not null,
  sort_order integer not null default 0
);
insert into suppliers (id, name, credit_days) values ('11111111-1111-1111-1111-111111111111', 'Lanka Pipe Traders', 30);
insert into items (name) values ('63mm uPVC pipe');
insert into invoices (id, supplier_id, invoice_number, invoice_date, total)
  values ('22222222-2222-2222-2222-222222222222', '11111111-1111-1111-1111-111111111111', 'INV-1', '2026-01-02', 5850);
insert into invoice_items (invoice_id, item_name, quantity, mro, line_total)
  values ('22222222-2222-2222-2222-222222222222', '63mm uPVC pipe', 10, 585, 5850);
"""


def run_all(conn: psycopg.Connection) -> None:
    for path in MIGRATIONS:
        conn.execute(path.read_text(encoding="utf-8"))


def tables(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute(
        "select table_name from information_schema.tables where table_schema = 'public'"
    ).fetchall()
    return {row[0] for row in rows}


def test_fresh_install_creates_the_proposal_schema(pg_url):
    with psycopg.connect(pg_url, autocommit=True) as conn:
        expected = {
            "businesses", "suppliers", "products", "product_aliases", "invoices", "invoice_items",
            "supplier_payments", "expenses", "cash_records", "model_predictions",
            "extraction_runs", "audit_log", "items",
        }
        assert expected <= tables(conn)
        assert conn.execute("select count(*) from businesses where id = %s", (BIZ,)).fetchone()[0] == 1
        bucket = conn.execute("select public from storage.buckets where id = 'invoice-images'").fetchone()
        assert bucket == (False,)
        rls = dict(conn.execute(
            "select relname, relrowsecurity from pg_class where relname in ('invoices', 'product_aliases')"
        ).fetchall())
        assert rls == {"invoices": True, "product_aliases": True}


def test_migrations_are_idempotent(pg_url):
    with psycopg.connect(pg_url, autocommit=True) as conn:
        run_all(conn)  # second run on top of the fixture's first run
        run_all(conn)
        assert conn.execute("select count(*) from businesses").fetchone()[0] == 1


def test_upgrades_the_first_prototypes_database_without_losing_rows(pg_server_url):
    reset_database(pg_server_url, migrations=[])
    with psycopg.connect(pg_server_url, autocommit=True) as conn:
        conn.execute(LEGACY_SCHEMA)
        run_all(conn)
        supplier = conn.execute("select name, credit_days, business_id::text from suppliers").fetchone()
        assert supplier == ("Lanka Pipe Traders", 30, BIZ)
        invoice = conn.execute(
            "select invoice_number, total, status, math_ok, payment_status, business_id::text from invoices"
        ).fetchone()
        assert invoice == ("INV-1", 5850, "draft", None, "unpaid", BIZ)
        line = conn.execute("select item_name, match_status, product_id from invoice_items").fetchone()
        assert line == ("63mm uPVC pipe", "unmatched", None)
        # the legacy items table is kept (no view replaces it)
        kind = conn.execute("select relkind from pg_class where relname = 'items'").fetchone()[0]
        assert kind == "r"
        # agent drafts may now arrive without supplier / number / line values
        conn.execute("insert into invoices (supplier_name_raw) values ('Unknown Co')")
        conn.execute(
            "insert into invoice_items (invoice_id, item_name) values ('22222222-2222-2222-2222-222222222222', 'unreadable')"
        )
        # the old per-supplier uniqueness still holds
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "insert into invoices (supplier_id, invoice_number) "
                "values ('11111111-1111-1111-1111-111111111111', 'INV-1')"
            )


def test_items_view_keeps_the_prototype_frontend_working(pg_url):
    with psycopg.connect(pg_url, autocommit=True) as conn:
        assert conn.execute("select relkind from pg_class where relname = 'items'").fetchone()[0] == "v"
        conn.execute("insert into items (name, sku) values ('Wall Paint 4L', null)")
        conn.execute("update items set sku = 'WP-4' where name = 'Wall Paint 4L'")
        product = conn.execute("select name, sku, business_id::text from products").fetchone()
        assert product == ("Wall Paint 4L", "WP-4", BIZ)
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("insert into items (name) values ('wall paint 4l')")


def test_check_constraints(pg_url):
    with psycopg.connect(pg_url, autocommit=True) as conn:
        for sql in (
            "insert into invoices (status) values ('approved')",
            "insert into invoices (payment_status) values ('maybe')",
            "insert into suppliers (name, credit_days) values ('x', -1)",
            "insert into product_aliases (product_id, alias_text, normalized_text, source) "
            "values (gen_random_uuid(), 'a', 'a', 'robot')",
        ):
            with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.ForeignKeyViolation)):
                conn.execute(sql)


def test_row_level_security_limits_owners_to_their_business(pg_url):
    with psycopg.connect(pg_url, autocommit=True) as conn:
        conn.execute("update businesses set owner_user_id = %s where id = %s", (OWNER, BIZ))
        conn.execute("insert into businesses (id, name) values (%s, 'Other shop')", (OTHER_BIZ,))
        conn.execute("insert into suppliers (business_id, name) values (%s, 'Mine'), (%s, 'Theirs')", (BIZ, OTHER_BIZ))
        conn.execute("grant usage on schema public to authenticated, anon")
        conn.execute("grant select on all tables in schema public to authenticated, anon")

        conn.execute("set role authenticated")
        conn.execute("select set_config('request.jwt.claim.sub', %s, false)", (OWNER,))
        assert [r[0] for r in conn.execute("select name from suppliers").fetchall()] == ["Mine"]
        assert conn.execute("select count(*) from businesses").fetchone()[0] == 1

        conn.execute("select set_config('request.jwt.claim.sub', '', false)")
        assert conn.execute("select count(*) from suppliers").fetchone()[0] == 0

        conn.execute("reset role")
        conn.execute("set role anon")
        assert conn.execute("select count(*) from suppliers").fetchone()[0] == 0
        conn.execute("reset role")
