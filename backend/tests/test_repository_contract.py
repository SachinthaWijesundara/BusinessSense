"""One contract, two implementations: InMemoryRepository and PostgresRepository (real SQL)."""

from __future__ import annotations

from datetime import date

import pytest

from database.models import (
    ExtractionRunLog,
    InvoiceQuery,
    LineMatchUpdate,
    NewInvoice,
    NewInvoiceLine,
    NewProduct,
    NewSupplier,
)
from database.postgres import PostgresRepository
from database.repository import DuplicateError, InMemoryRepository, NotFoundError, RepositoryError
from services.embeddings import HashingEmbedder

pytestmark = pytest.mark.anyio
BIZ = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"
MISSING = "99999999-9999-9999-9999-999999999999"


@pytest.fixture(params=["memory", pytest.param("postgres", marks=pytest.mark.postgres)])
async def repo(request):
    if request.param == "memory":
        yield InMemoryRepository()
        return
    url = request.getfixturevalue("pg_url")
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f"insert into businesses (id, name) values ('{OTHER}', 'Other shop')")
    repository = PostgresRepository(url, max_size=1)
    await repository.open()
    try:
        yield repository
    finally:
        await repository.close()


def lines(*items: tuple[str, float, float]) -> list[NewInvoiceLine]:
    return [
        NewInvoiceLine(sort_order=i, item_name=name, quantity=qty, mro=rate, line_total=round(qty * rate, 2))
        for i, (name, qty, rate) in enumerate(items)
    ]


async def test_suppliers(repo):
    a = await repo.create_supplier(BIZ, NewSupplier(name="Lanka Pipe Traders", credit_days=30))
    await repo.create_supplier(BIZ, NewSupplier(name="Ceylon Paints"))
    await repo.create_supplier(OTHER, NewSupplier(name="Lanka Pipe Traders"))
    assert [s.name for s in await repo.list_suppliers(BIZ)] == ["Ceylon Paints", "Lanka Pipe Traders"]
    assert (await repo.get_supplier(BIZ, a.id)).credit_days == 30
    assert await repo.get_supplier(OTHER, a.id) is None
    assert [s.id for s in await repo.find_suppliers_by_name(BIZ, "  lanka pipe TRADERS ")] == [a.id]
    updated = await repo.update_supplier(BIZ, a.id, {"credit_days": 45, "phone1": "011 000 0000"})
    assert (updated.credit_days, updated.phone1) == (45, "011 000 0000")
    with pytest.raises(NotFoundError):
        await repo.update_supplier(OTHER, a.id, {"credit_days": 1})
    with pytest.raises(RepositoryError):
        await repo.update_supplier(BIZ, a.id, {"business_id": OTHER})


async def test_products_and_aliases(repo):
    embedder = HashingEmbedder()
    pipe = await repo.create_product(BIZ, NewProduct(name="uPVC Pipe 63mm", sku="PP-063", unit="length"))
    paint = await repo.create_product(BIZ, NewProduct(name="Wall Paint 4L"))
    with pytest.raises(DuplicateError):
        await repo.create_product(BIZ, NewProduct(name="UPVC PIPE 63MM"))
    assert await repo.has_products(BIZ) and not await repo.has_products(OTHER)
    assert {p.id for p in await repo.products_without_aliases(BIZ)} == {pipe.id, paint.id}

    [pipe_vec, paint_vec, query_vec] = await embedder.embed(["uPVC Pipe 63mm", "Wall Paint 4L", "upvc pipe 63"])
    await repo.add_alias(BIZ, product_id=pipe.id, alias_text="uPVC Pipe 63mm", normalized_text="upvc pipe 63mm",
                         embedding=pipe_vec, source="catalogue", confirmed=True)
    await repo.add_alias(BIZ, product_id=paint.id, alias_text="Wall Paint 4L", normalized_text="wall paint 4l",
                         embedding=None, source="catalogue", confirmed=True)
    assert await repo.products_without_aliases(BIZ) == []
    [pending] = await repo.aliases_without_embeddings(BIZ)
    assert pending.product_id == paint.id
    await repo.set_alias_embedding(BIZ, pending.id, paint_vec)
    assert await repo.aliases_without_embeddings(BIZ) == []

    # upsert: same normalised text for the same product does not duplicate
    await repo.add_alias(BIZ, product_id=pipe.id, alias_text="UPVC pipe 63MM", normalized_text="upvc pipe 63mm",
                         embedding=None, source="owner_confirmed", confirmed=True)
    [exact] = await repo.find_aliases_exact(BIZ, "upvc pipe 63mm")
    assert (exact.product_id, exact.similarity, exact.product_sku) == (pipe.id, 1.0, "PP-063")
    assert await repo.find_aliases_exact(OTHER, "upvc pipe 63mm") == []

    hits = await repo.search_aliases(BIZ, query_vec, 5)
    assert [h.product_id for h in hits][:1] == [pipe.id]
    assert hits[0].similarity > hits[-1].similarity
    assert await repo.search_aliases(OTHER, query_vec, 5) == []

    with pytest.raises(NotFoundError):
        await repo.add_alias(OTHER, product_id=pipe.id, alias_text="x", normalized_text="x",
                             embedding=None, source="manual", confirmed=True)


