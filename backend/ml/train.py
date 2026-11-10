"""Train Model 1, Item Price Intelligence, for one shop (proposal section 6); the same command
also trains Model 2, the purchase spend forecast (ml/spend.py).

Run by the team on a laptop, never on the server (from backend/, with backend/.env pointing at
the shop's Supabase project):

    .venv\\Scripts\\python -m ml.train              # train both models, save, upload to the shop's Storage
    .venv\\Scripts\\python -m ml.train --model purchase_spend  # only Model 2
    .venv\\Scripts\\python -m ml.train --no-upload  # train and save locally only

What it does:
1. Reads the shop's verified, product-linked purchases (read only) and cleans them (ml/features.py).
2. Baselines: the latest comparable price and the rolling median of the last five prices.
3. Candidate learners (scikit-learn): a regularised linear model (ridge) and a gradient-boosted
   regressor. Both predict the change from the latest price, so one model serves every product.
4. Fair test in time order (rolling origin): for each of the last months, every method learns
   only from purchases before that month and predicts the purchases in it. Mean absolute error
   (rupees) and mean percentage error are compared.
5. A learner is kept only if it beats BOTH baselines; otherwise the better baseline is the model.
6. The price range comes from the spread of the chosen method's past errors (2.5% to 97.5%); a
   new price outside it is flagged for checking. How often real later prices fell inside the
   range is measured on months the range was not set from.
7. Saves item_price.joblib and item_price_report.json in ml/output/<business id>/ (not in Git)
   and uploads both to the shop's private "models" bucket in Supabase Storage.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import math
import statistics
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from database.client import PurchaseLine

from .features import (
    FEATURES,
    MIN_HISTORY,
    CleaningReport,
    Purchase,
    by_product,
    clean,
    describe,
    rolling_median,
    supplier_recorded_reliably,
)

EVAL_MONTHS = 12  # how many recent months the fair test predicts
MIN_TRAINING_EXAMPLES = 50  # a month is only tested once there is this much earlier history
RANGE_QUANTILES = (0.025, 0.975)  # the expected range covers 95% of past errors
MIN_RANGE = 0.02  # a price within 2% of the expectation is never flagged (rounding on the paper)
ARTIFACT_FORMAT = 1
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
BASELINES = ("latest price", "rolling median")
LEARNERS = ("linear (ridge)", "gradient boosting")


@dataclass(frozen=True)
class Example:
    """One purchase the methods predict: its inputs, the two baselines and the real price."""

    day: date
    product_id: str
    features: list[float]
    latest: float
    median: float
    actual: float

    @property
    def target(self) -> float:
        """What the learners predict: the change from the latest price (log ratio)."""
        return math.log(self.actual / self.latest)


def make_learners() -> dict[str, Any]:
    """The two candidate learners named in the proposal, with fixed settings."""
    return {
        "linear (ridge)": make_pipeline(StandardScaler(), Ridge(alpha=1.0)),
        "gradient boosting": HistGradientBoostingRegressor(
            max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=20,
            l2_regularization=1.0, random_state=0),
    }


def build_examples(usable: list[Purchase], learn: list[Purchase]) -> list[Example]:
    """One example per purchase with enough history. Inputs use every earlier usable purchase
    (what was known on the day); purchases left out of training are never targets."""
    learn_keys = {(p.invoice_id, p.product_id, p.unit_price, p.quantity) for p in learn}
    out: list[Example] = []
    for history in by_product(usable).values():
        for k in range(MIN_HISTORY, len(history)):
            purchase = history[k]
            if (purchase.invoice_id, purchase.product_id, purchase.unit_price, purchase.quantity) not in learn_keys:
                continue
            earlier = history[:k]
            features = describe(earlier, purchase.day, purchase.quantity)
            assert features is not None
            out.append(Example(day=purchase.day, product_id=purchase.product_id, features=features,
                               latest=earlier[-1].unit_price, median=rolling_median(earlier) or earlier[-1].unit_price,
                               actual=purchase.unit_price))
    out.sort(key=lambda e: e.day)
    return out


def _month(day: date) -> date:
    """The first day of a day's month."""
    return day.replace(day=1)


