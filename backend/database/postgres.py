"""Repository backed by Supabase Postgres through a direct connection string (psycopg 3).

Use the Supabase *pooler* connection string on Render (Settings -> Database -> Connection string).
Prepared statements are disabled so the transaction pooler (port 6543) works.

psycopg's synchronous pool is used and each call runs in a worker thread: psycopg's async mode
cannot run on Windows' default event loop, and the team develops on Windows.
Every method runs in one transaction, so an invoice header and its lines change together.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

import psycopg
from psycopg import errors as pg_errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg.types.string import TextLoader
from psycopg_pool import ConnectionPool

from .models import (
    INVOICE_UPDATABLE_COLUMNS,
    SUPPLIER_UPDATABLE_COLUMNS,
    AliasMatch,
    AliasRecord,
    AuditEvent,
    ExtractionRunLog,
    InvoiceLineRecord,
    InvoiceQuery,
    InvoiceRecord,
    LineMatchUpdate,
    NewInvoice,
    NewInvoiceLine,
    NewProduct,
    NewSupplier,
    ProductRecord,
    SupplierRecord,
)
from .repository import DuplicateError, NotFoundError, RepositoryError, check_columns

T = TypeVar("T")
Conn = psycopg.Connection

_JSON_COLUMNS = frozenset(
    {"math_issues", "field_issues", "missing_fields", "low_confidence_fields", "match_candidates"}
)

_SUPPLIER_COLS = "id, business_id, name, credit_days, phone1, phone2, email, address, created_at, updated_at"
_PRODUCT_COLS = "id, business_id, name, sku, unit, created_at"
_INVOICE_COLS = (
    "id, business_id, supplier_id, supplier_name_raw, invoice_number, invoice_date, delivery_date, "
    "due_date, payment_type, credit_days, special_discount, tax, total, status, source, math_ok, "
    "math_issues, field_issues, missing_fields, low_confidence_fields, payment_status, image_path, "
    "verified_at, created_at, updated_at"
)
_LINE_COLS = (
    "id, invoice_id, sort_order, sku, item_name, quantity, mro, discount, line_total, product_id, "
    "suggested_product_id, match_status, match_similarity, match_candidates, match_reason"
)


def _vector(values: Sequence[float]) -> str:
    return "[" + ",".join(f"{value:.8g}" for value in values) + "]"


def _param(column: str, value: Any) -> Any:
    return Jsonb(value) if column in _JSON_COLUMNS and value is not None else value


def _translate(exc: Exception) -> Exception:
    message = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    if isinstance(exc, pg_errors.UniqueViolation):
        return DuplicateError(message)
    if isinstance(exc, pg_errors.ForeignKeyViolation):
        return NotFoundError(message)
    if isinstance(exc, (pg_errors.IntegrityError, pg_errors.DataError)):
        return RepositoryError(message)
    return exc


def _configure(conn: Conn) -> None:
    conn.adapters.register_loader("uuid", TextLoader)  # ids travel as text everywhere


def _fetchone(conn: Conn, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    return conn.execute(sql, params).fetchone()


def _fetchall(conn: Conn, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    return list(conn.execute(sql, params).fetchall())


def _insert_lines(conn: Conn, invoice_id: str, lines: Sequence[NewInvoiceLine]) -> None:
    for line in lines:
        fields = line.model_dump()  # serialised by alias: the unit rate column is "mro"
        columns = ", ".join(fields)
        placeholders = ", ".join(["%s"] * len(fields))
        conn.execute(
            f"insert into invoice_items (invoice_id, {columns}) values (%s, {placeholders})",
            (invoice_id, *(_param(column, value) for column, value in fields.items())),
        )


def _load_invoice(conn: Conn, business_id: str, invoice_id: str) -> InvoiceRecord | None:
    row = _fetchone(
        conn,
        f"select {_INVOICE_COLS} from invoices where business_id = %s and id = %s",
        (business_id, invoice_id),
    )
    if row is None:
        return None
    line_rows = _fetchall(
        conn,
        f"select {_LINE_COLS} from invoice_items where invoice_id = %s order by sort_order, id",
        (invoice_id,),
    )
    lines = [InvoiceLineRecord.model_validate(line) for line in line_rows]
    return InvoiceRecord.model_validate({**row, "lines": lines})


class PostgresRepository:
    kind = "postgres"

    def __init__(self, dsn: str, *, max_size: int = 5) -> None:
        self._pool = ConnectionPool(
            dsn,
            min_size=1,
            max_size=max_size,
            open=False,
            kwargs={"prepare_threshold": None, "row_factory": dict_row, "autocommit": False},
            configure=_configure,
        )

    async def open(self) -> None:
        await asyncio.to_thread(self._pool.open, True, 30)

    async def close(self) -> None:
        await asyncio.to_thread(self._pool.close)

    def _in_tx(self, work: Callable[[Conn], T]) -> T:
        with self._pool.connection() as conn:
            try:
                with conn.transaction():
                    return work(conn)
            except psycopg.Error as exc:
                raise _translate(exc) from exc

    async def _run(self, work: Callable[[Conn], T]) -> T:
        return await asyncio.to_thread(self._in_tx, work)

    # suppliers -------------------------------------------------------------------------------
    async def list_suppliers(self, business_id: str) -> list[SupplierRecord]:
        rows = await self._run(
            lambda c: _fetchall(
                c,
                f"select {_SUPPLIER_COLS} from suppliers where business_id = %s order by lower(name)",
                (business_id,),
            )
        )
        return [SupplierRecord.model_validate(row) for row in rows]

    async def get_supplier(self, business_id: str, supplier_id: str) -> SupplierRecord | None:
        row = await self._run(
            lambda c: _fetchone(
                c,
                f"select {_SUPPLIER_COLS} from suppliers where business_id = %s and id = %s",
                (business_id, supplier_id),
            )
        )
        return SupplierRecord.model_validate(row) if row else None

    async def find_suppliers_by_name(self, business_id: str, name: str) -> list[SupplierRecord]:
        rows = await self._run(
            lambda c: _fetchall(
                c,
                f"select {_SUPPLIER_COLS} from suppliers "
                "where business_id = %s and lower(trim(name)) = lower(trim(%s)) order by created_at",
                (business_id, name),
            )
        )
        return [SupplierRecord.model_validate(row) for row in rows]

    async def create_supplier(self, business_id: str, data: NewSupplier) -> SupplierRecord:
        fields = data.model_dump()
        columns = ", ".join(fields)
        placeholders = ", ".join(["%s"] * len(fields))
        row = await self._run(
            lambda c: _fetchone(
                c,
                f"insert into suppliers (business_id, {columns}) values (%s, {placeholders}) "
                f"returning {_SUPPLIER_COLS}",
                (business_id, *fields.values()),
            )
        )
        assert row is not None
        return SupplierRecord.model_validate(row)

    async def update_supplier(
        self, business_id: str, supplier_id: str, changes: dict[str, Any]
    ) -> SupplierRecord:
        check_columns(changes, SUPPLIER_UPDATABLE_COLUMNS)
        assignments = "".join(f"{column} = %s, " for column in changes)
        row = await self._run(
            lambda c: _fetchone(
                c,
                f"update suppliers set {assignments}updated_at = now() "
                f"where business_id = %s and id = %s returning {_SUPPLIER_COLS}",
                (*changes.values(), business_id, supplier_id),
            )
        )
        if row is None:
            raise NotFoundError("supplier not found")
        return SupplierRecord.model_validate(row)

    # products and aliases --------------------------------------------------------------------
    async def list_products(self, business_id: str) -> list[ProductRecord]:
        rows = await self._run(
            lambda c: _fetchall(
                c,
                f"select {_PRODUCT_COLS} from products where business_id = %s order by lower(name)",
                (business_id,),
            )
        )
        return [ProductRecord.model_validate(row) for row in rows]

    async def get_product(self, business_id: str, product_id: str) -> ProductRecord | None:
        row = await self._run(
            lambda c: _fetchone(
                c,
                f"select {_PRODUCT_COLS} from products where business_id = %s and id = %s",
                (business_id, product_id),
            )
        )
        return ProductRecord.model_validate(row) if row else None

    async def create_product(self, business_id: str, data: NewProduct) -> ProductRecord:
        row = await self._run(
            lambda c: _fetchone(
                c,
                "insert into products (business_id, name, sku, unit) values (%s, %s, %s, %s) "
                f"returning {_PRODUCT_COLS}",
                (business_id, data.name, data.sku, data.unit),
            )
        )
        assert row is not None
        return ProductRecord.model_validate(row)

    async def has_products(self, business_id: str) -> bool:
        row = await self._run(
            lambda c: _fetchone(
                c,
                "select exists (select 1 from products where business_id = %s) as found",
                (business_id,),
            )
        )
        return bool(row and row["found"])

    async def products_without_aliases(self, business_id: str) -> list[ProductRecord]:
        rows = await self._run(
            lambda c: _fetchall(
                c,
                f"select {_PRODUCT_COLS} from products p where business_id = %s and not exists "
                "(select 1 from product_aliases a where a.product_id = p.id) order by created_at",
                (business_id,),
            )
        )
        return [ProductRecord.model_validate(row) for row in rows]

    async def aliases_without_embeddings(self, business_id: str) -> list[AliasRecord]:
        rows = await self._run(
            lambda c: _fetchall(
                c,
                "select id, product_id, alias_text, normalized_text, source, confirmed "
                "from product_aliases where business_id = %s and embedding is null order by created_at",
                (business_id,),
            )
        )
        return [AliasRecord.model_validate(row) for row in rows]

    async def set_alias_embedding(
        self, business_id: str, alias_id: str, embedding: Sequence[float]
    ) -> None:
        await self._run(
            lambda c: c.execute(
                "update product_aliases set embedding = %s where business_id = %s and id = %s",
                (_vector(embedding), business_id, alias_id),
            )
        )

    async def add_alias(
        self,
        business_id: str,
        *,
        product_id: str,
        alias_text: str,
        normalized_text: str,
        embedding: Sequence[float] | None,
        source: str,
        confirmed: bool,
    ) -> None:
        def work(c: Conn) -> None:
            if _fetchone(c, "select 1 from products where business_id = %s and id = %s", (business_id, product_id)) is None:
                raise NotFoundError("product not found")
            c.execute(
                "insert into product_aliases "
                "(business_id, product_id, alias_text, normalized_text, embedding, source, confirmed) "
                "values (%s, %s, %s, %s, %s, %s, %s) "
                "on conflict (business_id, product_id, normalized_text) do update set "
                "confirmed = product_aliases.confirmed or excluded.confirmed, "
                "embedding = coalesce(excluded.embedding, product_aliases.embedding)",
                (
                    business_id,
                    product_id,
                    alias_text,
                    normalized_text,
                    None if embedding is None else _vector(embedding),
                    source,
                    confirmed,
                ),
            )

        await self._run(work)

    async def find_aliases_exact(self, business_id: str, normalized_text: str) -> list[AliasMatch]:
        rows = await self._run(
            lambda c: _fetchall(
                c,
                "select p.id as product_id, p.name as product_name, p.sku as product_sku, "
                "a.alias_text, 1.0::float8 as similarity "
                "from product_aliases a join products p on p.id = a.product_id "
                "where a.business_id = %s and a.confirmed and a.normalized_text = %s",
                (business_id, normalized_text),
            )
        )
        return [AliasMatch.model_validate(row) for row in rows]

    async def search_aliases(
        self, business_id: str, embedding: Sequence[float], limit: int
    ) -> list[AliasMatch]:
        rows = await self._run(
            lambda c: _fetchall(
                c,
                "select product_id, product_name, product_sku, alias_text, similarity "
                "from match_product_aliases(%s, %s, %s)",
                (business_id, _vector(embedding), limit),
            )
        )
        return [AliasMatch.model_validate(row) for row in rows]

    # invoices --------------------------------------------------------------------------------
    async def create_invoice(
        self, business_id: str, invoice: NewInvoice, lines: Sequence[NewInvoiceLine]
    ) -> InvoiceRecord:
        fields = invoice.model_dump()
        columns = ", ".join(fields)
        placeholders = ", ".join(["%s"] * len(fields))

        def work(c: Conn) -> InvoiceRecord | None:
            if invoice.supplier_id and _fetchone(
                c, "select 1 from suppliers where business_id = %s and id = %s", (business_id, invoice.supplier_id)
            ) is None:
                raise NotFoundError("supplier not found")
            row = _fetchone(
                c,
                f"insert into invoices (business_id, {columns}) values (%s, {placeholders}) returning id",
                (business_id, *(_param(column, value) for column, value in fields.items())),
            )
            assert row is not None
            _insert_lines(c, row["id"], lines)
            return _load_invoice(c, business_id, row["id"])

        record = await self._run(work)
        assert record is not None
        return record

    async def get_invoice(self, business_id: str, invoice_id: str) -> InvoiceRecord | None:
        return await self._run(lambda c: _load_invoice(c, business_id, invoice_id))

    async def list_invoices(self, business_id: str, query: InvoiceQuery) -> list[InvoiceRecord]:
        clauses = ["business_id = %s"]
        params: list[Any] = [business_id]
        if query.supplier_id:
            clauses.append("supplier_id = %s")
            params.append(query.supplier_id)
        if query.status:
            clauses.append("status = %s")
            params.append(query.status)
        if query.invoice_number:
            clauses.append("invoice_number ilike %s")
            params.append(f"%{query.invoice_number}%")
        if query.date_from:
            clauses.append("invoice_date >= %s")
            params.append(query.date_from)
        if query.date_to:
            clauses.append("invoice_date <= %s")
            params.append(query.date_to)
        params.append(query.limit)
        rows = await self._run(
            lambda c: _fetchall(
                c,
                f"select {_INVOICE_COLS} from invoices where {' and '.join(clauses)} "
                "order by invoice_date desc nulls last, created_at desc limit %s",
                params,
            )
        )
        return [InvoiceRecord.model_validate(row) for row in rows]

    async def find_invoice_by_number(
        self,
        business_id: str,
        supplier_id: str,
        invoice_number: str,
        *,
        exclude_id: str | None = None,
    ) -> InvoiceRecord | None:
        sql = (
            f"select {_INVOICE_COLS} from invoices "
            "where business_id = %s and supplier_id = %s and invoice_number = %s"
        )
        params: list[Any] = [business_id, supplier_id, invoice_number]
        if exclude_id:
            sql += " and id <> %s"
            params.append(exclude_id)
        row = await self._run(lambda c: _fetchone(c, sql + " limit 1", params))
        return InvoiceRecord.model_validate(row) if row else None

    async def update_invoice(
        self,
        business_id: str,
        invoice_id: str,
        changes: dict[str, Any],
        lines: Sequence[NewInvoiceLine] | None = None,
    ) -> InvoiceRecord:
        check_columns(changes, INVOICE_UPDATABLE_COLUMNS)
        assignments = "".join(f"{column} = %s, " for column in changes)

        def work(c: Conn) -> InvoiceRecord | None:
            row = _fetchone(
                c,
                f"update invoices set {assignments}updated_at = now() "
                "where business_id = %s and id = %s returning id",
                (*(_param(column, value) for column, value in changes.items()), business_id, invoice_id),
            )
            if row is None:
                raise NotFoundError("invoice not found")
            if lines is not None:
                c.execute("delete from invoice_items where invoice_id = %s", (invoice_id,))
                _insert_lines(c, invoice_id, lines)
            return _load_invoice(c, business_id, invoice_id)

        record = await self._run(work)
        assert record is not None
        return record

    async def update_line_match(
        self, business_id: str, invoice_id: str, line_id: str, match: LineMatchUpdate
    ) -> None:
        fields = match.model_dump()
        assignments = ", ".join(f"{column} = %s" for column in fields)
        row = await self._run(
            lambda c: _fetchone(
                c,
                f"update invoice_items set {assignments} where id = %s and invoice_id = "
                "(select id from invoices where business_id = %s and id = %s) returning id",
                (
                    *(_param(column, value) for column, value in fields.items()),
                    line_id,
                    business_id,
                    invoice_id,
                ),
            )
        )
        if row is None:
            raise NotFoundError("invoice line not found")

    # logs ------------------------------------------------------------------------------------
    async def log_extraction_run(self, business_id: str, run: ExtractionRunLog) -> None:
        await self._run(
            lambda c: c.execute(
                "insert into extraction_runs "
                "(business_id, invoice_id, agent, model, guardrail_passed, raw_output, error, duration_ms) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    business_id,
                    run.invoice_id,
                    run.agent,
                    run.model,
                    run.guardrail_passed,
                    None if run.raw_output is None else Jsonb(run.raw_output),
                    run.error,
                    run.duration_ms,
                ),
            )
        )

    async def log_event(
        self, business_id: str, invoice_id: str | None, event_type: str, payload: dict[str, Any]
    ) -> None:
        await self._run(
            lambda c: c.execute(
                "insert into audit_log (business_id, invoice_id, event_type, payload) "
                "values (%s, %s, %s, %s)",
                (business_id, invoice_id, event_type, Jsonb(payload)),
            )
        )

    async def list_events(self, business_id: str, invoice_id: str) -> list[AuditEvent]:
        rows = await self._run(
            lambda c: _fetchall(
                c,
                "select id, invoice_id, event_type, payload, created_at from audit_log "
                "where business_id = %s and invoice_id = %s order by created_at, id",
                (business_id, invoice_id),
            )
        )
        return [AuditEvent.model_validate(row) for row in rows]