async def test_unconfirmed_aliases_are_not_searched(repo):
    embedder = HashingEmbedder()
    pipe = await repo.create_product(BIZ, NewProduct(name="uPVC Pipe 63mm"))
    [vec] = await embedder.embed(["guess"])
    await repo.add_alias(BIZ, product_id=pipe.id, alias_text="guess", normalized_text="guess",
                         embedding=vec, source="agent_linked", confirmed=False)
    assert await repo.find_aliases_exact(BIZ, "guess") == []
    assert await repo.search_aliases(BIZ, vec, 5) == []


async def test_invoice_lifecycle(repo):
    supplier = await repo.create_supplier(BIZ, NewSupplier(name="Lanka Pipe Traders"))
    new = NewInvoice(
        supplier_id=supplier.id,
        invoice_number="INV-1001",
        invoice_date=date(2026, 10, 20),
        total=6750.0,
        source="agent",
        math_ok=True,
        math_issues=[],
        missing_fields=["total"],
        low_confidence_fields=["invoice.lines[0].quantity"],
    )
    created = await repo.create_invoice(BIZ, new, lines(("63mm uPVC pipe", 10, 585.0), ("Solvent cement", 2, 450.0)))
    assert created.status == "draft" and created.payment_status == "unpaid"
    assert [l.item_name for l in created.lines] == ["63mm uPVC pipe", "Solvent cement"]
    assert created.lines[0].unit_rate == 585.0 and created.lines[0].quantity == 10.0
    assert created.low_confidence_fields == ["invoice.lines[0].quantity"]
    assert created.invoice_date == date(2026, 10, 20)

    fetched = await repo.get_invoice(BIZ, created.id)
    assert fetched.model_dump(exclude={"created_at", "updated_at"}) == created.model_dump(exclude={"created_at", "updated_at"})
    assert await repo.get_invoice(OTHER, created.id) is None

    with pytest.raises(DuplicateError):
        await repo.create_invoice(BIZ, NewInvoice(supplier_id=supplier.id, invoice_number="INV-1001"), [])
    found = await repo.find_invoice_by_number(BIZ, supplier.id, "INV-1001")
    assert found.id == created.id
    assert await repo.find_invoice_by_number(BIZ, supplier.id, "INV-1001", exclude_id=created.id) is None

    updated = await repo.update_invoice(
        BIZ,
        created.id,
        {"total": 7000.0, "math_ok": False, "math_issues": [{"code": "invoice_total_mismatch"}]},
        lines(("63mm uPVC pipe", 12, 585.0)),
    )
    assert updated.total == 7000.0 and updated.math_ok is False
    assert updated.math_issues == [{"code": "invoice_total_mismatch"}]
    assert [(l.item_name, l.quantity) for l in updated.lines] == [("63mm uPVC pipe", 12.0)]

    kept = await repo.update_invoice(BIZ, created.id, {"status": "verified"})
    assert kept.status == "verified" and len(kept.lines) == 1  # lines untouched when not passed

    with pytest.raises(RepositoryError):
        await repo.update_invoice(BIZ, created.id, {"business_id": OTHER})
    with pytest.raises(NotFoundError):
        await repo.update_invoice(OTHER, created.id, {"total": 1.0})


async def test_invoice_without_supplier_or_number(repo):
    first = await repo.create_invoice(BIZ, NewInvoice(supplier_name_raw="Unknown Co"), lines(("Nails", 1, 10.0)))
    second = await repo.create_invoice(BIZ, NewInvoice(supplier_name_raw="Unknown Co"), [])
    assert first.supplier_id is None and second.lines == []
    with pytest.raises(NotFoundError):
        await repo.create_invoice(BIZ, NewInvoice(supplier_id=MISSING), [])


