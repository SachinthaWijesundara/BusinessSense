from __future__ import annotations

from datetime import date

import pytest

from agentic.guardrails import blocking_fields, missing_fields
from agentic.schemas import ExtractedDocument, InvoiceDraft
from config import DEFAULT_BUSINESS_ID, Settings
from services.invoice_service import compute_due_date, date_issues, parse_iso_date
from services.text import normalize_text
from tests.helpers import invoice_doc, invoice_line, receipt_doc


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("63MM  uPVC-Pipe", "63mm upvc pipe"),
        ("  G.I. Pipe 1/2\" ", "g.i. pipe 1 2"),
        ("Cement 50kg (Holcim)", "cement 50kg holcim"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_text(raw, expected):
    assert normalize_text(raw) == expected


def test_parse_iso_date():
    assert parse_iso_date("2026-10-20") == date(2026, 10, 20)
    assert parse_iso_date("2026-10-20T00:00:00") == date(2026, 10, 20)
    assert parse_iso_date("13/06/2023") is None
    assert parse_iso_date(None) is None


def test_date_issues():
    today = date(2026, 11, 8)
    assert date_issues(InvoiceDraft(invoice_date="2026-10-20"), today=today) == []
    assert [i.code for i in date_issues(InvoiceDraft(invoice_date="2026-12-25"), today=today)] == ["future_date"]
    assert [i.code for i in date_issues(InvoiceDraft(invoice_date="2009-01-01"), today=today)] == ["implausible_date"]
    assert [i.code for i in date_issues(InvoiceDraft(invoice_date="31/31/2026"), today=today)] == ["invalid_date"]
    assert [i.field for i in date_issues(InvoiceDraft(delivery_date="nope"), today=today)] == ["delivery_date"]


def test_due_date_rules():
    d = date(2026, 10, 20)
    assert compute_due_date(d, credit_days=30) == date(2026, 11, 19)
    assert compute_due_date(d, payment_type="cash", credit_days=30) == d
    assert compute_due_date(d) is None
    assert compute_due_date(None, credit_days=30) is None


def test_guardrail_blocks_invoice_without_lines_or_date():
    assert blocking_fields(ExtractedDocument.model_validate(invoice_doc())) == []
    no_lines = ExtractedDocument.model_validate(invoice_doc(lines=[], total=0))
    assert blocking_fields(no_lines) == ["invoice.lines"]
    no_date = ExtractedDocument.model_validate(invoice_doc(date=None))
    assert blocking_fields(no_date) == ["invoice.invoice_date"]
    blank_rows = ExtractedDocument.model_validate(invoice_doc(lines=[invoice_line(" ", 1, 1)]))
    assert "invoice.lines" in blocking_fields(blank_rows)
    assert blocking_fields(ExtractedDocument(doc_type="invoice")) == ["invoice.lines", "invoice.invoice_date"]


def test_guardrail_receipts():
    assert blocking_fields(ExtractedDocument.model_validate(receipt_doc())) == []
    assert blocking_fields(ExtractedDocument(doc_type="receipt")) == ["receipt.payments"]


def test_missing_fields_do_not_block():
    doc = ExtractedDocument.model_validate(invoice_doc(supplier=None, number=None))
    assert blocking_fields(doc) == []
    assert missing_fields(doc) == ["supplier.name", "invoice.invoice_number"]


def test_settings_from_env(monkeypatch):
    for name in ("OPENAI_MODEL", "INVOICE_VISION_MODEL", "ALLOWED_ORIGINS", "DATABASE_URL", "SKU_MATCH_THRESHOLD"):
        monkeypatch.delenv(name, raising=False)
    defaults = Settings.from_env()
    assert defaults.vision_model == "gpt-4o"
    assert defaults.sku_match_threshold == 0.82
    assert defaults.repository_kind == "memory"
    assert defaults.default_business_id == DEFAULT_BUSINESS_ID
    assert "http://localhost:3001" in defaults.allowed_origins

    monkeypatch.setenv("ALLOWED_ORIGINS", "https://businesssense.vercel.app/, http://localhost:3001")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("SKU_MATCH_THRESHOLD", "0.9")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    custom = Settings.from_env()
    assert custom.allowed_origins == ("https://businesssense.vercel.app", "http://localhost:3001")
    assert custom.vision_model == "gpt-4o-mini"
    assert custom.sku_match_threshold == 0.9
    assert custom.repository_kind == "postgres"
    monkeypatch.setenv("INVOICE_VISION_MODEL", "gpt-4o")
    assert Settings.from_env().vision_model == "gpt-4o"
