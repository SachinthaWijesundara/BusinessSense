"""Data-access contract and an in-memory implementation.

``PostgresRepository`` (database/postgres.py) is what runs against Supabase. ``InMemoryRepository``
backs unit tests and local runs without ``DATABASE_URL``. Both pass the same contract tests in
``tests/test_repository_contract.py``, so behaviour cannot drift between them.

Every method takes ``business_id``: rows of one business are never visible to another.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, Protocol

from services.embeddings import cosine_similarity

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


class RepositoryError(Exception):
    pass


class DuplicateError(RepositoryError):
    """A unique rule was violated (same product name, same supplier invoice number...)."""


class NotFoundError(RepositoryError):
    pass


class Repository(Protocol):
    kind: str

    async def close(self) -> None: ...

    # suppliers
    async def list_suppliers(self, business_id: str) -> list[SupplierRecord]: ...
    async def get_supplier(self, business_id: str, supplier_id: str) -> SupplierRecord | None: ...
    async def find_suppliers_by_name(self, business_id: str, name: str) -> list[SupplierRecord]: ...
    async def create_supplier(self, business_id: str, data: NewSupplier) -> SupplierRecord: ...
    async def update_supplier(
        self, business_id: str, supplier_id: str, changes: dict[str, Any]
    ) -> SupplierRecord: ...

    # products and aliases
    async def list_products(self, business_id: str) -> list[ProductRecord]: ...
    async def get_product(self, business_id: str, product_id: str) -> ProductRecord | None: ...
    async def create_product(self, business_id: str, data: NewProduct) -> ProductRecord: ...
    async def has_products(self, business_id: str) -> bool: ...
    async def products_without_aliases(self, business_id: str) -> list[ProductRecord]: ...
    async def aliases_without_embeddings(self, business_id: str) -> list[AliasRecord]: ...
    async def set_alias_embedding(
        self, business_id: str, alias_id: str, embedding: Sequence[float]
    ) -> None: ...
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
    ) -> None: ...
    async def find_aliases_exact(
        self, business_id: str, normalized_text: str
    ) -> list[AliasMatch]: ...
    async def search_aliases(
        self, business_id: str, embedding: Sequence[float], limit: int
    ) -> list[AliasMatch]: ...

    # invoices
    async def create_invoice(
        self, business_id: str, invoice: NewInvoice, lines: Sequence[NewInvoiceLine]
    ) -> InvoiceRecord: ...
    async def get_invoice(self, business_id: str, invoice_id: str) -> InvoiceRecord | None: ...
    async def list_invoices(self, business_id: str, query: InvoiceQuery) -> list[InvoiceRecord]: ...
    async def find_invoice_by_number(
        self,
        business_id: str,
        supplier_id: str,
        invoice_number: str,
        *,
        exclude_id: str | None = None,
    ) -> InvoiceRecord | None: ...
    async def update_invoice(
        self,
        business_id: str,
        invoice_id: str,
        changes: dict[str, Any],
        lines: Sequence[NewInvoiceLine] | None = None,
    ) -> InvoiceRecord: ...
    async def update_line_match(
        self, business_id: str, invoice_id: str, line_id: str, match: LineMatchUpdate
    ) -> None: ...

    # logs
    async def log_extraction_run(self, business_id: str, run: ExtractionRunLog) -> None: ...
    async def log_event(
        self, business_id: str, invoice_id: str | None, event_type: str, payload: dict[str, Any]
    ) -> None: ...
    async def list_events(self, business_id: str, invoice_id: str) -> list[AuditEvent]: ...


def check_columns(changes: dict[str, Any], allowed: frozenset[str]) -> None:
    unknown = set(changes) - allowed
    if unknown:
        raise RepositoryError(f"cannot update column(s): {', '.join(sorted(unknown))}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class InMemoryRepository:
    """Dict-backed repository. Not for production: data is lost on restart."""

    kind = "memory"

    def __init__(self) -> None:
        self.suppliers: dict[str, SupplierRecord] = {}
        self.products: dict[str, ProductRecord] = {}
        self.aliases: dict[str, dict[str, Any]] = {}
        self.invoices: dict[str, InvoiceRecord] = {}
        self.extraction_runs: list[dict[str, Any]] = []
        self.events: list[AuditEvent] = []
        self._event_business: dict[str, str] = {}

    async def close(self) -> None:
        return None

    # suppliers -------------------------------------------------------------------------------
    async def list_suppliers(self, business_id: str) -> list[SupplierRecord]:
        rows = [s for s in self.suppliers.values() if s.business_id == business_id]
        return sorted(rows, key=lambda s: s.name.lower())

    async def get_supplier(self, business_id: str, supplier_id: str) -> SupplierRecord | None:
        row = self.suppliers.get(supplier_id)
        return row if row and row.business_id == business_id else None

    async def find_suppliers_by_name(self, business_id: str, name: str) -> list[SupplierRecord]:
        target = name.strip().lower()
        return [s for s in await self.list_suppliers(business_id) if s.name.strip().lower() == target]

    async def create_supplier(self, business_id: str, data: NewSupplier) -> SupplierRecord:
        now = _now()
        record = SupplierRecord(
            id=_new_id(), business_id=business_id, created_at=now, updated_at=now, **data.model_dump()
        )
        self.suppliers[record.id] = record
        return record

    async def update_supplier(
        self, business_id: str, supplier_id: str, changes: dict[str, Any]
    ) -> SupplierRecord:
        check_columns(changes, SUPPLIER_UPDATABLE_COLUMNS)
        current = await self.get_supplier(business_id, supplier_id)
        if current is None:
            raise NotFoundError("supplier not found")
        updated = current.model_copy(update={**changes, "updated_at": _now()})
        self.suppliers[supplier_id] = updated
        return updated

    # products and aliases --------------------------------------------------------------------
    async def list_products(self, business_id: str) -> list[ProductRecord]:
        rows = [p for p in self.products.values() if p.business_id == business_id]
        return sorted(rows, key=lambda p: p.name.lower())

    async def get_product(self, business_id: str, product_id: str) -> ProductRecord | None:
        row = self.products.get(product_id)
        return row if row and row.business_id == business_id else None

    async def create_product(self, business_id: str, data: NewProduct) -> ProductRecord:
        name = data.name.strip().lower()
        if any(p.name.strip().lower() == name for p in await self.list_products(business_id)):
            raise DuplicateError(f"a product named '{data.name}' already exists")
        record = ProductRecord(id=_new_id(), business_id=business_id, created_at=_now(), **data.model_dump())
        self.products[record.id] = record
        return record

    async def has_products(self, business_id: str) -> bool:
        return any(p.business_id == business_id for p in self.products.values())

    async def products_without_aliases(self, business_id: str) -> list[ProductRecord]:
        with_alias = {a["product_id"] for a in self.aliases.values() if a["business_id"] == business_id}
        return [p for p in await self.list_products(business_id) if p.id not in with_alias]

    async def aliases_without_embeddings(self, business_id: str) -> list[AliasRecord]:
        return [
            self._alias_record(a)
            for a in self.aliases.values()
            if a["business_id"] == business_id and a["embedding"] is None
        ]

    async def set_alias_embedding(
        self, business_id: str, alias_id: str, embedding: Sequence[float]
    ) -> None:
        alias = self.aliases.get(alias_id)
        if alias and alias["business_id"] == business_id:
            alias["embedding"] = list(embedding)

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
        if await self.get_product(business_id, product_id) is None:
            raise NotFoundError("product not found")
        for alias in self.aliases.values():
            if (
                alias["business_id"] == business_id
                and alias["product_id"] == product_id
                and alias["normalized_text"] == normalized_text
            ):
                alias["confirmed"] = alias["confirmed"] or confirmed
                if embedding is not None:
                    alias["embedding"] = list(embedding)
                return
        alias_id = _new_id()
        self.aliases[alias_id] = {
            "id": alias_id,
            "business_id": business_id,
            "product_id": product_id,
            "alias_text": alias_text,
            "normalized_text": normalized_text,
            "embedding": None if embedding is None else list(embedding),
            "source": source,
            "confirmed": confirmed,
        }

    @staticmethod
    def _alias_record(alias: dict[str, Any]) -> AliasRecord:
        return AliasRecord(
            id=alias["id"],
            product_id=alias["product_id"],
            alias_text=alias["alias_text"],
            normalized_text=alias["normalized_text"],
            source=alias["source"],
            confirmed=alias["confirmed"],
        )

    def _match(self, alias: dict[str, Any], similarity: float) -> AliasMatch:
        product = self.products[alias["product_id"]]
        return AliasMatch(
            product_id=product.id,
            product_name=product.name,
            product_sku=product.sku,
            alias_text=alias["alias_text"],
            similarity=similarity,
        )

    async def find_aliases_exact(self, business_id: str, normalized_text: str) -> list[AliasMatch]:
        return [
            self._match(alias, 1.0)
            for alias in self.aliases.values()
            if alias["business_id"] == business_id
            and alias["confirmed"]
            and alias["normalized_text"] == normalized_text
        ]

    async def search_aliases(
        self, business_id: str, embedding: Sequence[float], limit: int
    ) -> list[AliasMatch]:
        scored = [
            self._match(alias, cosine_similarity(embedding, alias["embedding"]))
            for alias in self.aliases.values()
            if alias["business_id"] == business_id
            and alias["confirmed"]
            and alias["embedding"] is not None
        ]
        scored.sort(key=lambda match: match.similarity, reverse=True)
        return scored[:limit]

    # invoices --------------------------------------------------------------------------------
    def _check_duplicate(
        self, business_id: str, supplier_id: str | None, number: str | None, exclude_id: str | None
    ) -> None:
        if not supplier_id or not number:
            return
        for inv in self.invoices.values():
            if (
                inv.business_id == business_id
                and inv.supplier_id == supplier_id
                and inv.invoice_number == number
                and inv.id != exclude_id
            ):
                raise DuplicateError("that invoice number is already saved for this supplier")

    def _check_refs(self, business_id: str, supplier_id: str | None, lines: Sequence[NewInvoiceLine]) -> None:
        if supplier_id and (
            supplier_id not in self.suppliers or self.suppliers[supplier_id].business_id != business_id
        ):
            raise NotFoundError("supplier not found")
        for line in lines:
            for product_id in (line.product_id, line.suggested_product_id):
                if product_id and product_id not in self.products:
                    raise NotFoundError("product not found")

    @staticmethod
    def _line_records(invoice_id: str, lines: Sequence[NewInvoiceLine]) -> list[InvoiceLineRecord]:
        return [
            InvoiceLineRecord(id=_new_id(), invoice_id=invoice_id, **line.model_dump())
            for line in lines
        ]

    async def create_invoice(
        self, business_id: str, invoice: NewInvoice, lines: Sequence[NewInvoiceLine]
    ) -> InvoiceRecord:
        self._check_refs(business_id, invoice.supplier_id, lines)
        self._check_duplicate(business_id, invoice.supplier_id, invoice.invoice_number, None)
        now = _now()
        invoice_id = _new_id()
        record = InvoiceRecord(
            id=invoice_id,
            business_id=business_id,
            created_at=now,
            updated_at=now,
            lines=self._line_records(invoice_id, lines),
            **invoice.model_dump(),
        )
        self.invoices[invoice_id] = record
        return copy.deepcopy(record)

    async def get_invoice(self, business_id: str, invoice_id: str) -> InvoiceRecord | None:
        row = self.invoices.get(invoice_id)
        if row is None or row.business_id != business_id:
            return None
        result = copy.deepcopy(row)
        result.lines.sort(key=lambda line: line.sort_order)
        return result

    async def list_invoices(self, business_id: str, query: InvoiceQuery) -> list[InvoiceRecord]:
        rows = []
        for inv in self.invoices.values():
            if inv.business_id != business_id:
                continue
            if query.supplier_id and inv.supplier_id != query.supplier_id:
                continue
            if query.status and inv.status != query.status:
                continue
            if query.invoice_number and query.invoice_number.lower() not in (inv.invoice_number or "").lower():
                continue
            if query.date_from and (inv.invoice_date is None or inv.invoice_date < query.date_from):
                continue
            if query.date_to and (inv.invoice_date is None or inv.invoice_date > query.date_to):
                continue
            rows.append(inv.model_copy(update={"lines": []}))
        rows.sort(key=lambda inv: (inv.invoice_date is not None, inv.invoice_date, inv.created_at), reverse=True)
        return rows[: query.limit]

    async def find_invoice_by_number(
        self,
        business_id: str,
        supplier_id: str,
        invoice_number: str,
        *,
        exclude_id: str | None = None,
    ) -> InvoiceRecord | None:
        for inv in self.invoices.values():
            if (
                inv.business_id == business_id
                and inv.supplier_id == supplier_id
                and inv.invoice_number == invoice_number
                and inv.id != exclude_id
            ):
                return copy.deepcopy(inv)
        return None

    async def update_invoice(
        self,
        business_id: str,
        invoice_id: str,
        changes: dict[str, Any],
        lines: Sequence[NewInvoiceLine] | None = None,
    ) -> InvoiceRecord:
        check_columns(changes, INVOICE_UPDATABLE_COLUMNS)
        current = self.invoices.get(invoice_id)
        if current is None or current.business_id != business_id:
            raise NotFoundError("invoice not found")
        merged = current.model_copy(update=changes)
        self._check_refs(business_id, merged.supplier_id, lines or [])
        self._check_duplicate(business_id, merged.supplier_id, merged.invoice_number, invoice_id)
        update: dict[str, Any] = {**changes, "updated_at": _now()}
        if lines is not None:
            update["lines"] = self._line_records(invoice_id, lines)
        # Validate through the model so bad values fail like a database constraint would.
        self.invoices[invoice_id] = InvoiceRecord.model_validate({**current.model_dump(), **update})
        result = await self.get_invoice(business_id, invoice_id)
        assert result is not None
        return result

    async def update_line_match(
        self, business_id: str, invoice_id: str, line_id: str, match: LineMatchUpdate
    ) -> None:
        current = self.invoices.get(invoice_id)
        if current is None or current.business_id != business_id:
            raise NotFoundError("invoice not found")
        for product_id in (match.product_id, match.suggested_product_id):
            if product_id and product_id not in self.products:
                raise NotFoundError("product not found")
        for index, line in enumerate(current.lines):
            if line.id == line_id:
                current.lines[index] = line.model_copy(update=match.model_dump())
                return
        raise NotFoundError("invoice line not found")

    # logs ------------------------------------------------------------------------------------
    async def log_extraction_run(self, business_id: str, run: ExtractionRunLog) -> None:
        self.extraction_runs.append({"business_id": business_id, **run.model_dump()})

    async def log_event(
        self, business_id: str, invoice_id: str | None, event_type: str, payload: dict[str, Any]
    ) -> None:
        event = AuditEvent(
            id=_new_id(),
            invoice_id=invoice_id,
            event_type=event_type,
            payload=copy.deepcopy(payload),
            created_at=_now(),
        )
        self.events.append(event)
        self._event_business[event.id] = business_id

    async def list_events(self, business_id: str, invoice_id: str) -> list[AuditEvent]:
        return [
            event
            for event in self.events
            if event.invoice_id == invoice_id and self._event_business[event.id] == business_id
        ]