async def test_failed_update_changes_nothing(repo):
    """Header + lines are replaced in one transaction: a bad line leaves the old invoice intact."""
    supplier = await repo.create_supplier(BIZ, NewSupplier(name="Lanka Pipe Traders"))
    created = await repo.create_invoice(BIZ, NewInvoice(supplier_id=supplier.id, total=5850.0),
                                        lines(("63mm uPVC pipe", 10, 585.0)))
    bad_line = NewInvoiceLine(item_name="Ghost", product_id=MISSING)
    with pytest.raises(NotFoundError):
        await repo.update_invoice(BIZ, created.id, {"total": 1.0}, [bad_line])
    after = await repo.get_invoice(BIZ, created.id)
    assert after.total == 5850.0
    assert [l.item_name for l in after.lines] == ["63mm uPVC pipe"]


async def test_line_matches(repo):
    pipe = await repo.create_product(BIZ, NewProduct(name="uPVC Pipe 63mm"))
    created = await repo.create_invoice(BIZ, NewInvoice(), lines(("63mm upvc pipe", 1, 585.0)))
    line = created.lines[0]
    assert line.match_status == "unmatched"
    await repo.update_line_match(
        BIZ,
        created.id,
        line.id,
        LineMatchUpdate(product_id=pipe.id, match_status="linked", match_similarity=0.91,
                        match_candidates=[{"product_id": pipe.id, "similarity": 0.91}], match_reason="ok"),
    )
    [linked] = (await repo.get_invoice(BIZ, created.id)).lines
    assert (linked.product_id, linked.match_status, linked.match_reason) == (pipe.id, "linked", "ok")
    assert linked.match_similarity == pytest.approx(0.91)
    assert linked.match_candidates == [{"product_id": pipe.id, "similarity": 0.91}]
    with pytest.raises(NotFoundError):
        await repo.update_line_match(OTHER, created.id, line.id, LineMatchUpdate(match_status="pending"))
    with pytest.raises(NotFoundError):
        await repo.update_line_match(BIZ, created.id, MISSING, LineMatchUpdate(match_status="pending"))


async def test_list_invoices_filters(repo):
    a = await repo.create_supplier(BIZ, NewSupplier(name="A"))
    b = await repo.create_supplier(BIZ, NewSupplier(name="B"))
    await repo.create_invoice(BIZ, NewInvoice(supplier_id=a.id, invoice_number="A-1", invoice_date=date(2026, 1, 5)), [])
    await repo.create_invoice(BIZ, NewInvoice(supplier_id=a.id, invoice_number="A-2", invoice_date=date(2026, 3, 5), status="verified"), [])
    await repo.create_invoice(BIZ, NewInvoice(supplier_id=b.id, invoice_number="B-1", invoice_date=None), [])
    await repo.create_invoice(OTHER, NewInvoice(invoice_number="X-1"), [])

    def numbers(rows):
        return [r.invoice_number for r in rows]

    assert numbers(await repo.list_invoices(BIZ, InvoiceQuery())) == ["A-2", "A-1", "B-1"]
    assert numbers(await repo.list_invoices(BIZ, InvoiceQuery(supplier_id=a.id))) == ["A-2", "A-1"]
    assert numbers(await repo.list_invoices(BIZ, InvoiceQuery(status="verified"))) == ["A-2"]
    assert numbers(await repo.list_invoices(BIZ, InvoiceQuery(invoice_number="b-"))) == ["B-1"]
    assert numbers(await repo.list_invoices(BIZ, InvoiceQuery(date_from=date(2026, 2, 1)))) == ["A-2"]
    assert numbers(await repo.list_invoices(BIZ, InvoiceQuery(date_to=date(2026, 2, 1)))) == ["A-1"]
    assert numbers(await repo.list_invoices(BIZ, InvoiceQuery(limit=1))) == ["A-2"]
    assert all(r.lines == [] for r in await repo.list_invoices(BIZ, InvoiceQuery()))


async def test_logs(repo):
    created = await repo.create_invoice(BIZ, NewInvoice(), [])
    await repo.log_extraction_run(BIZ, ExtractionRunLog(invoice_id=created.id, agent="InvoiceVisionAgent",
                                                        model="gpt-4o", guardrail_passed=True,
                                                        raw_output={"doc_type": "invoice"}, duration_ms=12))
    await repo.log_event(BIZ, created.id, "invoice_extracted", {"math_ok": True})
    await repo.log_event(BIZ, created.id, "invoice_verified", {"due_date": "2026-11-19"})
    events = await repo.list_events(BIZ, created.id)
    assert [e.event_type for e in events] == ["invoice_extracted", "invoice_verified"]
    assert events[0].payload == {"math_ok": True}
    assert await repo.list_events(OTHER, created.id) == []
