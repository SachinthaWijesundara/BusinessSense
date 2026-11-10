"""Model 2, Purchasing Expenditure Forecast (proposal section 6): how much the shop is likely to
spend on purchases per month.

1. ``monthly_spend``  verified invoice totals per calendar month, from the first month in the
   archive to the last full month (the current month is still running, so it is not a target).
   Months without purchases count as 0. Drafts, rejected invoices, missing or future dates and
   zero totals are left out and counted.
2. Baselines: seasonal-naive (the same month last year) and the rolling mean of the last 3 months.
3. Candidates: exponential smoothing (Holt-Winters: level, damped trend and a yearly pattern) and a
   regression on lagged spend (ridge on the last 1, 2, 3 and 12 months and the time of year).
4. Fair test in time order (rolling origin): each of the last 12 months is predicted only from
   the months before it. Mean absolute error and weighted percentage error are compared.
5. A learner is kept only if its weighted percentage error beats BOTH baselines; otherwise the
   better baseline is the model.
6. The range is the 10%-90% spread of the chosen method's past errors (relative to the forecast).

The forecast feeds the Purchasing Cash Planner, whose cash requirement is worked out by plain
rules (services/cash_flow_service.py). A cash-versus-credit split is only estimated when past
invoices record cash or credit often enough.
"""

from __future__ import annotations

import io
import itertools
import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import joblib
import numpy as np

from database.client import InvoiceSummary

MIN_MONTHS = 24  # full months needed to train (a seasonal baseline plus a year of tests)
EVAL_MONTHS = 12
HORIZON = 4  # months forecast ahead (the current month and the next three)
RANGE_QUANTILES = (0.1, 0.9)  # the range covers 80% of past errors
SPLIT_RECORDED_SHARE = 0.8  # cash/credit split only when 80% of recent invoices record it
EARLIEST_DATE = date(2015, 1, 1)
BASELINES = ("seasonal naive", "rolling mean")
LEARNERS = ("exponential smoothing", "lagged regression")
ARTIFACT_FORMAT = 1


def month_start(day: date) -> date:
    """The first day of a day's month."""
    return day.replace(day=1)


