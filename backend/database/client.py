"""Everything the backend uses to read and write Supabase (proposal: database/client.py).

1. Record models   the shape of each row the API reads or writes (suppliers, products, invoices...).
2. ``Database``    all SQL against Supabase Postgres, through psycopg 3. Each method runs in one
                   transaction, so an invoice header and its lines always change together.
                   psycopg's synchronous pool is used and every call runs in a worker thread:
                   psycopg's async mode cannot run on Windows' default event loop.
3. ``SupabaseStorage``  the private bucket for invoice photos, with short-lived signed links.

Every query filters on ``business_id``: one shop never sees another shop's rows.
The tables themselves are created by the SQL files in the repo-root ``database/`` folder.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Annotated, Any, Literal, TypeVar
from urllib.parse import quote

import httpx
import psycopg
from psycopg import errors as pg_errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg.types.string import TextLoader
from psycopg_pool import ConnectionPool
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

# =============================================================================================
# 1. Record models
# =============================================================================================

InvoiceStatus = Literal["draft", "verified", "rejected"]
MatchStatus = Literal["unmatched", "linked", "pending", "new_product"]
PaymentStatus = Literal["unpaid", "paid", "unconfirmed"]


def _uuid_text(value: str) -> str:
    """Check that an id is a UUID and return it as text."""
    return str(uuid.UUID(value))


# An id that must be a UUID, kept as text (ids are compared as strings everywhere).
UUIDStr = Annotated[str, AfterValidator(_uuid_text)]

# A required name: surrounding spaces are removed and a blank name is refused.
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

# Credit period in days: 0 to 10 years.
CreditDays = Annotated[int, Field(ge=0, le=3650)]


class NewSupplier(BaseModel):
    """A supplier as the owner enters it."""

    name: Name
    credit_days: CreditDays | None = None
    phone1: str | None = None
    phone2: str | None = None
    email: str | None = None
    address: str | None = None


class SupplierRecord(NewSupplier):
    """A saved supplier."""

    id: str
    business_id: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NewProduct(BaseModel):
    """A master-catalogue product as the owner enters it."""

    name: Name
    sku: str | None = None
    unit: str | None = None


class ProductRecord(NewProduct):
    """A saved master-catalogue product."""

    id: str
    business_id: str
    created_at: datetime | None = None


class AliasRecord(BaseModel):
    """A printed description mapped to a product (product_aliases row)."""

    id: str
    product_id: str
    alias_text: str
    normalized_text: str
    source: str
    confirmed: bool


class AliasMatch(BaseModel):
    """One product found by an alias search, with its similarity (1.0 = exact)."""

    product_id: str
    product_name: str
    product_sku: str | None = None
    alias_text: str
    similarity: float


class NewInvoiceLine(BaseModel):
    """An invoice line to save. The unit-rate column is called "mro" (as on the paper)."""

    # "mro" as a field name would shadow BaseModel.mro, so the field is unit_rate with alias "mro".
    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True, serialize_by_alias=True)

    sort_order: int = 0
    sku: str | None = None
    item_name: str
    quantity: float | None = None
    unit_rate: float | None = Field(None, alias="mro", description="Unit rate in rupees.")
    discount: float = 0.0
    line_total: float | None = None
    product_id: str | None = None
    suggested_product_id: str | None = None
    match_status: MatchStatus = "unmatched"
    match_similarity: float | None = None
    match_candidates: list[dict[str, Any]] = Field(default_factory=list)
    match_reason: str | None = None


class InvoiceLineRecord(NewInvoiceLine):
    """A saved invoice line, with the linked product's name."""

    id: str
    invoice_id: str
    product_name: str | None = None


class NewInvoice(BaseModel):
    """An invoice header to save."""

    supplier_id: str | None = None
    supplier_name_raw: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    delivery_date: date | None = None
    due_date: date | None = None
    payment_type: Literal["cash", "credit"] | None = None
    credit_days: int | None = None
    special_discount: float = 0.0
    tax: float = 0.0
    total: float | None = None
    status: InvoiceStatus = "draft"
    source: Literal["manual", "agent"] = "manual"
    math_ok: bool | None = None
    math_issues: list[dict[str, Any]] = Field(default_factory=list)
    field_issues: list[dict[str, Any]] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    low_confidence_fields: list[str] = Field(default_factory=list)
    payment_status: PaymentStatus = "unpaid"
    image_path: str | None = None


