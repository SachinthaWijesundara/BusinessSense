"""Everything the backend uses to read and write Supabase (proposal: database/client.py).

1. Record models   the shape of each row the API reads or writes (suppliers, products, invoices,
                   supplier payments...).
2. ``Database``    all SQL against Supabase Postgres, through psycopg 3. Each method runs in one
                   transaction, so an invoice header and its lines always change together.
                   psycopg's synchronous pool is used and every call runs in a worker thread:
                   psycopg's async mode cannot run on Windows' default event loop.
3. ``SupabaseStorage``  the private buckets: invoice photos (short-lived signed links) and trained
                        models.
4. ``SupabaseAuth``     owner login through Supabase Auth: sign in, refresh, sign out, and
                        checking a login token. Passwords never reach the database tables.

Every query filters on ``business_id``: one shop never sees another shop's rows.
The tables themselves are created by the SQL files in the repo-root ``database/`` folder.
"""

from __future__ import annotations

import asyncio
import time
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
PaymentStatus = Literal["unpaid", "paid", "unconfirmed"]  # an invoice's payment state
SupplierPaymentStatus = Literal["unconfirmed", "confirmed", "rejected"]  # one recorded payment
PaymentMethod = Literal["cash", "cheque", "bank_transfer"]


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
    """A saved invoice without its lines (list rows). ``amount_paid`` is the sum of the owner's
    confirmed supplier payments linked to it."""

    id: str
    business_id: str
    supplier_name: str | None = None
    amount_paid: float = 0.0
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
    offset: int = Field(0, ge=0)  # how many matching invoices to skip (for pages)


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


class PurchaseLine(BaseModel):
    """One line of a verified invoice that is linked to a catalogue product: what Model 1 learns from.

    ``line_total`` is what was paid for the line (after any line discount, in either data-entry
    style), so ``line_total / quantity`` is the unit price actually paid.
    """

    invoice_id: str
    invoice_date: date | None = None
    supplier_id: str | None = None
    product_id: str
    product_name: str
    quantity: float | None = None
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


class NewPayment(BaseModel):
    """A supplier payment to save: typed in by the owner, or one invoice settled on a receipt photo."""

    supplier_id: str | None = None
    supplier_name_raw: str | None = None
    invoice_id: str | None = None
    invoice_number_raw: str | None = None
    amount: float | None = None
    paid_on: date | None = None
    method: PaymentMethod | None = None
    receipt_number: str | None = None
    cheque_number: str | None = None
    bank_name: str | None = None
    cheque_date: date | None = None
    status: SupplierPaymentStatus = "unconfirmed"
    source: Literal["manual", "agent"] = "manual"
    image_path: str | None = None
    low_confidence_fields: list[str] = Field(default_factory=list)
    confirmed_at: datetime | None = None


class PaymentRecord(NewPayment):
    """A saved supplier payment, with its supplier's name and its invoice's number.

    ``reminder_due`` is true once it has waited more than two days for the owner's confirmation;
    ``image_url`` is filled in by the API (signed link to the receipt photo).
    """

    id: str
    business_id: str
    supplier_name: str | None = None
    invoice_number: str | None = None
    reminder_sent_at: datetime | None = None
    reminder_due: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    image_url: str | None = None


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
PAYMENT_UPDATABLE_COLUMNS = frozenset(
    {
        "supplier_id", "invoice_id", "invoice_number_raw", "amount", "paid_on", "method", "receipt_number",
        "cheque_number", "bank_name", "cheque_date", "status", "image_path", "confirmed_at",
    }
)
PAYMENT_REMINDER_DAYS = 2  # proposal: an unconfirmed payment gets a reminder after two days


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
# Lines typed in the newer data-entry style keep the rate in mrp / discount_rate / net_price and
# leave mro at 0; for them the rate actually paid per unit is net_price.
_LINE_RATE = "case when l.mro = 0 and l.net_price is not null then l.net_price else l.mro end"


def _line_column(name: str) -> str:
    """One invoice_items column for a select (the unit rate as paid for MRP-style lines)."""
    return f"{_LINE_RATE} as mro" if name == "mro" else f"l.{name}"


