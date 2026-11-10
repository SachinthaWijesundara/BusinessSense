"""Tests for the deterministic rules: math audit, dates, due dates, text normalisation, the
guardrail's field lists, settings, cash flow, purchase analysis and the dashboard. No database, no OpenAI."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from agentic.guardrails import blocking_fields, missing_fields
from agentic.schemas import ExtractedDocument, InvoiceDraft, InvoiceLineDraft
from api.dashboard import build_dashboard
from database.client import InvoiceSummary, PaymentRecord, SupplierLine
from main import DEFAULT_BUSINESS_ID, Settings
from services.alias_service import normalize_text
from services.invoice_service import compute_due_date, date_issues, invoice_payment_status, math_audit, parse_iso_date
from services.cash_flow_service import (
    confirm_blockers,
    overdue_balances,
    payable_cash_series,
    payments_due,
    reminders,
    still_owed,
    supplier_totals,
)
from services.purchase_analysis_service import (
    last_months,
    monthly_purchases,
    monthly_purchases_and_paid,
    spend_slices_by_item,
    spend_slices_by_supplier,
    supplier_spend,
)
from tests.conftest import invoice_doc, invoice_line, receipt_doc


# ---- math audit --------------------------------------------------------------------------------
def line(item="63mm uPVC pipe", qty=10.0, rate=585.0, discount=0.0, total=None) -> InvoiceLineDraft:
    """A line that adds up unless ``total`` says otherwise."""
    return InvoiceLineDraft(item_name=item, quantity=qty, mro=rate, discount=discount,
                            line_total=(qty * rate - discount) if total is None else total)


def invoice(lines, total=None, special_discount=None, tax=None) -> InvoiceDraft:
    """An invoice whose total adds up unless ``total`` says otherwise."""
    if total is None:
        total = sum(item.line_total or 0 for item in lines) - (special_discount or 0) + (tax or 0)
    return InvoiceDraft(lines=lines, total=total, special_discount=special_discount, tax=tax)


def codes(result) -> list[str]:
    """The issue codes of an audit result."""
    return [issue.code for issue in result.issues]


def test_clean_invoice_passes():
    """A correct invoice has no issues and reports the computed total."""
    result = math_audit(invoice([line(), line("Elbow 63mm", 4, 120.0)]))
    assert result.ok and result.issues == [] and result.computed_total == 6330.0


def test_line_total_mismatch_is_caught():
    """A wrong line total is flagged with expected and actual values."""
    result = math_audit(invoice([line(total=5580.0)], total=5580.0))
    assert codes(result) == ["line_total_mismatch"]
    assert (result.issues[0].line_index, result.issues[0].expected, result.issues[0].actual) == (0, 5850.0, 5580.0)


def test_invoice_total_mismatch_is_caught():
    """A wrong invoice total is flagged."""
    result = math_audit(invoice([line()], total=6850.0))
    assert codes(result) == ["invoice_total_mismatch"] and result.issues[0].expected == 5850.0


@pytest.mark.parametrize(("diff", "ok"), [(0.0, True), (1.0, True), (1.01, False), (-1.0, True), (-1.01, False)])
def test_tolerance_is_plus_minus_one_rupee(diff, ok):
    """Differences up to Rs 1 pass; anything more fails."""
    assert math_audit(invoice([line(total=5850.0 + diff)], total=5850.0 + diff)).ok is ok


def test_discount_special_discount_and_tax():
    """Line discounts, the special discount and tax all count."""
    assert math_audit(invoice([line(discount=292.5)])).ok
    lines = [line(), line("Elbow 63mm", 4, 120.0)]
    assert math_audit(invoice(lines, total=6330.0 - 330.0 + 108.0, special_discount=330.0, tax=108.0)).ok
    assert not math_audit(invoice(lines, total=6330.0, special_discount=330.0)).ok


def test_float_noise_never_fails_the_audit():
    """0.1 + 0.2 style float noise does not matter (Decimal maths)."""
    lines = [line("Washer", 3, 0.1, total=0.3), line("Nut", 1, 0.2, total=0.2)]
    assert math_audit(invoice(lines, total=0.5), tolerance=0.0).ok


def test_missing_values_lines_and_total():
    """Missing values, a missing total and no lines are each reported."""
    missing_qty = InvoiceLineDraft(item_name="Pipe", quantity=None, mro=585.0, line_total=5850.0)
    assert codes(math_audit(invoice([missing_qty], total=5850.0))) == ["missing_value"]
    assert codes(math_audit(InvoiceDraft(lines=[line()], total=None))) == ["missing_total"]
    assert codes(math_audit(InvoiceDraft(lines=[], total=0))) == ["no_lines"]


def test_blank_rows_are_ignored_and_negative_values_flagged():
    """Blank rows do not count as lines; negative values are flagged."""
    result = math_audit(invoice([line(), InvoiceLineDraft(item_name="  "), line("Elbow", 2, 100.0, total=250.0)], total=6100.0))
    assert codes(result) == ["line_total_mismatch"] and result.issues[0].line_index == 1
    assert "negative_value" in codes(math_audit(invoice([line(qty=-1, total=-585.0)], total=-585.0)))


# ---- dates, text, guardrail -------------------------------------------------------------------
@pytest.mark.parametrize(("raw", "expected"), [("63MM  uPVC-Pipe", "63mm upvc pipe"), ('  G.I. Pipe 1/2" ', "g.i. pipe 1 2"),
                                               ("", ""), (None, "")])
def test_normalize_text(raw, expected):
    """Printed descriptions are tidied the same way every time."""
    assert normalize_text(raw) == expected


def test_dates_and_due_dates():
    """Dates parse, impossible dates are flagged, due dates follow credit days."""
    today = date(2026, 11, 8)
    assert parse_iso_date("2026-10-20") == date(2026, 10, 20) and parse_iso_date("13/06/2023") is None
    assert date_issues(InvoiceDraft(invoice_date="2026-10-20"), today=today) == []
    assert [i.code for i in date_issues(InvoiceDraft(invoice_date="2026-12-25"), today=today)] == ["future_date"]
    assert [i.code for i in date_issues(InvoiceDraft(invoice_date="2009-01-01"), today=today)] == ["implausible_date"]
    assert [i.code for i in date_issues(InvoiceDraft(invoice_date="31/31/2026"), today=today)] == ["invalid_date"]
    d = date(2026, 10, 20)
    assert compute_due_date(d, credit_days=30) == date(2026, 11, 19)
    assert compute_due_date(d, payment_type="cash", credit_days=30) == d
    assert compute_due_date(d) is None and compute_due_date(None, credit_days=30) is None


def test_guardrail_fields():
    """No lines or no date blocks; a missing supplier or number only needs review."""
    assert blocking_fields(ExtractedDocument.model_validate(invoice_doc())) == []
    assert blocking_fields(ExtractedDocument.model_validate(invoice_doc(lines=[], total=0))) == ["invoice.lines"]
    assert blocking_fields(ExtractedDocument.model_validate(invoice_doc(date=None))) == ["invoice.invoice_date"]
    assert "invoice.lines" in blocking_fields(ExtractedDocument.model_validate(invoice_doc(lines=[invoice_line(" ", 1, 1)])))
    assert blocking_fields(ExtractedDocument.model_validate(receipt_doc())) == []
    assert blocking_fields(ExtractedDocument(doc_type="receipt")) == ["receipt.payments"]
    partial = ExtractedDocument.model_validate(invoice_doc(supplier=None, number=None))
    assert blocking_fields(partial) == [] and missing_fields(partial) == ["supplier.name", "invoice.invoice_number"]


def test_settings_from_env(monkeypatch):
    """Defaults follow the proposal; environment variables override them."""
    for name in ("INVOICE_VISION_MODEL", "ALLOWED_ORIGINS", "DATABASE_URL", "SKU_MATCH_THRESHOLD", "SUPABASE_URL",
                 "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(name, raising=False)
    defaults = Settings.from_env()
    assert (defaults.vision_model, defaults.sku_match_model, defaults.sku_match_threshold) == ("gpt-4o", "gpt-4o-mini", 0.82)
    assert defaults.database_url is None and not defaults.storage_configured
    assert defaults.default_business_id == DEFAULT_BUSINESS_ID and "http://localhost:3001" in defaults.allowed_origins
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://businesssense.vercel.app/, http://localhost:3001")
    monkeypatch.setenv("INVOICE_VISION_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("SKU_MATCH_THRESHOLD", "0.9")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co/")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "sb_secret_x")
    custom = Settings.from_env()
    assert custom.allowed_origins == ("https://businesssense.vercel.app", "http://localhost:3001")
    assert (custom.vision_model, custom.sku_match_threshold) == ("gpt-4o-mini", 0.9)
    assert custom.supabase_url == "https://x.supabase.co" and custom.storage_configured


# ---- cash flow, purchase analysis, dashboard ---------------------------------------------------
def summary(**fields) -> InvoiceSummary:
    """A saved invoice row with sensible defaults."""
    base = {"id": "i", "business_id": DEFAULT_BUSINESS_ID, "status": "verified", "payment_status": "unpaid",
            "total": 100.0, "invoice_date": date(2026, 10, 1), "supplier_id": "s1", "supplier_name": "A"}
    return InvoiceSummary.model_validate({**base, **fields})


def test_supplier_totals():
    """Outstanding = verified and not paid; drafts are counted; rejected invoices are left out."""
    invoices = [
        summary(id="1", total=1000.0, due_date=date(2026, 11, 20)),
        summary(id="2", total=500.5, payment_status="paid", due_date=date(2026, 11, 1)),
        summary(id="3", total=250.25, due_date=date(2026, 11, 10)),
        summary(id="4", status="draft", total=999.0),
        summary(id="5", status="rejected", total=777.0),
    ]
    totals = supplier_totals(invoices)
    assert (totals.invoice_count, totals.draft_count) == (4, 1)
    assert (totals.verified_total, totals.outstanding) == (1750.75, 1250.25)
    assert totals.next_due_date == date(2026, 11, 10)
    empty = supplier_totals([])
    assert (empty.invoice_count, empty.outstanding, empty.next_due_date) == (0, 0.0, None)


def test_dashboard_combines_purchases_and_payments():
    """Review queue, 12 months of purchases, top suppliers and payments due in 30 days."""
    today = date(2026, 11, 9)
    invoices = [
        summary(id="1", total=1000.0, invoice_date=date(2026, 10, 3), supplier_id="s1", supplier_name="A",
                due_date=date(2026, 11, 2)),
        summary(id="2", total=200.0, invoice_date=date(2026, 10, 20), supplier_id="s2", supplier_name="B",
                due_date=date(2026, 11, 30)),
        summary(id="3", total=50.0, invoice_date=date(2026, 9, 1), supplier_id="s2", supplier_name="B",
                payment_status="paid", due_date=date(2026, 10, 1)),
        summary(id="4", total=70.0, invoice_date=date(2025, 1, 1), due_date=date(2027, 1, 1)),
        summary(id="5", status="draft", total=999.0),
        summary(id="6", status="draft", total=None),
    ]
    dash = build_dashboard(invoices, today=today)
    assert (dash.review_queue, dash.verified_invoices) == (2, 4)
    assert len(dash.monthly_purchases) == 12 and dash.monthly_purchases[-1].month == "2026-11"
    by_month = {m.month: m.total for m in dash.monthly_purchases}
    assert (by_month["2026-10"], by_month["2026-09"], by_month["2026-11"]) == (1200.0, 50.0, 0.0)
    assert [(s.supplier_name, s.total) for s in dash.supplier_spend] == [("A", 1070.0), ("B", 250.0)]
    assert [(p.invoice_id, p.days_left) for p in dash.payments_due] == [("1", -7), ("2", 21)]
    assert dash.forecast is None and "Model 2" in dash.forecast_note


def test_purchase_analysis_and_payments_due_directly():
    """The building blocks: month keys across a year boundary, spend ranking, due-date window."""
    assert last_months(date(2026, 2, 15), 4) == ["2025-11", "2025-12", "2026-01", "2026-02"]
    rows = [summary(id="1", total=10.0, invoice_date=date(2026, 1, 5)),
            summary(id="2", total=5.0, invoice_date=date(2026, 2, 1), supplier_id="s2", supplier_name="B"),
            summary(id="3", status="draft", total=99.0, invoice_date=date(2026, 2, 2))]
    assert [(m.month, m.total) for m in monthly_purchases(rows, today=date(2026, 2, 15), months=2)] == [("2026-01", 10.0), ("2026-02", 5.0)]
    assert [s.supplier_id for s in supplier_spend(rows, limit=1)] == ["s1"]
    due = [summary(id="a", due_date=date(2026, 3, 20)), summary(id="b", due_date=date(2026, 3, 1))]
    assert [p.invoice_id for p in payments_due(due, today=date(2026, 2, 28), window_days=30)] == ["b", "a"]
    assert payments_due(due, today=date(2026, 2, 28), window_days=0) == []


def test_payable_cash_series_buckets_and_rolls_overdue():
    """Forward series length matches the window; overdue cash lands on today."""
    today = date(2026, 11, 10)
    invoices = [
        summary(id="over", total=100.0, amount_paid=0.0, due_date=date(2026, 11, 1),
                supplier_name="A", invoice_number="O-1"),
        summary(id="soon", total=250.0, amount_paid=50.0, due_date=date(2026, 11, 12),
                supplier_name="B", invoice_number="S-1"),
        summary(id="far", total=80.0, due_date=date(2026, 12, 20)),
        summary(id="paid", total=40.0, payment_status="paid", due_date=date(2026, 11, 11)),
    ]
    series = payable_cash_series(invoices, today=today, window_days=5)
    assert len(series) == 5 and series[0].day == today and series[-1].day == date(2026, 11, 14)
    assert series[0].total_owed == 100.0 and [r.invoice_id for r in series[0].invoices] == ["over"]
    assert series[2].total_owed == 200.0 and series[2].invoices[0].still_owed == 200.0
    assert all(day.total_owed == 0.0 for day in series if day.day not in (today, date(2026, 11, 12)))
    overdue = overdue_balances(invoices, today=today)
    assert overdue.total_owed == 100.0 and overdue.invoice_count == 1
    assert overdue.invoices[0].days_overdue == 9 and overdue.invoices[0].invoice_id == "over"


def test_monthly_paid_and_spend_pies():
    """Paid uses amount_paid on invoices dated that month; pies group supplier and product."""
    today = date(2026, 11, 15)
    invoices = [
        summary(id="1", total=1000.0, amount_paid=400.0, invoice_date=date(2026, 11, 2),
                supplier_id="s1", supplier_name="A"),
        summary(id="2", total=500.0, amount_paid=500.0, invoice_date=date(2026, 10, 5),
                supplier_id="s2", supplier_name="B", payment_status="paid"),
        summary(id="3", status="draft", total=999.0, amount_paid=0.0, invoice_date=date(2026, 11, 1)),
    ]
    monthly = monthly_purchases_and_paid(invoices, today=today, months=2)
    assert [(m.month, m.purchases, m.paid) for m in monthly] == [
        ("2026-10", 500.0, 500.0),
        ("2026-11", 1000.0, 400.0),
    ]
    suppliers = spend_slices_by_supplier(invoices, today=today, months=2)
    assert [(s.name, s.total) for s in suppliers] == [("A", 1000.0), ("B", 500.0)]
    lines = [
        SupplierLine(invoice_id="1", item_name="Pipe", product_id="p1", product_name="Pipe 63", line_total=700.0),
        SupplierLine(invoice_id="1", item_name="Odd", product_id=None, product_name=None, line_total=300.0),
        SupplierLine(invoice_id="2", item_name="Pipe", product_id="p1", product_name="Pipe 63", line_total=500.0),
    ]
    items = spend_slices_by_item(lines)
    assert [(i.name, i.total) for i in items] == [("Pipe 63", 1200.0), ("Unmatched", 300.0)]
    assert abs(sum(i.share for i in items) - 1.0) < 1e-9


# ---- supplier payments ---------------------------------------------------------------------------
def pay(amount: float | None = 500.0, status: str = "confirmed", **extra) -> PaymentRecord:
    """A saved supplier payment."""
    fields = {"id": extra.pop("id", "p1"), "business_id": DEFAULT_BUSINESS_ID, "supplier_id": "s1",
              "amount": amount, "paid_on": date(2026, 10, 25), "status": status, **extra}
    return PaymentRecord.model_validate(fields)


def test_invoice_payment_status_from_payments():
    """Paid once confirmed payments cover the total; unconfirmed while one waits; rejected ones never count."""
    assert invoice_payment_status(900.0, "unpaid", [pay(900.0)]) == "paid"
    assert invoice_payment_status(900.0, "unpaid", [pay(400.0), pay(499.5)]) == "paid"  # within Rs 1
    assert invoice_payment_status(900.0, "unpaid", [pay(400.0)]) == "unpaid"  # part paid
    assert invoice_payment_status(900.0, "unpaid", [pay(400.0), pay(500.0, "unconfirmed")]) == "unconfirmed"
    assert invoice_payment_status(900.0, "paid", [pay(400.0)]) == "unpaid"  # the records decide
    assert invoice_payment_status(900.0, "unconfirmed", [pay(900.0, "rejected")]) == "unpaid"
    assert invoice_payment_status(900.0, "paid", []) == "paid"  # marked paid by hand, nothing recorded
    assert invoice_payment_status(None, "unpaid", [pay(900.0)]) == "unpaid"


def test_confirm_blockers():
    """A confirmed payment needs a supplier, amount and date, a verified invoice, and no overpayment."""
    today = date(2026, 11, 1)
    verified = InvoiceSummary(id="i1", business_id=DEFAULT_BUSINESS_ID, invoice_number="T-1", total=900.0,
                              status="verified", supplier_id="s1")
    assert confirm_blockers(pay(900.0, "unconfirmed"), verified, 0.0, tolerance=1.0, today=today) == []
    assert confirm_blockers(pay(901.0, "unconfirmed"), verified, 0.0, tolerance=1.0, today=today) == []
    [over] = confirm_blockers(pay(600.0, "unconfirmed"), verified, 400.0, tolerance=1.0, today=today)
    assert over == "Rs 600.00 is more than the Rs 500.00 still owed on invoice T-1."
    draft = verified.model_copy(update={"status": "draft"})
    assert confirm_blockers(pay(100.0, "unconfirmed"), draft, 0.0, tolerance=1.0, today=today) == ["Verify invoice T-1 first."]
    empty = pay(None, "unconfirmed", supplier_id=None, paid_on=None)
    assert confirm_blockers(empty, None, 0.0, tolerance=1.0, today=today) == [
        "Pick the supplier.", "Enter the amount paid.", "Enter the payment date."]
    future = pay(100.0, "unconfirmed", paid_on=date(2026, 12, 1))
    assert confirm_blockers(future, None, 0.0, tolerance=1.0, today=today) == ["The payment date 2026-12-01 is in the future."]
    unknown = pay(100.0, "unconfirmed", invoice_number_raw="T-9")
    assert "T-9" in confirm_blockers(unknown, None, 0.0, tolerance=1.0, today=today)[0]
    assert confirm_blockers(pay(100.0, "unconfirmed"), None, 0.0, tolerance=1.0, today=today) == []  # not for an invoice
    assert "rejected" in confirm_blockers(pay(100.0, "rejected"), None, 0.0, tolerance=1.0, today=today)[0]


def test_supplier_totals_with_payments_and_reminders():
    """Outstanding subtracts confirmed payments; unconfirmed ones are counted apart; old ones get a reminder."""
    owed = InvoiceSummary(id="i1", business_id="b", status="verified", total=1000.0, amount_paid=400.0,
                          payment_status="unpaid", due_date=date(2026, 11, 5))
    assert still_owed(owed) == 600.0 and still_owed(owed.model_copy(update={"amount_paid": 1500.0})) == 0.0
    payments = [pay(400.0), pay(250.0, "unconfirmed", id="p2"), pay(99.0, "rejected", id="p3")]
    totals = supplier_totals([owed], payments)
    assert (totals.outstanding, totals.paid_total, totals.unconfirmed_total, totals.unconfirmed_count) == (600.0, 400.0, 250.0, 1)
    [due] = payments_due([owed], today=date(2026, 11, 1))
    assert (due.total, due.amount_paid, due.still_owed, due.days_left) == (1000.0, 400.0, 600.0, 4)
    old = pay(10.0, "unconfirmed", id="old", reminder_due=True, created_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
    older = pay(10.0, "unconfirmed", id="older", reminder_due=True, created_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert [p.id for p in reminders([pay(10.0, "unconfirmed"), old, older])] == ["older", "old"]