def _predict(method: str, learner: Any, examples: list[Example]) -> list[float]:
    """Predicted unit prices of a method for some examples."""
    if method == "latest price":
        return [e.latest for e in examples]
    if method == "rolling median":
        return [e.median for e in examples]
    changes = learner.predict(np.array([e.features for e in examples]))
    return [e.latest * math.exp(float(change)) for e, change in zip(examples, changes, strict=True)]


def backtest(examples: list[Example], months: int = EVAL_MONTHS) -> tuple[list[date], dict[str, list[tuple[date, float, float]]]]:
    """Rolling-origin test: (tested months, per method a list of (month, actual, predicted))."""
    all_months = sorted({_month(e.day) for e in examples})
    tested: list[date] = []
    results: dict[str, list[tuple[date, float, float]]] = {name: [] for name in (*BASELINES, *LEARNERS)}
    for month in all_months[-months:]:
        train = [e for e in examples if e.day < month]
        test = [e for e in examples if _month(e.day) == month]
        if len(train) < MIN_TRAINING_EXAMPLES or not test:
            continue
        tested.append(month)
        x = np.array([e.features for e in train])
        y = np.array([e.target for e in train])
        learners = make_learners()
        for name in (*BASELINES, *LEARNERS):
            learner = learners.get(name)
            if learner is not None:
                learner.fit(x, y)
            for e, predicted in zip(test, _predict(name, learner, test), strict=True):
                results[name].append((month, e.actual, predicted))
    return tested, results


def scores(rows: list[tuple[date, float, float]]) -> dict[str, float]:
    """Mean absolute error (rupees) and mean percentage error of (actual, predicted) pairs."""
    if not rows:
        return {"predictions": 0, "mae": float("nan"), "mape": float("nan")}
    errors = [abs(actual - predicted) for _, actual, predicted in rows]
    return {"predictions": len(rows), "mae": round(statistics.fmean(errors), 2),
            "mape": round(100 * statistics.fmean(err / actual for err, (_, actual, _) in zip(errors, rows, strict=True)), 2)}


def choose(method_scores: dict[str, dict[str, float]]) -> tuple[str, bool]:
    """The method to use, and whether it is a learner. A learner is kept only if its percentage
    error is below BOTH baselines'; otherwise the better baseline is used."""
    best_baseline = min(BASELINES, key=lambda name: method_scores[name]["mape"])
    learners = [name for name in LEARNERS if method_scores[name]["predictions"]]
    if learners:
        best_learner = min(learners, key=lambda name: method_scores[name]["mape"])
        if all(method_scores[best_learner]["mape"] < method_scores[name]["mape"] for name in BASELINES):
            return best_learner, True
    return best_baseline, False


def error_range(rows: list[tuple[date, float, float]]) -> tuple[float, float]:
    """The 2.5%-97.5% spread of past errors (log ratio actual / predicted), at least +/- MIN_RANGE."""
    errors = np.array([math.log(actual / predicted) for _, actual, predicted in rows])
    low, high = (float(q) for q in np.quantile(errors, RANGE_QUANTILES))
    return min(low, -math.log(1 + MIN_RANGE)), max(high, math.log(1 + MIN_RANGE))