# Invoice columns plus the supplier's name and the confirmed payments made against it.
_INVOICE_SELECT = (
    "select " + ", ".join(f"i.{name}" for name in _INVOICE_FIELDS) + ", s.name as supplier_name, "
    "coalesce((select sum(sp.amount) from supplier_payments sp "
    "where sp.invoice_id = i.id and sp.status = 'confirmed'), 0) as amount_paid "
    "from invoices i left join suppliers s on s.id = i.supplier_id "
)
_PAYMENT_FIELDS = (
    "id", "business_id", "supplier_id", "supplier_name_raw", "invoice_id", "invoice_number_raw", "amount",
    "paid_on", "method", "receipt_number", "cheque_number", "bank_name", "cheque_date", "status", "source",
    "image_path", "low_confidence_fields", "confirmed_at", "reminder_sent_at", "created_at", "updated_at",
)
# Payment columns plus the supplier's name, the invoice's number and whether a reminder is due.
_PAYMENT_SELECT = (
    "select " + ", ".join(f"p.{name}" for name in _PAYMENT_FIELDS) + ", s.name as supplier_name, "
    "i.invoice_number, (p.status = 'unconfirmed' and p.created_at <= now() - "
    f"interval '{PAYMENT_REMINDER_DAYS} days') as reminder_due "
    "from supplier_payments p left join suppliers s on s.id = p.supplier_id "
    "left join invoices i on i.id = p.invoice_id "
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
        "select " + ", ".join(_line_column(name) for name in _LINE_FIELDS) + ", p.name as product_name "
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

    @staticmethod
    def _invoice_filters(business_id: str, query: InvoiceQuery) -> tuple[list[str], list[Any]]:
        """The where-clauses and values of an invoice search."""
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
        return clauses, params

    async def list_invoices(self, business_id: str, query: InvoiceQuery) -> list[InvoiceSummary]:
        """Search invoices, newest invoice date first, one page at a time (limit / offset)."""
        clauses, params = self._invoice_filters(business_id, query)
        rows = await self._run(lambda c: _fetchall(
            c,
            _INVOICE_SELECT + f"where {' and '.join(clauses)} "
            "order by i.invoice_date desc nulls last, i.created_at desc, i.id limit %s offset %s",
            [*params, query.limit, query.offset],
        ))
        return [InvoiceSummary.model_validate(row) for row in rows]

    async def count_invoices(self, business_id: str, query: InvoiceQuery) -> int:
        """How many invoices match a search (all pages together)."""
        clauses, params = self._invoice_filters(business_id, query)
        row = await self._run(lambda c: _fetchone(
            c, f"select count(*) as n from invoices i where {' and '.join(clauses)}", params
        ))
        return int(row["n"]) if row else 0

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
            f"p.name as product_name, l.quantity, {_LINE_RATE} as mro, l.line_total "
            "from invoice_items l join invoices i on i.id = l.invoice_id "
            "left join products p on p.id = l.product_id "
            "where i.business_id = %s and i.supplier_id = %s and i.status = 'verified' "
            "order by i.invoice_date desc nulls last, i.created_at desc, l.sort_order",
            (business_id, supplier_id),
        ))
        return [SupplierLine.model_validate(row) for row in rows]

    async def find_invoices_by_number_any_supplier(self, business_id: str, invoice_number: str) -> list[InvoiceSummary]:
        """Every invoice with exactly this number, whatever its supplier (to place a receipt)."""
        rows = await self._run(lambda c: _fetchall(
            c, _INVOICE_SELECT + "where i.business_id = %s and i.invoice_number = %s order by i.created_at",
            (business_id, invoice_number),
        ))
        return [InvoiceSummary.model_validate(row) for row in rows]

    # ---- supplier payments -------------------------------------------------------------------
    async def create_payments(self, business_id: str, payments: Sequence[NewPayment]) -> list[PaymentRecord]:
        """Save payments together (all the rows of one receipt, or one typed-in payment)."""

        def work(c: Conn) -> list[dict[str, Any]]:
            """Check each supplier and invoice belongs to the business, insert, read the rows back."""
            ids = []
            for payment in payments:
                if payment.supplier_id and _fetchone(
                    c, "select 1 from suppliers where business_id = %s and id = %s", (business_id, payment.supplier_id)
                ) is None:
                    raise NotFoundError("supplier not found")
                if payment.invoice_id and _fetchone(
                    c, "select 1 from invoices where business_id = %s and id = %s", (business_id, payment.invoice_id)
                ) is None:
                    raise NotFoundError("invoice not found")
                fields = payment.model_dump()
                row = _fetchone(
                    c,
                    f"insert into supplier_payments (business_id, {', '.join(fields)}) "
                    f"values (%s, {', '.join(['%s'] * len(fields))}) returning id",
                    (business_id, *(_param(column, value) for column, value in fields.items())),
                )
                assert row is not None
                ids.append(row["id"])
            return [
                row for payment_id in ids
                if (row := _fetchone(c, _PAYMENT_SELECT + "where p.business_id = %s and p.id = %s",
                                     (business_id, payment_id))) is not None
            ]

        return [PaymentRecord.model_validate(row) for row in await self._run(work)]

    async def get_payment(self, business_id: str, payment_id: str) -> PaymentRecord | None:
        """One payment, or None."""
        row = await self._run(lambda c: _fetchone(
            c, _PAYMENT_SELECT + "where p.business_id = %s and p.id = %s", (business_id, payment_id)
        ))
        return PaymentRecord.model_validate(row) if row else None

    async def list_payments(
        self,
        business_id: str,
        *,
        supplier_id: str | None = None,
        invoice_id: str | None = None,
        status: SupplierPaymentStatus | None = None,
        receipt_number: str | None = None,
    ) -> list[PaymentRecord]:
        """Payments filtered by supplier, invoice, status or receipt number (newest payment first)."""
        clauses = ["p.business_id = %s"]
        params: list[Any] = [business_id]
        for clause, value in (
            ("p.supplier_id = %s", supplier_id),
            ("p.invoice_id = %s", invoice_id),
            ("p.status = %s", status),
            ("p.receipt_number = %s", receipt_number),
        ):
            if value:
                clauses.append(clause)
                params.append(value)
        rows = await self._run(lambda c: _fetchall(
            c,
            _PAYMENT_SELECT + f"where {' and '.join(clauses)} "
            "order by p.paid_on desc nulls first, p.created_at desc, p.receipt_number, p.id",
            params,
        ))
        return [PaymentRecord.model_validate(row) for row in rows]

    async def update_payment(self, business_id: str, payment_id: str, changes: dict[str, Any]) -> PaymentRecord:
        """Change some payment fields (corrections, confirmation, rejection)."""
        check_columns(changes, PAYMENT_UPDATABLE_COLUMNS)
        assignments = "".join(f"{column} = %s, " for column in changes)

        def work(c: Conn) -> dict[str, Any] | None:
            """Check a new supplier or invoice belongs to the business, update, read the row back."""
            if changes.get("supplier_id") and _fetchone(
                c, "select 1 from suppliers where business_id = %s and id = %s", (business_id, changes["supplier_id"])
            ) is None:
                raise NotFoundError("supplier not found")
            if changes.get("invoice_id") and _fetchone(
                c, "select 1 from invoices where business_id = %s and id = %s", (business_id, changes["invoice_id"])
            ) is None:
                raise NotFoundError("invoice not found")
            row = _fetchone(
                c,
                f"update supplier_payments set {assignments}updated_at = now() "
                "where business_id = %s and id = %s returning id",
                (*(_param(column, value) for column, value in changes.items()), business_id, payment_id),
            )
            if row is None:
                raise NotFoundError("payment not found")
            return _fetchone(c, _PAYMENT_SELECT + "where p.business_id = %s and p.id = %s", (business_id, payment_id))

        row = await self._run(work)
        assert row is not None
        return PaymentRecord.model_validate(row)

    async def mark_reminders_sent(self, business_id: str, payment_ids: Sequence[str]) -> None:
        """Record when the owner was first reminded about payments still waiting for confirmation."""
        if not payment_ids:
            return
        await self._run(lambda c: c.execute(
            "update supplier_payments set reminder_sent_at = now() "
            "where business_id = %s and id = any(%s::uuid[]) and reminder_sent_at is null",
            (business_id, list(payment_ids)),
        ))

    async def purchase_lines(self, business_id: str, product_id: str | None = None) -> list[PurchaseLine]:
        """Lines of verified invoices that are linked to a product, oldest first (Model 1's history).

        Drafts, rejected invoices and lines whose product is not confirmed are left out, so the
        model only learns from checked, product-linked purchases (proposal section 6).
        """
        sql = (
            "select i.id as invoice_id, i.invoice_date, i.supplier_id, l.product_id, p.name as product_name, "
            "l.quantity, l.line_total from invoice_items l join invoices i on i.id = l.invoice_id "
            "join products p on p.id = l.product_id "
            "where i.business_id = %s and i.status = 'verified' and l.product_id is not null"
        )
        params: list[Any] = [business_id]
        if product_id:
            sql += " and l.product_id = %s"
            params.append(product_id)
        rows = await self._run(lambda c: _fetchall(
            c, sql + " order by i.invoice_date nulls first, i.created_at, i.id, l.sort_order", params
        ))
        return [PurchaseLine.model_validate(row) for row in rows]

    async def business_ids(self) -> list[str]:
        """Every business in this database (one per shop's project), oldest first."""
        rows = await self._run(lambda c: _fetchall(c, "select id from businesses order by created_at, id"))
        return [row["id"] for row in rows]

    # ---- login ---------------------------------------------------------------------------------
    async def business_for_user(self, user_id: str) -> tuple[str, str] | None:
        """The business (id, name) a Supabase Auth login belongs to, or None."""
        row = await self._run(lambda c: _fetchone(
            c,
            "select b.id, b.name from business_users bu join businesses b on b.id = bu.business_id "
            "where bu.user_id = %s order by bu.created_at limit 1",
            (user_id,),
        ))
        return (row["id"], row["name"]) if row else None

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

    async def download(self, path: str) -> bytes | None:
        """One stored file's bytes (e.g. a trained model), or None if it is not there."""
        response = await self._client.get(f"{self._base}/object/authenticated/{self._object(path)}",
                                          headers=self._headers)
        if response.status_code in (400, 404):
            return None
        if response.status_code >= 400:
            raise StorageError(f"download failed ({response.status_code}): {response.text[:200]}")
        return response.content

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