class InvoiceSummary(NewInvoice):
    """A saved invoice without its lines (list rows)."""

    id: str
    business_id: str
    supplier_name: str | None = None
    verified_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Invoice(InvoiceSummary):
    """A saved invoice with its lines; ``image_url`` is filled in by the API (signed link)."""

    image_url: str | None = None
    lines: list[InvoiceLineRecord] = Field(default_factory=list)


class InvoiceQuery(BaseModel):
    """Filters for the invoice search."""

    supplier_id: str | None = None
    status: InvoiceStatus | None = None
    payment_status: PaymentStatus | None = None
    invoice_number: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    limit: int = Field(100, ge=1, le=500)


class SupplierLine(BaseModel):
    """One line of a verified invoice, as shown on the Supplier Profile."""

    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True, serialize_by_alias=True)

    invoice_id: str
    invoice_number: str | None = None
    invoice_date: date | None = None
    item_name: str
    product_id: str | None = None
    product_name: str | None = None
    quantity: float | None = None
    unit_rate: float | None = Field(None, alias="mro", description="Unit rate in rupees.")
    line_total: float | None = None


class LineMatchUpdate(BaseModel):
    """The product-match result to store on one invoice line."""

    product_id: str | None = None
    suggested_product_id: str | None = None
    match_status: MatchStatus
    match_similarity: float | None = None
    match_candidates: list[dict[str, Any]] = Field(default_factory=list)
    match_reason: str | None = None


class ExtractionRunLog(BaseModel):
    """One raw agent output, kept so nothing has to be sent to OpenAI twice."""

    invoice_id: str | None = None
    agent: str
    model: str | None = None
    guardrail_passed: bool | None = None
    raw_output: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int | None = None


class AuditEvent(BaseModel):
    """One correction, confirmation or verification in the audit log."""

    id: str
    invoice_id: str | None
    event_type: str
    payload: dict[str, Any]
    created_at: datetime | None = None


# Columns callers may change through Database.update_invoice / update_supplier.
INVOICE_UPDATABLE_COLUMNS = frozenset(
    {
        "supplier_id", "supplier_name_raw", "invoice_number", "invoice_date", "delivery_date",
        "due_date", "payment_type", "credit_days", "special_discount", "tax", "total", "status",
        "math_ok", "math_issues", "field_issues", "missing_fields", "low_confidence_fields",
        "payment_status", "image_path", "verified_at",
    }
)
SUPPLIER_UPDATABLE_COLUMNS = frozenset({"name", "credit_days", "phone1", "phone2", "email", "address"})


class RepositoryError(Exception):
    """A database rule was broken (bad column, check constraint...)."""


class DuplicateError(RepositoryError):
    """A unique rule was broken (same product name, same supplier invoice number...)."""


class NotFoundError(RepositoryError):
    """The row does not exist, or belongs to another business."""


# =============================================================================================
# 2. Database (Supabase Postgres)
# =============================================================================================

T = TypeVar("T")
Conn = psycopg.Connection

_JSON_COLUMNS = frozenset(
    {"math_issues", "field_issues", "missing_fields", "low_confidence_fields", "match_candidates"}
)
_SUPPLIER_COLS = "id, business_id, name, credit_days, phone1, phone2, email, address, created_at, updated_at"
_PRODUCT_COLS = "id, business_id, name, sku, unit, created_at"
_INVOICE_FIELDS = (
    "id", "business_id", "supplier_id", "supplier_name_raw", "invoice_number", "invoice_date",
    "delivery_date", "due_date", "payment_type", "credit_days", "special_discount", "tax", "total",
    "status", "source", "math_ok", "math_issues", "field_issues", "missing_fields",
    "low_confidence_fields", "payment_status", "image_path", "verified_at", "created_at", "updated_at",
)
_LINE_FIELDS = (
    "id", "invoice_id", "sort_order", "sku", "item_name", "quantity", "mro", "discount", "line_total",
    "product_id", "suggested_product_id", "match_status", "match_similarity", "match_candidates",
    "match_reason",
)
# Invoice columns plus the supplier's name from the suppliers table.
_INVOICE_SELECT = (
    "select " + ", ".join(f"i.{name}" for name in _INVOICE_FIELDS) + ", s.name as supplier_name "
    "from invoices i left join suppliers s on s.id = i.supplier_id "
)


