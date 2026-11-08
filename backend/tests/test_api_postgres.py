"""The main owner flows again, persisted in real Postgres, checking the rows that land."""

from __future__ import annotations

import asyncio

import psycopg
import pytest

from database.postgres import PostgresRepository
from tests.helpers import invoice_doc, invoice_line

pytestmark = pytest.mark.postgres


@pytest.fixture
def pg_harness(make_harness, pg_url):
    repo = PostgresRepository(pg_url, max_size=1)
    asyncio.run(repo.open())
    harness = make_harness(repo=repo)
    harness.pg_url = pg_url
    return harness


def query(url: str, sql: str, params=()):
    with psycopg.connect(url) as conn:
        return conn.execute(sql, params).fetchall()


def test_health_reports_postgres(pg_harness):
    assert pg_harness.client.get("/health").json()["repository"] == "postgres"


def test_upload_and_verify_persist(pg_harness):
    h = pg_harness
    supplier = h.supplier(credit_days=14)
    h.product("63mm uPVC pipe")
    h.product("PVC solvent cement 250ml")
    invoice = h.upload(invoice_doc()).json()
    assert invoice["math_ok"] is True and all(l["match_status"] == "linked" for l in invoice["lines"])
    assert h.client.post(f"/invoices/{invoice['id']}/verify").status_code == 200

    [row] = query(h.pg_url, "select status, due_date::text, supplier_id::text, total from invoices")
    assert row == ("verified", "2026-11-03", supplier["id"], 6750)
    lines = query(h.pg_url, "select item_name, mro, match_status, product_id is not null from invoice_items order by sort_order")
    assert lines == [("63mm uPVC pipe", 585, "linked", True), ("PVC solvent cement 250ml", 450, "linked", True)]
    [run] = query(h.pg_url, "select agent, guardrail_passed, raw_output->>'doc_type', invoice_id::text from extraction_runs")
    assert run == ("InvoiceVisionAgent", True, "invoice", invoice["id"])
    events = [r[0] for r in query(h.pg_url, "select event_type from audit_log order by created_at")]
    assert events == ["invoice_extracted", "sku_matched", "invoice_verified"]
    aliases = query(h.pg_url, "select source, embedding is not null from product_aliases")
    assert aliases and all(source == "catalogue" and embedded for source, embedded in aliases)


def test_fix_confirm_and_alias_learning_persist(pg_harness):
    h = pg_harness
    h.supplier()
    bad = invoice_doc(lines=[invoice_line("63mm uPVC pipe", 10, 585.0, line_total=5580.0)], total=5580.0)
    invoice = h.upload(bad).json()
    assert invoice["math_ok"] is False
    line = invoice["lines"][0]
    fixed = h.client.post(
        f"/invoices/{invoice['id']}/fix",
        json={"total": 5850.0, "lines": [{"id": line["id"], "item_name": line["item_name"],
                                          "quantity": 10, "mro": 585.0, "line_total": 5850.0}]},
    ).json()
    assert fixed["math_ok"] is True
    confirmed = h.client.post(
        f"/invoices/{invoice['id']}/confirm-sku",
        json={"line_id": fixed["lines"][0]["id"], "new_product": {"name": "uPVC Pipe 63mm"}},
    ).json()
    product_id = confirmed["lines"][0]["product_id"]
    assert h.client.post(f"/invoices/{invoice['id']}/verify").status_code == 200

    aliases = query(h.pg_url, "select alias_text, source, embedding is not null from product_aliases order by created_at")
    assert ("63mm uPVC pipe", "owner_confirmed", True) in aliases

    again = h.upload(invoice_doc(number="INV-2", lines=[invoice_line("63MM uPVC  pipe", 2, 585.0)])).json()
    assert again["lines"][0]["product_id"] == product_id
    [(math_issues,)] = query(h.pg_url, "select math_issues from invoices where id = %s", (invoice["id"],))
    assert math_issues == []


def test_duplicate_is_refused_by_postgres_too(pg_harness):
    h = pg_harness
    h.supplier()
    assert h.upload(invoice_doc()).status_code == 201
    assert h.upload(invoice_doc()).status_code == 409
    assert query(h.pg_url, "select count(*) from invoices") == [(1,)]