def range_check(rows: list[tuple[date, float, float]], months: list[date]) -> dict[str, Any]:
    """Set the range on the first half of the tested months, count how many later prices fell inside."""
    if len(months) < 2:
        return {"calibrated_on_months": 0, "checked_on_months": 0, "inside_pct": None}
    cut = months[len(months) // 2]
    early = [r for r in rows if r[0] < cut]
    late = [r for r in rows if r[0] >= cut]
    if not early or not late:
        return {"calibrated_on_months": 0, "checked_on_months": 0, "inside_pct": None}
    low, high = error_range(early)
    inside = sum(1 for _, actual, predicted in late if low <= math.log(actual / predicted) <= high)
    return {"calibrated_on_months": sum(1 for m in months if m < cut), "checked_on_months": sum(1 for m in months if m >= cut),
            "inside_pct": round(100 * inside / len(late), 1)}


def flags_in_history(usable: list[Purchase], learn: list[Purchase], method: str, learned: bool, estimator: Any,
                     range_log: tuple[float, float]) -> dict[str, Any]:
    """How often the price check would have flagged the shop's past purchases (each judged from
    the purchases before it), and how many of the far-off prices it catches."""
    from .inference import ItemPriceModel  # the same check the app will use

    model = ItemPriceModel({"format": ARTIFACT_FORMAT, "model": "item_price", "version": "check", "method": method,
                            "learned": learned, "estimator": estimator, "range_log": range_log,
                            "min_history": MIN_HISTORY})
    learn_keys = {(p.invoice_id, p.product_id, p.unit_price, p.quantity) for p in learn}
    checked = flagged = far_off = far_off_flagged = 0
    for history in by_product(usable).values():
        for k in range(MIN_HISTORY, len(history)):
            purchase = history[k]
            status = model.check(model.predict(history[:k], purchase.day, purchase.quantity), purchase.unit_price).status
            checked += 1
            flagged += status in ("above", "below")
            if (purchase.invoice_id, purchase.product_id, purchase.unit_price, purchase.quantity) not in learn_keys:
                far_off += 1
                far_off_flagged += status in ("above", "below")
    return {"checked": checked, "flagged": flagged, "flagged_pct": round(100 * flagged / checked, 1) if checked else None,
            "far_off_prices": far_off, "far_off_flagged": far_off_flagged}


def train_item_price(lines: list[PurchaseLine], *, today: date, business_id: str,
                     months: int = EVAL_MONTHS) -> tuple[dict[str, Any], dict[str, Any]]:
    """Clean, test, choose and fit Model 1. Returns (the model to save, the report)."""
    usable, learn, cleaning = clean(lines, today=today)
    examples = build_examples(usable, learn)
    if len(examples) < MIN_TRAINING_EXAMPLES:
        raise ValueError(f"Not enough purchase history to train: {len(examples)} usable repeat purchases "
                         f"(need {MIN_TRAINING_EXAMPLES}). The screens keep showing recent prices as a baseline.")
    tested, results = backtest(examples, months)
    method_scores = {name: scores(rows) for name, rows in results.items()}
    method, learned = choose(method_scores)
    low, high = error_range(results[method]) if results[method] else (-math.log(1 + MIN_RANGE), math.log(1 + MIN_RANGE))

    estimator = None
    if learned:
        estimator = make_learners()[method]
        estimator.fit(np.array([e.features for e in examples]), np.array([e.target for e in examples]))

    version = f"item_price-{today:%Y%m%d}"
    trained_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    flags = flags_in_history(usable, learn, method, learned, estimator, (low, high))
    report = {
        "model": "item_price", "version": version, "trained_at": trained_at, "business_id": business_id,
        "history": {"from": usable[0].day.isoformat() if usable else None, "to": usable[-1].day.isoformat() if usable else None},
        "cleaning": cleaning.as_dict(),
        "examples": len(examples),
        "supplier_used": supplier_recorded_reliably(usable),
        "backtest": {"months": [m.isoformat()[:7] for m in tested], "methods": method_scores},
        "chosen": method,
        "learner_kept": learned,
        "range_pct": {"low": round(100 * (math.exp(low) - 1), 2), "high": round(100 * (math.exp(high) - 1), 2)},
        "range_check": range_check(results[method], tested),
        "flags_in_history": flags,
        "min_history": MIN_HISTORY,
    }
    artifact = {
        "format": ARTIFACT_FORMAT, "model": "item_price", "version": version, "trained_at": trained_at,
        "business_id": business_id, "method": method, "learned": learned, "estimator": estimator,
        "features": list(FEATURES), "range_log": (low, high), "min_history": MIN_HISTORY, "report": report,
    }
    return artifact, report


def to_bytes(artifact: dict[str, Any]) -> bytes:
    """The saved model file (joblib)."""
    buffer = io.BytesIO()
    joblib.dump(artifact, buffer)
    return buffer.getvalue()


def print_report(report: dict[str, Any]) -> None:
    """The results as a small table (the numbers for the evaluation report)."""
    cleaning: CleaningReport | dict = report["cleaning"]
    print(f"Model 1 (item price) for business {report['business_id']} - {report['version']}")
    print(f"  history {report['history']['from']} to {report['history']['to']}: {cleaning['lines_in']} lines in, "
          f"{cleaning['kept']} kept, dropped {cleaning['dropped'] or 'none'}, "
          f"{cleaning['left_out_of_training']} far-off prices left out of training")
    print(f"  products {cleaning['products']} ({cleaning['products_with_history']} with enough history), "
          f"{report['examples']} repeat purchases to learn from, supplier used: {report['supplier_used']}")
    print(f"  fair test on {len(report['backtest']['months'])} months: {', '.join(report['backtest']['months'])}")
    print(f"    {'method':<20}{'predictions':>12}{'MAE (Rs)':>12}{'error %':>10}")
    for name, s in report["backtest"]["methods"].items():
        mark = "  <- chosen" if name == report["chosen"] else ""
        print(f"    {name:<20}{s['predictions']:>12}{s['mae']:>12,.2f}{s['mape']:>10.2f}{mark}")
    verdict = "a learner beat both baselines" if report["learner_kept"] else "no learner beat both baselines: the baseline is used"
    print(f"  chosen: {report['chosen']} ({verdict})")
    check = report["range_check"]
    print(f"  expected range: {report['range_pct']['low']:+.1f}% to {report['range_pct']['high']:+.1f}% around the prediction; "
          f"later prices inside it: {check['inside_pct']}% (set on {check['calibrated_on_months']} months, "
          f"checked on {check['checked_on_months']})")
    flags = report["flags_in_history"]
    print(f"  price check on past purchases: {flags['flagged']} of {flags['checked']} flagged ({flags['flagged_pct']}%); "
          f"far-off prices caught: {flags['far_off_flagged']} of {flags['far_off_prices']}")


async def _main(argv: list[str]) -> int:
    """Command line: read the shop's purchases, train Model 1 and Model 2, save and upload."""
    parser = argparse.ArgumentParser(description="Train Model 1 (item price) and Model 2 (purchase spend) for one shop.")
    parser.add_argument("--business-id", help="the shop (default: the only business in the database)")
    parser.add_argument("--model", choices=("all", "item_price", "purchase_spend"), default="all",
                        help="which model to train (default: both)")
    parser.add_argument("--no-upload", action="store_true", help="save locally only, do not upload to Supabase Storage")
    parser.add_argument("--months", type=int, default=EVAL_MONTHS, help="how many recent months Model 1 is tested on")
    args = parser.parse_args(argv)

    from database.client import Database, SupabaseStorage
    from main import Settings

    from . import spend

    settings = Settings.from_env()
    if not settings.database_url:
        print("DATABASE_URL is not set (backend/.env).", file=sys.stderr)
        return 2
    if not args.no_upload and not settings.storage_configured:
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set: use --no-upload or set them.", file=sys.stderr)
        return 2
    db = Database(settings.database_url, max_size=1)
    await db.open()
    try:
        businesses = await db.business_ids()
        business_id = args.business_id or (businesses[0] if len(businesses) == 1 else None)
        if business_id is None or business_id not in businesses:
            print(f"Choose the shop with --business-id (found {len(businesses)}).", file=sys.stderr)
            return 2
        lines = await db.purchase_lines(business_id) if args.model in ("all", "item_price") else []
        invoices = await db.list_all_invoices(business_id) if args.model in ("all", "purchase_spend") else []
    finally:
        await db.close()

    trained: list[tuple[str, bytes, dict[str, Any]]] = []
    status = 0
    if args.model in ("all", "item_price"):
        try:
            artifact, report = train_item_price(lines, today=date.today(), business_id=business_id, months=args.months)
            print_report(report)
            trained.append(("item_price", to_bytes(artifact), report))
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            status = 1
    if args.model in ("all", "purchase_spend"):
        try:
            artifact, report = spend.train_purchase_spend(invoices, today=date.today(), business_id=business_id)
            spend.print_report(report)
            trained.append(("purchase_spend", spend.to_bytes(artifact), report))
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            status = 1

    folder = OUTPUT_DIR / business_id
    folder.mkdir(parents=True, exist_ok=True)
    for name, data, report in trained:
        (folder / f"{name}.joblib").write_bytes(data)
        (folder / f"{name}_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"  saved: {folder}")

    if trained and not args.no_upload:
        assert settings.supabase_url and settings.supabase_service_role_key
        storage = SupabaseStorage(url=settings.supabase_url, service_key=settings.supabase_service_role_key, bucket="models")
        try:
            for name, data, report in trained:
                await storage.put(f"{business_id}/{name}.joblib", data, "application/octet-stream")
                await storage.put(f"{business_id}/{name}_report.json", json.dumps(report, indent=2).encode(), "application/json")
                print(f"  uploaded to Supabase Storage: models/{business_id}/{name}.joblib")
        finally:
            await storage.aclose()
    return status


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))
