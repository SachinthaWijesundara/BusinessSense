"""Tests for Model 2 (purchase spend forecast) and the Purchasing Cash Planner rules. Fictional data."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from database.client import InvoiceSummary, PaymentRecord
from ml.spend import (
    SpendForecastModel,
    add_months,
    cash_credit_split,
    choose,
    forecast_months,
    hw_fit,
    hw_forecast,
    monthly_spend,
    rolling_mean,
    seasonal_naive,
    to_bytes,
    train_purchase_spend,
)
from services.cash_flow_service import credit_mix, due_inside, period_spend, plan_cash
from tests.conftest import MemoryStorage

TODAY = date(2026, 11, 10)
TRAIN_TODAY = date(2026, 5, 10)  # just after the 40 months of seasonal_invoices()


def inv(n: int, day: date, total: float, *, status: str = "verified", supplier: str = "s1", due: date | None = None,
        paid: float = 0.0, payment_status: str = "unpaid", payment_type: str | None = None) -> InvoiceSummary:
    """One invoice summary."""
    return InvoiceSummary(id=f"i{n}", business_id="b", supplier_id=supplier, supplier_name=f"Supplier {supplier}",
                          invoice_number=f"N-{n}", invoice_date=day, total=total, status=status, due_date=due,
                          amount_paid=paid, payment_status=payment_status, payment_type=payment_type)


def seasonal_invoices(months: int = 40, start: date = date(2023, 1, 1), noise: bool = True) -> list[InvoiceSummary]:
    """Monthly purchases with a yearly pattern (December high, July low), all paid."""
    pattern = [100, 90, 120, 130, 100, 95, 70, 80, 95, 105, 120, 160]
    out = []
    for i in range(months):
        month = add_months(start, i)
        total = pattern[month.month - 1] * 1000 * (1 + 0.01 * i) * (1 + (0.05 if noise and i % 3 == 0 else 0))
        out.append(inv(i, month + timedelta(days=9), round(total, 2), payment_status="paid", paid=total))
    return out


# ---- Model 2 ------------------------------------------------------------------------------------
def test_monthly_spend_counts_full_months_and_fills_gaps():
    """Verified totals per month up to the last full month; empty months are 0; this month apart."""
    invoices = [inv(1, date(2026, 7, 3), 100), inv(2, date(2026, 7, 20), 50), inv(3, date(2026, 9, 1), 30),
                inv(4, date(2026, 11, 2), 70), inv(5, date(2026, 8, 1), 999, status="draft"), inv(6, date(2027, 1, 1), 5),
                inv(7, date(2026, 8, 5), 0)]
    history = monthly_spend(invoices, today=TODAY)
    assert [m.isoformat()[:7] for m in history.months] == ["2026-07", "2026-08", "2026-09", "2026-10"]
    assert history.totals == [150.0, 0.0, 30.0, 0.0] and history.spent_this_month == 70.0
    assert history.report["dropped"] == {"not verified": 1, "date outside the archive": 1, "no total": 1}


def test_baselines_and_smoothing():
    """Seasonal-naive repeats last year; rolling mean is flat; smoothing follows a clean yearly pattern."""
    totals = [float(v) for v in range(1, 25)]
    assert seasonal_naive(totals, 3) == [13.0, 14.0, 15.0] and rolling_mean(totals, 2) == [23.0, 23.0]
    series = [100 + 20 * math.sin(2 * math.pi * t / 12) for t in range(36)]
    ahead = hw_forecast(series, hw_fit(series), 12)
    assert ahead == pytest.approx(series[:12], abs=6)


def test_a_learner_is_kept_only_if_it_beats_both_baselines():
    """Proposal: compared with seasonal-naive and rolling-mean baselines; kept only if it wins."""
    def scored(**wape):
        names = ("seasonal naive", "rolling mean", "exponential smoothing", "lagged regression")
        return {n: {"predictions": 12, "mae": 1.0, "wape": wape.get(n.split()[0], 50.0)} for n in names}

    assert choose(scored(seasonal=20, rolling=30, exponential=15, lagged=25)) == ("exponential smoothing", True)
    assert choose(scored(seasonal=20, rolling=30, exponential=20, lagged=25)) == ("seasonal naive", False)
    assert choose(scored(seasonal=float("nan"), rolling=float("nan"), exponential=float("nan"), lagged=float("nan")))[1] is False


def test_train_model_2_reports_and_forecasts():
    """Trained on a seasonal history: a fair test on 12 months, a range, and forecasts from the file."""
    invoices = seasonal_invoices()
    artifact, report = train_purchase_spend(invoices, today=TRAIN_TODAY, business_id="shop")
    assert len(report["backtest"]["months"]) == 12 and report["chosen"] in (
        "seasonal naive", "rolling mean", "exponential smoothing", "lagged regression")
    assert report["backtest"]["methods"]["seasonal naive"]["wape"] < report["backtest"]["methods"]["rolling mean"]["wape"]
    assert report["cash_credit_split"]["available"] is False
    model = SpendForecastModel.from_bytes(to_bytes(artifact))
    history = monthly_spend(invoices, today=date(2026, 5, 15))
    ahead = model.forecast(history.months, history.totals, 3)
    assert [f.month.isoformat()[:7] for f in ahead] == ["2026-05", "2026-06", "2026-07"]
    assert all(f.low is not None and f.low <= f.expected <= f.high for f in ahead)
    with pytest.raises(ValueError, match="Not enough purchase history"):
        train_purchase_spend(seasonal_invoices(12), today=date(2024, 1, 10), business_id="shop")


def test_without_a_model_the_forecast_is_a_labelled_rolling_mean():
    """No trained Model 2 (or little history): the mean of the last 3 months, no range."""
    ahead = forecast_months([date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)], [90.0, 120.0, 150.0], 2)
    assert [(f.expected, f.low, f.is_baseline, f.method) for f in ahead] == [(120.0, None, True, "rolling mean")] * 2


def test_cash_credit_split_only_when_recorded():
    """Estimated only when most recent invoices record cash or credit."""
    few = [inv(1, date(2026, 9, 1), 100, payment_type="cash"), inv(2, date(2026, 9, 2), 100)]
    assert cash_credit_split(few, today=TODAY)["available"] is False
    most = [inv(n, date(2026, 9, 1), 100, payment_type="cash" if n < 3 else "credit") for n in range(10)]
    assert cash_credit_split(most, today=TODAY) | {"note": ""} == {"available": True, "recorded_pct": 100.0,
                                                                  "cash_share_pct": 30.0, "note": ""}
    # Future-dated invoices must not be counted in recent 1-year lookback
    future = [inv(1, TODAY + timedelta(days=30), 100, payment_type="cash")]
    assert cash_credit_split(future, today=TODAY)["available"] is False


@pytest.mark.anyio
async def test_model_2_loads_from_storage():
    """The file ml/train.py uploads loads back from the models bucket."""
    storage = MemoryStorage()
    assert await SpendForecastModel.from_storage(storage, "shop") is None
    artifact, _ = train_purchase_spend(seasonal_invoices(), today=TRAIN_TODAY, business_id="shop")
    await storage.put("shop/purchase_spend.joblib", to_bytes(artifact), "application/octet-stream")
    assert (await SpendForecastModel.from_storage(storage, "shop")).version == artifact["version"]


# ---- the planner rules --------------------------------------------------------------------------
def test_period_spend_spreads_months_over_days():
    """This month: what is left after the spend so far, over the days left; next month: evenly."""
    forecasts = forecast_months([date(2026, 9, 1), date(2026, 10, 1)], [300.0, 300.0], 2)  # 300 a month
    rows = period_spend(forecasts, today=date(2026, 11, 21), days=20, spent_this_month=100.0)
    assert rows[0][1] == pytest.approx(200 / 10) and rows[-1][1] == pytest.approx(300 / 31)
    assert sum(a for _, a in rows) == pytest.approx(200 + 300 / 31 * 10)


def test_new_purchases_fall_due_by_credit_days():
    """Cash suppliers fall due at once; 30-day credit purchases fall due after the period."""
    mix = credit_mix([inv(1, date(2026, 10, 1), 300, supplier="cash"), inv(2, date(2026, 10, 2), 700, supplier="credit")],
                     {"cash": 0, "credit": 30}, today=TODAY)
    assert mix == {0: 0.3, 30: 0.7}
    daily = [(TODAY + timedelta(days=d), 10.0) for d in range(30)]
    assert due_inside(daily, mix, today=TODAY, days=30) == pytest.approx(300 * 0.3)
    assert credit_mix([inv(1, date(2026, 10, 1), 10, supplier="x")], {"x": None}, today=TODAY) == {30: 1.0}
    # Future-dated invoices must be excluded from credit mix calculation
    future_inv = inv(3, TODAY + timedelta(days=60), 500, supplier="cash")
    assert credit_mix([future_inv], {"cash": 0}, today=TODAY) == {30: 1.0}


def test_cash_plan_adds_bills_due_and_new_purchases():
    """Reserve = bills due in the period (overdue included) + new purchases falling due inside it;
    unconfirmed payments only change the assumed figure."""
    history = seasonal_invoices(30, start=date(2024, 5, 1))
    bills = [inv(100, date(2026, 10, 1), 1000, due=date(2026, 11, 1), paid=400),  # overdue, 600 left
             inv(101, date(2026, 10, 20), 500, due=date(2026, 11, 20)),
             inv(102, date(2026, 11, 1), 800, due=date(2027, 2, 1)),  # after the period
             inv(103, date(2026, 11, 2), 300, supplier="none")]  # no credit days: no due date
    waiting = [PaymentRecord(id="p1", business_id="b", invoice_id="i101", amount=200.0, status="unconfirmed")]
    plan = plan_cash(today=TODAY, days=30, invoices=history + bills, credit_days={"s1": 30, "none": None},
                     unconfirmed=waiting, model=None)
    assert [o.invoice_number for o in plan.obligations] == ["N-100", "N-101"]
    assert (plan.bills_due, plan.overdue) == (1100.0, 600.0)
    assert [(d.day, d.amount, d.cumulative) for d in plan.due_by_day] == [(TODAY, 600.0, 600.0), (date(2026, 11, 20), 500.0, 1100.0)]
    assert [o.invoice_number for o in plan.no_due_date] == ["N-103"]
    assert plan.reserve == pytest.approx(plan.bills_due + plan.new_purchases_due.expected)
    assert plan.reserve_if_unconfirmed_are_paid == pytest.approx(plan.reserve - 200)
    assert plan.model.trained is False and plan.period_spend.is_baseline and plan.reserve_high is None
    assert "purchasing cash requirement only" in plan.notes[0]
