"""Deterministic invoice rules: the math audit, date checks and due dates.

Plain Python, no LLM. Money is computed with ``Decimal`` so 0.1 + 0.2 style float noise can never
flip an audit result. The audit is the gate between an agent's draft and the verified ledger:
an invoice that does not add up stays a draft and never reaches Model 1 or Model 2.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from agentic.schemas import InvoiceDraft, usable_lines

DEFAULT_TOLERANCE = 1.0  # rupees, per the proposal (MathAuditNode: ± Rs 1)
EARLIEST_PLAUSIBLE_DATE = date(2015, 1, 1)  # the shop's archive starts in 2019


@dataclass(frozen=True)
class AuditIssue:
    code: str
    message: str
    line_index: int | None = None
    field: str | None = None
    expected: float | None = None
    actual: float | None = None

    def to_dict(self) -> dict:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class MathAuditResult:
    ok: bool
    issues: list[AuditIssue]
    computed_total: float | None

    def issues_as_dicts(self) -> list[dict]:
        return [issue.to_dict() for issue in self.issues]


def _dec(value: float | int | str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01")))


def _fmt(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,}"


def math_audit(invoice: InvoiceDraft, tolerance: float = DEFAULT_TOLERANCE) -> MathAuditResult:
    """Check every line (qty x rate - discount = line total) and the invoice total.

    Invoice total rule: sum(line totals) - special discount + tax = total, within ``tolerance``.
    ``line_index`` refers to the position among usable lines (blank rows removed), which is the
    same order the lines are stored in.
    """
    tol = Decimal(str(tolerance))
    issues: list[AuditIssue] = []
    lines = usable_lines(invoice)
    if not lines:
        issues.append(AuditIssue("no_lines", "The invoice has no item lines."))

    line_sum = Decimal("0")
    line_sum_complete = bool(lines)
    for index, line in enumerate(lines):
        label = f"Line {index + 1}"
        quantity = _dec(line.quantity)
        rate = _dec(line.unit_rate)
        discount = _dec(line.discount) or Decimal("0")
        total = _dec(line.line_total)

        missing = [
            name
            for name, value in (("quantity", quantity), ("unit rate", rate), ("line total", total))
            if value is None
        ]
        if missing:
            issues.append(
                AuditIssue(
                    "missing_value",
                    f"{label}: missing {', '.join(missing)}.",
                    line_index=index,
                )
            )
            if total is None:
                line_sum_complete = False
            else:
                line_sum += total
            continue

        assert quantity is not None and rate is not None and total is not None
        if quantity < 0 or rate < 0 or discount < 0 or total < 0:
            issues.append(
                AuditIssue("negative_value", f"{label}: a value is negative.", line_index=index)
            )

        expected = quantity * rate - discount
        if abs(expected - total) > tol:
            issues.append(
                AuditIssue(
                    "line_total_mismatch",
                    (
                        f"{label}: {quantity.normalize():f} x {_fmt(rate)} - {_fmt(discount)} "
                        f"= {_fmt(expected)}, but the line total says {_fmt(total)}."
                    ),
                    line_index=index,
                    expected=_money(expected),
                    actual=_money(total),
                )
            )
        line_sum += total

    special_discount = _dec(invoice.special_discount) or Decimal("0")
    tax = _dec(invoice.tax) or Decimal("0")
    computed_total = line_sum - special_discount + tax if line_sum_complete else None

    invoice_total = _dec(invoice.total)
    if invoice_total is None:
        issues.append(AuditIssue("missing_total", "The invoice total is missing.", field="total"))
    elif computed_total is not None and abs(computed_total - invoice_total) > tol:
        issues.append(
            AuditIssue(
                "invoice_total_mismatch",
                (
                    f"Line totals - special discount + tax = {_fmt(computed_total)}, "
                    f"but the invoice total says {_fmt(invoice_total)}."
                ),
                field="total",
                expected=_money(computed_total),
                actual=_money(invoice_total),
            )
        )

    return MathAuditResult(
        ok=not issues,
        issues=issues,
        computed_total=None if computed_total is None else _money(computed_total),
    )


def parse_iso_date(value: str | date | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def date_issues(invoice: InvoiceDraft, *, today: date | None = None) -> list[AuditIssue]:
    """Flag invoice dates that cannot be right. These keep an invoice in draft for review."""
    today = today or date.today()
    issues: list[AuditIssue] = []
    for field_name in ("invoice_date", "delivery_date"):
        raw = getattr(invoice, field_name)
        if raw is None or not str(raw).strip():
            continue
        parsed = parse_iso_date(raw)
        label = field_name.replace("_", " ")
        if parsed is None:
            issues.append(
                AuditIssue("invalid_date", f"The {label} '{raw}' is not a date.", field=field_name)
            )
        elif parsed > today + timedelta(days=1):
            issues.append(
                AuditIssue("future_date", f"The {label} {parsed} is in the future.", field=field_name)
            )
        elif parsed < EARLIEST_PLAUSIBLE_DATE:
            issues.append(
                AuditIssue(
                    "implausible_date",
                    f"The {label} {parsed} is older than the shop's archive.",
                    field=field_name,
                )
            )
    return issues


def compute_due_date(
    invoice_date: date | None,
    *,
    payment_type: str | None = None,
    credit_days: int | None = None,
) -> date | None:
    """Due date = invoice date + credit days. Cash invoices fall due on the invoice date."""
    if invoice_date is None:
        return None
    if payment_type == "cash":
        return invoice_date
    if credit_days is None:
        return None
    return invoice_date + timedelta(days=credit_days)