def check_columns(changes: dict[str, Any], allowed: frozenset[str]) -> None:
    """Refuse updates to columns that callers must not change."""
    unknown = set(changes) - allowed
    if unknown:
        raise RepositoryError(f"cannot update column(s): {', '.join(sorted(unknown))}")


def _vector(values: Sequence[float]) -> str:
    """Write an embedding the way pgvector reads it: "[0.1,0.2,...]"."""
    return "[" + ",".join(f"{value:.8g}" for value in values) + "]"


def _param(column: str, value: Any) -> Any:
    """Wrap JSON columns so psycopg sends them as jsonb."""
    return Jsonb(value) if column in _JSON_COLUMNS and value is not None else value


def _translate(exc: Exception) -> Exception:
    """Turn Postgres errors into the API's DuplicateError / NotFoundError / RepositoryError."""
    message = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    if isinstance(exc, pg_errors.UniqueViolation):
        return DuplicateError(message)
    if isinstance(exc, pg_errors.ForeignKeyViolation):
        return NotFoundError(message)
    if isinstance(exc, (pg_errors.IntegrityError, pg_errors.DataError)):
        return RepositoryError(message)
    return exc


def _configure(conn: Conn) -> None:
    """Return uuid columns as text, so ids are plain strings everywhere."""
    conn.adapters.register_loader("uuid", TextLoader)