def add_months(month: date, count: int) -> date:
    """A month ``count`` months later (or earlier, when negative)."""
    index = month.year * 12 + month.month - 1 + count
    return date(index // 12, index % 12 + 1, 1)


@dataclass
class SpendHistory:
    """Monthly purchase spend up to the last full month, and this month's spend so far."""

    months: list[date]
    totals: list[float]
    current_month: date
    spent_this_month: float
    report: dict[str, Any]


def monthly_spend(invoices: list[InvoiceSummary], *, today: date) -> SpendHistory:
    """Verified purchase totals per month (gaps filled with 0), with a cleaning report."""
    dropped: dict[str, int] = {}
    by_month: dict[date, float] = {}
    current = month_start(today)
    spent_now = 0.0
    used = 0
    for inv in invoices:
        reason = None
        if inv.status != "verified":
            reason = "not verified"
        elif inv.invoice_date is None:
            reason = "no invoice date"
        elif inv.invoice_date > today or inv.invoice_date < EARLIEST_DATE:
            reason = "date outside the archive"
        elif inv.total is None or inv.total <= 0:
            reason = "no total"
        if reason:
            dropped[reason] = dropped.get(reason, 0) + 1
            continue
        assert inv.invoice_date is not None and inv.total is not None
        used += 1
        month = month_start(inv.invoice_date)
        if month == current:
            spent_now += float(inv.total)
        else:
            by_month[month] = by_month.get(month, 0.0) + float(inv.total)
    months: list[date] = []
    if by_month:
        month = min(by_month)
        while month < current:
            months.append(month)
            month = add_months(month, 1)
    return SpendHistory(months=months, totals=[round(by_month.get(m, 0.0), 2) for m in months], current_month=current,
                        spent_this_month=round(spent_now, 2),
                        report={"invoices_in": len(invoices), "used": used, "dropped": dropped, "full_months": len(months)})


def cash_credit_split(invoices: list[InvoiceSummary], *, today: date) -> dict[str, Any]:
    """The cash share of the last year's purchases, only when cash/credit is recorded often enough."""
    recent = [inv for inv in invoices if inv.status == "verified" and inv.invoice_date and inv.total
              and (today - inv.invoice_date).days <= 365]
    recorded = [inv for inv in recent if inv.payment_type in ("cash", "credit")]
    share = len(recorded) / len(recent) if recent else 0.0
    if not recent or share < SPLIT_RECORDED_SHARE:
        return {"available": False, "recorded_pct": round(100 * share, 1),
                "note": f"Only {round(100 * share)}% of the last year's invoices record cash or credit, so the "
                        "cash-versus-credit split is not estimated."}
    cash = sum(float(inv.total or 0) for inv in recorded if inv.payment_type == "cash")
    total = sum(float(inv.total or 0) for inv in recorded)
    return {"available": True, "recorded_pct": round(100 * share, 1), "cash_share_pct": round(100 * cash / total, 1),
            "note": "Share of the last year's purchases paid in cash at purchase."}


# ---- baselines ------------------------------------------------------------------------------------
def seasonal_naive(totals: list[float], steps: int) -> list[float]:
    """The same month last year, for each of the next ``steps`` months."""
    return [totals[len(totals) - 12 + (h % 12)] if len(totals) >= 12 else statistics.fmean(totals[-3:])
            for h in range(steps)]


def rolling_mean(totals: list[float], steps: int, window: int = 3) -> list[float]:
    """The mean of the last ``window`` months, flat."""
    return [statistics.fmean(totals[-window:])] * steps


# ---- exponential smoothing (Holt-Winters, additive, damped trend) ---------------------------------
HW_GRID = list(itertools.product((0.1, 0.3, 0.5), (0.0, 0.1), (0.1, 0.3), (0.9, 0.98)))


def _hw_run(y: list[float], params: tuple[float, float, float, float]) -> tuple[float, float, list[float], float]:
    """Run the smoothing over the series: (level, trend, seasonal terms, one-step squared error)."""
    alpha, beta, gamma, phi = params
    level = statistics.fmean(y[:12])
    trend = (statistics.fmean(y[12:24]) - level) / 12 if len(y) >= 24 else 0.0
    season = [v - level for v in y[:12]]
    sse = 0.0
    for t in range(12, len(y)):
        s = season[t % 12]
        predicted = level + phi * trend + s
        sse += (y[t] - predicted) ** 2
        new_level = alpha * (y[t] - s) + (1 - alpha) * (level + phi * trend)
        trend = beta * (new_level - level) + (1 - beta) * phi * trend
        season[t % 12] = gamma * (y[t] - new_level) + (1 - gamma) * s
        level = new_level
    return level, trend, season, sse


def hw_fit(y: list[float]) -> tuple[float, float, float, float]:
    """The smoothing settings with the smallest one-step error on the series."""
    return min(HW_GRID, key=lambda params: _hw_run(y, params)[3])


def hw_forecast(y: list[float], params: tuple[float, float, float, float], steps: int) -> list[float]:
    """The next ``steps`` months from the smoothing (never below 0)."""
    level, trend, season, _ = _hw_run(y, params)
    phi = params[3]
    out, damp = [], 0.0
    for h in range(1, steps + 1):
        damp += phi ** h
        out.append(max(0.0, level + damp * trend + season[(len(y) + h - 1) % 12]))
    return out


# ---- regression on lagged spend -----------------------------------------------------------------
def _lag_features(history: list[float], month: date) -> list[float]:
    """Inputs for one month from the months before it."""
    return [history[-1], history[-2], history[-3], history[-12], statistics.fmean(history[-3:]),
            math.sin(2 * math.pi * month.month / 12), math.cos(2 * math.pi * month.month / 12)]


def lag_fit(months: list[date], y: list[float]) -> Any:
    """A ridge regression from the lagged months (needs 12 months of history before each target)."""
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    x = [_lag_features(y[:t], months[t]) for t in range(12, len(y))]
    model = make_pipeline(StandardScaler(), Ridge(alpha=1.0))
    model.fit(np.array(x), np.array(y[12:]))
    return model


def lag_forecast(estimator: Any, months: list[date], y: list[float], steps: int) -> list[float]:
    """The next ``steps`` months, each prediction fed back as a lag for the next (never below 0)."""
    history, out = list(y), []
    for h in range(steps):
        month = add_months(months[-1], h + 1)
        value = max(0.0, float(estimator.predict(np.array([_lag_features(history, month)]))[0]))
        out.append(value)
        history.append(value)
    return out


# ---- the fair test and the choice -----------------------------------------------------------------
def _predict(method: str, months: list[date], y: list[float], steps: int) -> list[float]:
    """Forecast ``steps`` months after the series with one method (fitting learners on it)."""
    if method == "seasonal naive":
        return seasonal_naive(y, steps)
    if method == "rolling mean":
        return rolling_mean(y, steps)
    if method == "exponential smoothing":
        return hw_forecast(y, hw_fit(y), steps)
    return lag_forecast(lag_fit(months, y), months, y, steps)


def backtest(months: list[date], y: list[float], eval_months: int = EVAL_MONTHS) -> dict[str, list[tuple[date, float, float]]]:
    """Rolling origin: each tested month predicted one month ahead from the months before it."""
    results: dict[str, list[tuple[date, float, float]]] = {name: [] for name in (*BASELINES, *LEARNERS)}
    for t in range(max(MIN_MONTHS, len(y) - eval_months), len(y)):
        for name in results:
            results[name].append((months[t], y[t], _predict(name, months[:t], y[:t], 1)[0]))
    return results


def scores(rows: list[tuple[date, float, float]]) -> dict[str, float]:
    """Mean absolute error (rupees) and weighted percentage error (total error / total spend)."""
    if not rows:
        return {"predictions": 0, "mae": float("nan"), "wape": float("nan")}
    errors = [abs(actual - predicted) for _, actual, predicted in rows]
    spend = sum(actual for _, actual, _ in rows)
    return {"predictions": len(rows), "mae": round(statistics.fmean(errors), 2),
            "wape": round(100 * sum(errors) / spend, 2) if spend else float("nan")}


def choose(method_scores: dict[str, dict[str, float]]) -> tuple[str, bool]:
    """A learner is kept only if its weighted percentage error beats BOTH baselines."""
    best_baseline = min(BASELINES, key=lambda name: method_scores[name]["wape"])
    best_learner = min(LEARNERS, key=lambda name: method_scores[name]["wape"])
    if all(method_scores[best_learner]["wape"] < method_scores[name]["wape"] for name in BASELINES):
        return best_learner, True
    return best_baseline, False


def error_range(rows: list[tuple[date, float, float]]) -> tuple[float, float]:
    """The 10%-90% spread of past errors relative to the forecast (e.g. -0.25, +0.30)."""
    rel = [actual / predicted - 1 for _, actual, predicted in rows if predicted > 0]
    if len(rel) < 3:
        return -0.3, 0.3
    low, high = (float(q) for q in np.quantile(rel, RANGE_QUANTILES))
    return min(low, 0.0), max(high, 0.0)


def range_check(rows: list[tuple[date, float, float]]) -> dict[str, Any]:
    """Set the range on the first half of the tested months, count how many later months fell inside."""
    half = len(rows) // 2
    if half < 3:
        return {"calibrated_on_months": 0, "checked_on_months": 0, "inside_pct": None}
    low, high = error_range(rows[:half])
    late = rows[half:]
    inside = sum(1 for _, actual, predicted in late if predicted * (1 + low) <= actual <= predicted * (1 + high))
    return {"calibrated_on_months": half, "checked_on_months": len(late), "inside_pct": round(100 * inside / len(late), 1)}


def train_purchase_spend(invoices: list[InvoiceSummary], *, today: date, business_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build, test, choose and fit Model 2. Returns (the model to save, the report)."""
    history = monthly_spend(invoices, today=today)
    months, y = history.months, history.totals
    if len(y) < MIN_MONTHS + 3:
        raise ValueError(f"Not enough purchase history to train Model 2: {len(y)} full months (need {MIN_MONTHS + 3}). "
                         "The planner keeps showing the rolling mean as a baseline.")
    results = backtest(months, y)
    method_scores = {name: scores(rows) for name, rows in results.items()}
    method, learned = choose(method_scores)
    best_baseline = min(BASELINES, key=lambda name: method_scores[name]["wape"])
    low, high = error_range(results[method])
    version = f"purchase_spend-{today:%Y%m%d}"
    trained_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report = {
        "model": "purchase_spend", "version": version, "trained_at": trained_at, "business_id": business_id,
        "period": "month",
        "history": {"from": months[0].isoformat()[:7], "to": months[-1].isoformat()[:7]},
        "cleaning": history.report,
        "backtest": {"months": [m.isoformat()[:7] for m, _, _ in results[method]], "methods": method_scores},
        "chosen": method, "learner_kept": learned, "best_baseline": best_baseline,
        "range_pct": {"low": round(100 * low, 1), "high": round(100 * high, 1)},
        "range_check": range_check(results[method]),
        "cash_credit_split": cash_credit_split(invoices, today=today),
    }
    artifact = {
        "format": ARTIFACT_FORMAT, "model": "purchase_spend", "version": version, "trained_at": trained_at,
        "business_id": business_id, "method": method, "learned": learned, "best_baseline": best_baseline,
        "hw_params": hw_fit(y) if method == "exponential smoothing" else None,
        "estimator": lag_fit(months, y) if method == "lagged regression" else None,
        "range": (low, high), "report": report,
    }
    return artifact, report


def to_bytes(artifact: dict[str, Any]) -> bytes:
    """The saved model file (joblib)."""
    buffer = io.BytesIO()
    joblib.dump(artifact, buffer)
    return buffer.getvalue()


def print_report(report: dict[str, Any]) -> None:
    """The results as a small table (the numbers for the evaluation report)."""
    print(f"Model 2 (purchase spend) for business {report['business_id']} - {report['version']}")
    c = report["cleaning"]
    print(f"  months {report['history']['from']} to {report['history']['to']} ({c['full_months']} full months); "
          f"{c['used']} verified invoices used, dropped {c['dropped'] or 'none'}")
    print(f"  fair test on {len(report['backtest']['months'])} months: {', '.join(report['backtest']['months'])}")
    print(f"    {'method':<24}{'months':>8}{'MAE (Rs)':>14}{'WAPE %':>9}")
    for name, s in report["backtest"]["methods"].items():
        mark = "  <- chosen" if name == report["chosen"] else ""
        print(f"    {name:<24}{s['predictions']:>8}{s['mae']:>14,.0f}{s['wape']:>9.1f}{mark}")
    verdict = "a learner beat both baselines" if report["learner_kept"] else "no learner beat both baselines: the baseline is used"
    print(f"  chosen: {report['chosen']} ({verdict})")
    check = report["range_check"]
    print(f"  range: {report['range_pct']['low']:+.1f}% to {report['range_pct']['high']:+.1f}% around the forecast; "
          f"later months inside it: {check['inside_pct']}% (set on {check['calibrated_on_months']}, checked on {check['checked_on_months']})")
    print(f"  cash/credit split: {report['cash_credit_split']['note']}")


# ---- using the model ------------------------------------------------------------------------------
MODEL_PATH = "{business_id}/purchase_spend.joblib"


@dataclass(frozen=True)
class MonthForecast:
    """Expected purchase spend for one month, with its range and the baseline beside it."""

    month: date
    expected: float
    low: float | None
    high: float | None
    baseline_name: str
    baseline: float
    method: str
    is_baseline: bool


class SpendForecastModel:
    """A trained Model 2, loaded from its file (only from the shop's private "models" bucket:
    joblib files can run code when loaded)."""

    def __init__(self, artifact: dict[str, Any]) -> None:
        """Keep the chosen method, its settings, the range and the training report."""
        if artifact.get("model") != "purchase_spend" or artifact.get("format") != ARTIFACT_FORMAT:
            raise ValueError("not a Model 2 (purchase spend) file")
        self.version: str = artifact["version"]
        self.method: str = artifact["method"]
        self.learned: bool = artifact["learned"]
        self.best_baseline: str = artifact.get("best_baseline", "rolling mean")
        self.report: dict[str, Any] = artifact.get("report", {})
        self._hw_params = artifact.get("hw_params")
        self._estimator = artifact.get("estimator")
        self._low, self._high = artifact["range"]

    @classmethod
    def from_bytes(cls, data: bytes) -> SpendForecastModel:
        """Load a model file made by ml/train.py."""
        return cls(joblib.load(io.BytesIO(data)))

    @classmethod
    async def from_storage(cls, storage: Any, business_id: str) -> SpendForecastModel | None:
        """The shop's Model 2 from its "models" bucket, or None if it has not been trained."""
        data = await storage.download(MODEL_PATH.format(business_id=business_id))
        return cls.from_bytes(data) if data else None

    def forecast(self, months: list[date], totals: list[float], steps: int = HORIZON) -> list[MonthForecast]:
        """The months after the series: the chosen method with its range, the best baseline beside it."""
        return forecast_months(months, totals, steps, model=self)

    def _values(self, months: list[date], totals: list[float], steps: int) -> list[float]:
        """The chosen method's values (learners use their saved settings on the latest months)."""
        if self.method == "exponential smoothing" and self._hw_params:
            return hw_forecast(totals, tuple(self._hw_params), steps)
        if self.method == "lagged regression" and self._estimator is not None:
            return lag_forecast(self._estimator, months, totals, steps)
        return seasonal_naive(totals, steps) if self.method == "seasonal naive" else rolling_mean(totals, steps)


def forecast_months(months: list[date], totals: list[float], steps: int = HORIZON,
                    model: SpendForecastModel | None = None) -> list[MonthForecast]:
    """Monthly forecasts after the series. Without a trained model (or with too little history),
    the rolling mean of the last 3 months, labelled as a baseline, with no range."""
    if not totals:
        return []
    after = [add_months(months[-1], h + 1) for h in range(steps)]
    if model is None or len(totals) < 13:
        values = rolling_mean(totals, steps)
        return [MonthForecast(m, round(v, 2), None, None, "rolling mean", round(v, 2), "rolling mean", True)
                for m, v in zip(after, values, strict=True)]
    values = model._values(months, totals, steps)
    baseline = (seasonal_naive if model.best_baseline == "seasonal naive" else rolling_mean)(totals, steps)
    return [MonthForecast(m, round(v, 2), round(v * (1 + model._low), 2), round(v * (1 + model._high), 2),
                          model.best_baseline, round(b, 2), model.method, not model.learned)
            for m, v, b in zip(after, values, baseline, strict=True)]
