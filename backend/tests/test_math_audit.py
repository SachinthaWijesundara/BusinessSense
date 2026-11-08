from __future__ import annotations

import pytest

from agentic.schemas import InvoiceDraft, InvoiceLineDraft
from services.invoice_service import math_audit


def line(item="63mm uPVC pipe", qty=10.0, rate=585.0, discount=0.0, total=None) -> InvoiceLineDraft:
    return InvoiceLineDraft(
        item_name=item,
        quantity=qty,
        mro=rate,
        discount=discount,
        line_total=(qty * rate - discount) if total is None else total,
    )


def invoice(lines, total=None, special_discount=None, tax=None) -> InvoiceDraft:
    if total is None:
        total = sum(item.line_total or 0 for item in lines) - (special_discount or 0) + (tax or 0)
    return InvoiceDraft(lines=lines, total=total, special_discount=special_discount, tax=tax)


def codes(result) -> list[str]:
    return [issue.code for issue in result.issues]


def test_clean_invoice_passes():
    result = math_audit(invoice([line(), line("Elbow 63mm", 4, 120.0)]))
    assert result.ok
    assert result.issues == []
    assert result.computed_total == 6330.0


def test_line_total_mismatch_is_caught():
    result = math_audit(invoice([line(total=5580.0)], total=5580.0))
    assert not result.ok
    assert codes(result) == ["line_total_mismatch"]
    issue = result.issues[0]
    assert (issue.line_index, issue.expected, issue.actual) == (0, 5850.0, 5580.0)


def test_invoice_total_mismatch_is_caught():
    result = math_audit(invoice([line()], total=6850.0))
    assert codes(result) == ["invoice_total_mismatch"]
    assert result.issues[0].expected == 5850.0


@pytest.mark.parametrize(("diff", "ok"), [(0.0, True), (1.0, True), (1.01, False), (-1.0, True), (-1.01, False)])
def test_tolerance_is_plus_minus_one_rupee(diff, ok):
    assert math_audit(invoice([line(total=5850.0 + diff)], total=5850.0 + diff)).ok is ok


def test_custom_tolerance():
    assert math_audit(invoice([line(total=5853.0)], total=5853.0), tolerance=5).ok


def test_discount_rupees_are_subtracted_per_line():
    assert math_audit(invoice([line(discount=292.5)])).ok
    assert not math_audit(invoice([line(discount=292.5, total=5850.0)], total=5850.0)).ok


def test_special_discount_and_tax_apply_to_invoice_total():
    lines = [line(), line("Elbow 63mm", 4, 120.0)]
    assert math_audit(invoice(lines, total=6330.0 - 330.0 + 108.0, special_discount=330.0, tax=108.0)).ok
    assert not math_audit(invoice(lines, total=6330.0, special_discount=330.0)).ok


def test_float_noise_never_fails_the_audit():
    lines = [line("Washer", 3, 0.1, total=0.3), line("Nut", 1, 0.2, total=0.2)]
    assert math_audit(invoice(lines, total=0.5), tolerance=0.0).ok


def test_missing_values_fail_with_reason():
    result = math_audit(invoice([InvoiceLineDraft(item_name="Pipe", quantity=None, mro=585.0, line_total=5850.0)], total=5850.0))
    assert codes(result) == ["missing_value"]
    assert "quantity" in result.issues[0].message


def test_missing_line_total_skips_total_check_but_fails():
    result = math_audit(invoice([InvoiceLineDraft(item_name="Pipe", quantity=10, mro=585.0)], total=5850.0))
    assert codes(result) == ["missing_value"]
    assert result.computed_total is None


def test_missing_invoice_total():
    result = math_audit(InvoiceDraft(lines=[line()], total=None))
    assert codes(result) == ["missing_total"]


def test_no_lines():
    result = math_audit(InvoiceDraft(lines=[], total=0))
    assert codes(result) == ["no_lines"]


def test_blank_rows_are_ignored_and_indexes_skip_them():
    lines = [line(), InvoiceLineDraft(item_name="   "), line("Elbow", 2, 100.0, total=250.0)]
    result = math_audit(invoice(lines, total=6100.0))
    assert codes(result) == ["line_total_mismatch"]
    assert result.issues[0].line_index == 1  # second usable line, not third row


def test_negative_values_are_flagged():
    result = math_audit(invoice([line(qty=-1, total=-585.0)], total=-585.0))
    assert "negative_value" in codes(result)


def test_issue_dicts_drop_empty_keys():
    result = math_audit(invoice([line(total=1.0)], total=1.0))
    as_dict = result.issues_as_dicts()[0]
    assert as_dict["code"] == "line_total_mismatch"
    assert "field" not in as_dict