# =============================================================================================
# 4. Supabase Auth (owner login)
# =============================================================================================


class AuthError(Exception):
    """A login was refused (wrong email or password, expired session, too many attempts)."""

    def __init__(self, status_code: int, message: str) -> None:
        """Keep the HTTP status to send back and a message for the owner."""
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class LoginSession(BaseModel):
    """What a successful login or refresh gives the browser."""

    access_token: str
    refresh_token: str
    expires_in: int
    user_id: str
    email: str | None = None


class SupabaseAuth:
    """Owner login through Supabase Auth, called only from the backend with the server key.

    Supabase keeps the passwords (hashed); this class only passes them on to sign in and checks
    the short-lived access tokens the browser sends back. A checked token is remembered for up to
    a minute so every request does not call Supabase again.
    """

    CACHE_SECONDS = 60.0

    def __init__(self, *, url: str, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        """Remember the Auth address and the server key (tests pass a mock HTTP client)."""
        self._base = url.rstrip("/") + "/auth/v1"
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=20)
        self._checked: dict[str, tuple[str, float]] = {}

    def _headers(self, token: str | None = None) -> dict[str, str]:
        """The server key, and the user's token when the call is about that user."""
        headers = {"apikey": self._api_key}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        elif self._api_key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _session(self, response: httpx.Response) -> LoginSession:
        """Turn Supabase's token reply into a LoginSession, or raise AuthError."""
        if response.status_code == 429:
            raise AuthError(429, "Too many login attempts. Wait a few minutes and try again.")
        if response.status_code >= 400:
            raise AuthError(401, "Email or password is incorrect.")
        data = response.json()
        user = data.get("user") or {}
        return LoginSession(access_token=data["access_token"], refresh_token=data["refresh_token"],
                            expires_in=int(data.get("expires_in") or 3600), user_id=user["id"],
                            email=user.get("email"))

    async def sign_in(self, email: str, password: str) -> LoginSession:
        """Check an email and password with Supabase Auth."""
        response = await self._client.post(f"{self._base}/token", params={"grant_type": "password"},
                                           json={"email": email, "password": password}, headers=self._headers())
        return self._session(response)

    async def refresh(self, refresh_token: str) -> LoginSession:
        """Swap a refresh token for a new session (the old refresh token stops working)."""
        response = await self._client.post(f"{self._base}/token", params={"grant_type": "refresh_token"},
                                           json={"refresh_token": refresh_token}, headers=self._headers())
        if response.status_code >= 400 and response.status_code != 429:
            raise AuthError(401, "Your login has expired. Log in again.")
        return self._session(response)

    async def sign_out(self, access_token: str) -> None:
        """End the session everywhere (Supabase revokes its refresh tokens)."""
        self._checked.pop(access_token, None)
        await self._client.post(f"{self._base}/logout", headers=self._headers(access_token))

    async def user_id(self, access_token: str) -> str | None:
        """The user a valid access token belongs to, or None for an invalid or expired token."""
        now = time.monotonic()
        cached = self._checked.get(access_token)
        if cached and cached[1] > now:
            return cached[0]
        response = await self._client.get(f"{self._base}/user", headers=self._headers(access_token))
        if response.status_code != 200:
            self._checked.pop(access_token, None)
            return None
        user_id = response.json().get("id")
        if len(self._checked) > 1000:
            self._checked = {k: v for k, v in self._checked.items() if v[1] > now}
        if user_id:
            self._checked[access_token] = (user_id, now + self.CACHE_SECONDS)
        return user_id

    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