def _fetchone(conn: Conn, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    """Run a query and return its first row, or None."""
    return conn.execute(sql, params).fetchone()


def _fetchall(conn: Conn, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    """Run a query and return all rows."""
    return list(conn.execute(sql, params).fetchall())


def _insert_lines(conn: Conn, invoice_id: str, lines: Sequence[NewInvoiceLine]) -> None:
    """Insert an invoice's lines (inside the caller's transaction)."""
    for line in lines:
        fields = line.model_dump()  # by alias: the unit-rate column is "mro"
        columns = ", ".join(fields)
        placeholders = ", ".join(["%s"] * len(fields))
        conn.execute(
            f"insert into invoice_items (invoice_id, {columns}) values (%s, {placeholders})",
            (invoice_id, *(_param(column, value) for column, value in fields.items())),
        )


def _load_invoice(conn: Conn, business_id: str, invoice_id: str) -> Invoice | None:
    """Read one invoice with its supplier name, lines and linked product names."""
    row = _fetchone(conn, _INVOICE_SELECT + "where i.business_id = %s and i.id = %s", (business_id, invoice_id))
    if row is None:
        return None
    line_rows = _fetchall(
        conn,
        "select " + ", ".join(f"l.{name}" for name in _LINE_FIELDS) + ", p.name as product_name "
        "from invoice_items l left join products p on p.id = l.product_id "
        "where l.invoice_id = %s order by l.sort_order, l.id",
        (invoice_id,),
    )
    lines = [InvoiceLineRecord.model_validate(line) for line in line_rows]
    return Invoice.model_validate({**row, "lines": lines})


class Database:
    """All reads and writes against Supabase Postgres."""

    def __init__(self, dsn: str, *, max_size: int = 5) -> None:
        """Prepare a connection pool. Prepared statements are off so Supabase's
        transaction pooler (port 6543) works."""
        self._pool = ConnectionPool(
            dsn,
            min_size=1,
            max_size=max_size,
            open=False,
            kwargs={"prepare_threshold": None, "row_factory": dict_row, "autocommit": False},
            configure=_configure,
        )

    async def open(self) -> None:
        """Open the pool (fails fast if the database cannot be reached)."""
        await asyncio.to_thread(self._pool.open, True, 30)

    async def close(self) -> None:
        """Close every pooled connection."""
        await asyncio.to_thread(self._pool.close)

    def _in_tx(self, work: Callable[[Conn], T]) -> T:
        """Run ``work`` in one transaction and translate database errors."""
        with self._pool.connection() as conn:
            try:
                with conn.transaction():
                    return work(conn)
            except psycopg.Error as exc:
                raise _translate(exc) from exc

    async def _run(self, work: Callable[[Conn], T]) -> T:
        """Run a transaction in a worker thread so the API stays responsive."""
        return await asyncio.to_thread(self._in_tx, work)

    # ---- suppliers ---------------------------------------------------------------------------
    async def list_suppliers(self, business_id: str) -> list[SupplierRecord]:
        """All suppliers of the business, by name."""
        rows = await self._run(lambda c: _fetchall(
            c, f"select {_SUPPLIER_COLS} from suppliers where business_id = %s order by lower(name)", (business_id,)
        ))
        return [SupplierRecord.model_validate(row) for row in rows]

    async def get_supplier(self, business_id: str, supplier_id: str) -> SupplierRecord | None:
        """One supplier, or None."""
        row = await self._run(lambda c: _fetchone(
            c, f"select {_SUPPLIER_COLS} from suppliers where business_id = %s and id = %s", (business_id, supplier_id)
        ))
        return SupplierRecord.model_validate(row) if row else None

    async def find_suppliers_by_name(self, business_id: str, name: str) -> list[SupplierRecord]:
        """Suppliers whose name matches, ignoring case and outer spaces (oldest first)."""
        rows = await self._run(lambda c: _fetchall(
            c,
            f"select {_SUPPLIER_COLS} from suppliers "
            "where business_id = %s and lower(trim(name)) = lower(trim(%s)) order by created_at",
            (business_id, name),
        ))
        return [SupplierRecord.model_validate(row) for row in rows]

    async def create_supplier(self, business_id: str, data: NewSupplier) -> SupplierRecord:
        """Save a new supplier."""
        fields = data.model_dump()
        columns = ", ".join(fields)
        placeholders = ", ".join(["%s"] * len(fields))
        row = await self._run(lambda c: _fetchone(
            c,
            f"insert into suppliers (business_id, {columns}) values (%s, {placeholders}) returning {_SUPPLIER_COLS}",
            (business_id, *fields.values()),
        ))
        assert row is not None
        return SupplierRecord.model_validate(row)

    async def update_supplier(self, business_id: str, supplier_id: str, changes: dict[str, Any]) -> SupplierRecord:
        """Change some supplier fields (e.g. the credit days)."""
        check_columns(changes, SUPPLIER_UPDATABLE_COLUMNS)
        assignments = "".join(f"{column} = %s, " for column in changes)
        row = await self._run(lambda c: _fetchone(
            c,
            f"update suppliers set {assignments}updated_at = now() "
            f"where business_id = %s and id = %s returning {_SUPPLIER_COLS}",
            (*changes.values(), business_id, supplier_id),
        ))
        if row is None:
            raise NotFoundError("supplier not found")
        return SupplierRecord.model_validate(row)

    # ---- products and aliases ----------------------------------------------------------------
    async def list_products(self, business_id: str) -> list[ProductRecord]:
        """The master catalogue, by name."""
        rows = await self._run(lambda c: _fetchall(
            c, f"select {_PRODUCT_COLS} from products where business_id = %s order by lower(name)", (business_id,)
        ))
        return [ProductRecord.model_validate(row) for row in rows]

    async def get_product(self, business_id: str, product_id: str) -> ProductRecord | None:
        """One product, or None."""
        row = await self._run(lambda c: _fetchone(
            c, f"select {_PRODUCT_COLS} from products where business_id = %s and id = %s", (business_id, product_id)
        ))
        return ProductRecord.model_validate(row) if row else None

    async def create_product(self, business_id: str, data: NewProduct) -> ProductRecord:
        """Add a product to the catalogue (names are unique, ignoring case)."""
        row = await self._run(lambda c: _fetchone(
            c,
            f"insert into products (business_id, name, sku, unit) values (%s, %s, %s, %s) returning {_PRODUCT_COLS}",
            (business_id, data.name, data.sku, data.unit),
        ))
        assert row is not None
        return ProductRecord.model_validate(row)

    async def has_products(self, business_id: str) -> bool:
        """True when the catalogue has at least one product."""
        row = await self._run(lambda c: _fetchone(
            c, "select exists (select 1 from products where business_id = %s) as found", (business_id,)
        ))
        return bool(row and row["found"])

    async def products_without_aliases(self, business_id: str) -> list[ProductRecord]:
        """Products that have no alias yet (e.g. added by SQL or the data-entry app)."""
        rows = await self._run(lambda c: _fetchall(
            c,
            f"select {_PRODUCT_COLS} from products p where business_id = %s and not exists "
            "(select 1 from product_aliases a where a.product_id = p.id) order by created_at",
            (business_id,),
        ))
        return [ProductRecord.model_validate(row) for row in rows]

    async def aliases_without_embeddings(self, business_id: str) -> list[AliasRecord]:
        """Aliases still waiting for their embedding vector."""
        rows = await self._run(lambda c: _fetchall(
            c,
            "select id, product_id, alias_text, normalized_text, source, confirmed "
            "from product_aliases where business_id = %s and embedding is null order by created_at",
            (business_id,),
        ))
        return [AliasRecord.model_validate(row) for row in rows]

    async def set_alias_embedding(self, business_id: str, alias_id: str, embedding: Sequence[float]) -> None:
        """Store the embedding vector of one alias."""
        await self._run(lambda c: c.execute(
            "update product_aliases set embedding = %s where business_id = %s and id = %s",
            (_vector(embedding), business_id, alias_id),
        ))

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
        """Save a printed description for a product (updates it if it already exists)."""

        def work(c: Conn) -> None:
            """Check the product belongs to the business, then insert or update the alias."""
            if _fetchone(c, "select 1 from products where business_id = %s and id = %s", (business_id, product_id)) is None:
                raise NotFoundError("product not found")
            c.execute(
                "insert into product_aliases "
                "(business_id, product_id, alias_text, normalized_text, embedding, source, confirmed) "
                "values (%s, %s, %s, %s, %s, %s, %s) "
                "on conflict (business_id, product_id, normalized_text) do update set "
                "confirmed = product_aliases.confirmed or excluded.confirmed, "
                "embedding = coalesce(excluded.embedding, product_aliases.embedding)",
                (business_id, product_id, alias_text, normalized_text,
                 None if embedding is None else _vector(embedding), source, confirmed),
            )

        await self._run(work)

    async def retire_other_aliases(self, business_id: str, normalized_text: str, keep_product_id: str) -> None:
        """Stop using this printed text for any other product (the owner confirmed it for a new one).

        The rows stay (unconfirmed) for the audit trail; catalogue names are never touched.
        """
        await self._run(lambda c: c.execute(
            "update product_aliases set confirmed = false "
            "where business_id = %s and normalized_text = %s and product_id <> %s and source <> 'catalogue'",
            (business_id, normalized_text, keep_product_id),
        ))

    async def find_aliases_exact(self, business_id: str, normalized_text: str) -> list[AliasMatch]:
        """Confirmed aliases with exactly this normalised text (similarity 1.0)."""
        rows = await self._run(lambda c: _fetchall(
            c,
            "select p.id as product_id, p.name as product_name, p.sku as product_sku, "
            "a.alias_text, 1.0::float8 as similarity "
            "from product_aliases a join products p on p.id = a.product_id "
            "where a.business_id = %s and a.confirmed and a.normalized_text = %s",
            (business_id, normalized_text),
        ))
        return [AliasMatch.model_validate(row) for row in rows]

    async def search_aliases(self, business_id: str, embedding: Sequence[float], limit: int) -> list[AliasMatch]:
        """Confirmed aliases closest in meaning (pgvector cosine similarity), best first."""
        rows = await self._run(lambda c: _fetchall(
            c,
            "select product_id, product_name, product_sku, alias_text, similarity "
            "from match_product_aliases(%s, %s, %s)",
            (business_id, _vector(embedding), limit),
        ))
        return [AliasMatch.model_validate(row) for row in rows]

    # ---- invoices ----------------------------------------------------------------------------
    async def create_invoice(self, business_id: str, invoice: NewInvoice, lines: Sequence[NewInvoiceLine]) -> Invoice:
        """Save an invoice header and its lines together."""
        fields = invoice.model_dump()
        columns = ", ".join(fields)
        placeholders = ", ".join(["%s"] * len(fields))

        def work(c: Conn) -> Invoice | None:
            """Check the supplier, insert the header, insert the lines, read the invoice back."""
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

    async def get_invoice(self, business_id: str, invoice_id: str) -> Invoice | None:
        """One invoice with its lines, or None."""
        return await self._run(lambda c: _load_invoice(c, business_id, invoice_id))

    async def list_invoices(self, business_id: str, query: InvoiceQuery) -> list[InvoiceSummary]:
        """Search invoices (newest invoice date first)."""
        clauses = ["i.business_id = %s"]
        params: list[Any] = [business_id]
        for column, value in (
            ("i.supplier_id = %s", query.supplier_id),
            ("i.status = %s", query.status),
            ("i.payment_status = %s", query.payment_status),
            ("i.invoice_date >= %s", query.date_from),
            ("i.invoice_date <= %s", query.date_to),
        ):
            if value:
                clauses.append(column)
                params.append(value)
        if query.invoice_number:
            # Escape the wildcard characters so the owner's text is matched literally.
            literal = (query.invoice_number.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"))
            clauses.append("i.invoice_number ilike %s escape '\\'")
            params.append(f"%{literal}%")
        params.append(query.limit)
        rows = await self._run(lambda c: _fetchall(
            c,
            _INVOICE_SELECT + f"where {' and '.join(clauses)} "
            "order by i.invoice_date desc nulls last, i.created_at desc limit %s",
            params,
        ))
        return [InvoiceSummary.model_validate(row) for row in rows]

    async def list_all_invoices(self, business_id: str, *, supplier_id: str | None = None) -> list[InvoiceSummary]:
        """Every invoice of the business (or of one supplier), for totals and the dashboard."""
        sql = _INVOICE_SELECT + "where i.business_id = %s"
        params: list[Any] = [business_id]
        if supplier_id:
            sql += " and i.supplier_id = %s"
            params.append(supplier_id)
        rows = await self._run(lambda c: _fetchall(
            c, sql + " order by i.invoice_date desc nulls last, i.created_at desc", params
        ))
        return [InvoiceSummary.model_validate(row) for row in rows]

    async def find_invoice_by_number(
        self, business_id: str, supplier_id: str, invoice_number: str, *, exclude_id: str | None = None
    ) -> InvoiceSummary | None:
        """An invoice of this supplier with this number (duplicate check), or None."""
        sql = _INVOICE_SELECT + "where i.business_id = %s and i.supplier_id = %s and i.invoice_number = %s"
        params: list[Any] = [business_id, supplier_id, invoice_number]
        if exclude_id:
            sql += " and i.id <> %s"
            params.append(exclude_id)
        row = await self._run(lambda c: _fetchone(c, sql + " limit 1", params))
        return InvoiceSummary.model_validate(row) if row else None

    async def update_invoice(
        self,
        business_id: str,
        invoice_id: str,
        changes: dict[str, Any],
        lines: Sequence[NewInvoiceLine] | None = None,
    ) -> Invoice:
        """Change header fields and, when ``lines`` is given, replace all lines, in one transaction."""
        check_columns(changes, INVOICE_UPDATABLE_COLUMNS)
        assignments = "".join(f"{column} = %s, " for column in changes)

        def work(c: Conn) -> Invoice | None:
            """Update the header, replace the lines if given, read the invoice back."""
            row = _fetchone(
                c,
                f"update invoices set {assignments}updated_at = now() where business_id = %s and id = %s returning id",
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

    async def update_line_match(self, business_id: str, invoice_id: str, line_id: str, match: LineMatchUpdate) -> None:
        """Store the product-match result of one line and mark its invoice as changed (updated_at)."""
        fields = match.model_dump()
        assignments = ", ".join(f"{column} = %s" for column in fields)

        def work(c: Conn) -> None:
            """Update the line, then touch its invoice, in one transaction."""
            row = _fetchone(
                c,
                f"update invoice_items set {assignments} where id = %s and invoice_id = "
                "(select id from invoices where business_id = %s and id = %s) returning id",
                (*(_param(column, value) for column, value in fields.items()), line_id, business_id, invoice_id),
            )
            if row is None:
                raise NotFoundError("invoice line not found")
            c.execute("update invoices set updated_at = now() where business_id = %s and id = %s",
                      (business_id, invoice_id))

        await self._run(work)

    async def list_supplier_lines(self, business_id: str, supplier_id: str) -> list[SupplierLine]:
        """Lines of this supplier's verified invoices, newest first (Supplier Profile)."""
        rows = await self._run(lambda c: _fetchall(
            c,
            "select i.id as invoice_id, i.invoice_number, i.invoice_date, l.item_name, l.product_id, "
            "p.name as product_name, l.quantity, l.mro, l.line_total "
            "from invoice_items l join invoices i on i.id = l.invoice_id "
            "left join products p on p.id = l.product_id "
            "where i.business_id = %s and i.supplier_id = %s and i.status = 'verified' "
            "order by i.invoice_date desc nulls last, i.created_at desc, l.sort_order",
            (business_id, supplier_id),
        ))
        return [SupplierLine.model_validate(row) for row in rows]

    # ---- logs --------------------------------------------------------------------------------
    async def log_extraction_run(self, business_id: str, run: ExtractionRunLog) -> None:
        """Keep one raw agent output (proposal: store every raw agent output)."""
        await self._run(lambda c: c.execute(
            "insert into extraction_runs "
            "(business_id, invoice_id, agent, model, guardrail_passed, raw_output, error, duration_ms) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s)",
            (business_id, run.invoice_id, run.agent, run.model, run.guardrail_passed,
             None if run.raw_output is None else Jsonb(run.raw_output), run.error, run.duration_ms),
        ))

    async def log_event(self, business_id: str, invoice_id: str | None, event_type: str, payload: dict[str, Any]) -> None:
        """Add an entry to the audit log."""
        await self._run(lambda c: c.execute(
            "insert into audit_log (business_id, invoice_id, event_type, payload) values (%s, %s, %s, %s)",
            (business_id, invoice_id, event_type, Jsonb(payload)),
        ))

    async def list_events(self, business_id: str, invoice_id: str) -> list[AuditEvent]:
        """The audit log of one invoice, oldest first."""
        rows = await self._run(lambda c: _fetchall(
            c,
            "select id, invoice_id, event_type, payload, created_at from audit_log "
            "where business_id = %s and invoice_id = %s order by created_at, id",
            (business_id, invoice_id),
        ))
        return [AuditEvent.model_validate(row) for row in rows]


# =============================================================================================
# 3. Supabase Storage (invoice photos)
# =============================================================================================


class StorageError(Exception):
    """An upload to Supabase Storage failed."""


class SupabaseStorage:
    """Uploads invoice photos to a private bucket and creates short-lived view links.

    Uses the service-role key, which stays on the server.
    """

    def __init__(self, *, url: str, service_key: str, bucket: str, client: httpx.AsyncClient | None = None) -> None:
        """Remember the Storage address and key (tests pass a mock HTTP client)."""
        self._base = url.rstrip("/") + "/storage/v1"
        self._bucket = bucket
        self._headers = {"apikey": service_key}
        # Legacy service_role keys are JWTs and also go in Authorization; new sb_secret_ keys do not.
        if service_key.startswith("eyJ"):
            self._headers["Authorization"] = f"Bearer {service_key}"
        self._client = client or httpx.AsyncClient(timeout=30)

    def _object(self, path: str) -> str:
        """The bucket-relative address of one stored file."""
        return f"{self._bucket}/{quote(path, safe='/')}"

    async def put(self, path: str, data: bytes, content_type: str) -> None:
        """Upload (or replace) one file."""
        response = await self._client.post(
            f"{self._base}/object/{self._object(path)}",
            content=data,
            headers={**self._headers, "Content-Type": content_type, "x-upsert": "true"},
        )
        if response.status_code >= 400:
            raise StorageError(f"upload failed ({response.status_code}): {response.text[:200]}")

    async def signed_url(self, path: str, expires_in: int = 3600) -> str | None:
        """A link to view one file for ``expires_in`` seconds, or None if it cannot be made."""
        response = await self._client.post(
            f"{self._base}/object/sign/{self._object(path)}",
            json={"expiresIn": expires_in},
            headers=self._headers,
        )
        if response.status_code >= 400:
            return None
        signed = response.json().get("signedURL") or response.json().get("signedUrl")
        return f"{self._base}{signed}" if signed else None

    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
