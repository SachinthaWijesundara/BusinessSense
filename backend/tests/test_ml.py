"""Tests for Model 1 (Item Price Intelligence): cleaning, the inputs, choosing between learners and
baselines, the price range and check, saving and loading. Fictional data only."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from database.client import PurchaseLine
from ml.features import FEATURES, MIN_HISTORY, clean, describe, rolling_median, supplier_recorded_reliably
from ml.inference import ItemPriceModel
from ml.train import BASELINES, LEARNERS, choose, to_bytes, train_item_price
from tests.conftest import MemoryStorage

TODAY = date(2026, 11, 1)


def line(n: int, day: date, product: str = "p1", qty: float = 10, price: float = 100.0, *,
         supplier: str | None = "s1", total: float | None = None) -> PurchaseLine:
    """One verified, product-linked line (``price`` per unit unless ``total`` is given)."""
    return PurchaseLine(invoice_id=f"inv-{n}", invoice_date=day, supplier_id=supplier, product_id=product,
                        product_name=f"Product {product}", quantity=qty, line_total=qty * price if total is None else total)


def series(product: str, start: date, count: int, price_on, *, every: int = 7, first: int = 0, qty: float = 10) -> list[PurchaseLine]:
    """``count`` purchases of a product, one every ``every`` days, priced by ``price_on(index)``."""
    return [line(first + i, start + timedelta(days=every * i), product, qty, price_on(i)) for i in range(count)]


# ---- cleaning ---------------------------------------------------------------------------------
def test_clean_keeps_comparable_prices_and_counts_what_it_drops():
    """Unit price = line total / quantity (any entry style); unusable lines are dropped with a reason."""
    lines = [
        line(1, date(2026, 1, 5), qty=4, total=900.0),  # rate style after a discount: 225 per unit
        line(2, date(2026, 1, 6), qty=3, price=225.0),  # MRP style: net price x quantity
        line(3, None, qty=1),
        line(4, date(2027, 1, 1)),
        line(5, date(2010, 1, 1)),
        line(6, date(2026, 1, 7), qty=0),
        line(7, date(2026, 1, 8), total=0),
        line(8, date(2026, 1, 9), price=225.0), line(8, date(2026, 1, 9), price=225.0),  # the same line typed twice
    ]
    usable, learn, report = clean(lines, today=TODAY)
    assert [p.unit_price for p in usable[:2]] == [225.0, 225.0]
    assert report.lines_in == 9 and report.kept == 3 and len(learn) == 3
    assert report.dropped == {"no invoice date": 1, "invoice date in the future": 1, "invoice date before the archive": 1,
                              "no quantity": 1, "no amount": 1, "same line entered twice": 1}


def test_far_off_prices_stay_usable_but_are_not_learned_from():
    """A price 40% above its neighbours is left out of training (it is what the range must flag)."""
    prices = [100, 100, 101, 140, 101, 102, 102]
    usable, learn, report = clean(series("p1", date(2026, 1, 1), 7, lambda i: prices[i]), today=TODAY)
    assert len(usable) == 7 and len(learn) == 6 and report.left_out_of_training == 1
    assert 140 not in [p.unit_price for p in learn]
    one_odd_of_three = clean(series("p2", date(2026, 1, 1), 3, lambda i: [225, 225, 100][i]), today=TODAY)
    assert one_odd_of_three[2].left_out_of_training == 0  # too few neighbours to judge any of them


def test_inputs_use_only_earlier_purchases():
    """No input looks at the purchase being predicted or later ones; thin history gets no inputs."""
    usable, _, _ = clean(series("p1", date(2026, 1, 1), 6, lambda i: 100 + 10 * i), today=TODAY)
    assert describe(usable[:MIN_HISTORY - 1], usable[2].day, 10) is None
    first = describe(usable[:3], usable[3].day, 10)
    assert first is not None and len(first) == len(FEATURES)
    changed_future = usable[:3]  # the 4th purchase's price must not matter
    assert describe(changed_future, usable[3].day, 10) == first
    values = dict(zip(FEATURES, first, strict=True))
    assert values["last_change"] == pytest.approx(math.log(120 / 110))
    assert values["log_days_since_last"] == pytest.approx(math.log1p(7))
    assert rolling_median(usable[:5]) == 120


def test_supplier_is_used_only_when_recorded_reliably():
    """One supplier per product adds nothing; two suppliers with real history for a product do."""
    one = [line(i, date(2026, 1, 1) + timedelta(days=i), supplier="s1") for i in range(6)]
    two = one + [line(10 + i, date(2026, 2, 1) + timedelta(days=i), supplier="s2") for i in range(3)]
    assert not supplier_recorded_reliably(clean(one, today=TODAY)[0])
    assert supplier_recorded_reliably(clean(two, today=TODAY)[0])


# ---- choosing and training ----------------------------------------------------------------------
def test_a_learner_is_kept_only_if_it_beats_both_baselines():
    """Proposal: a learner is kept only if it beats the latest-price and rolling-median baselines."""
    def scored(**mape):
        return {name: {"predictions": 10, "mae": 1.0, "mape": mape.get(name.split()[0], 5.0)} for name in (*BASELINES, *LEARNERS)}

    assert choose(scored(latest=2.0, rolling=3.0, linear=1.5, gradient=1.8)) == ("linear (ridge)", True)
    assert choose(scored(latest=2.0, rolling=3.0, linear=2.5, gradient=2.0)) == ("latest price", False)  # a tie is not a win
    assert choose(scored(latest=4.0, rolling=3.0, linear=3.5, gradient=5.0)) == ("rolling median", False)
    assert choose(scored(latest=float("nan"), rolling=float("nan"), linear=float("nan"), gradient=float("nan")))[1] is False


def test_steady_prices_keep_the_baseline():
    """Prices that only change on price-list revisions: nothing beats the latest price."""
    lines = []
    for n, product in enumerate(["p1", "p2", "p3", "p4"]):
        lines += series(product, date(2024, 1, 1) + timedelta(days=n), 100, lambda i, b=100 * (n + 1): b * (1.05 if i >= 50 else 1),
                        first=1000 * n)
    _, report = train_item_price(lines, today=TODAY, business_id="shop")
    assert report["chosen"] == "latest price" and report["learner_kept"] is False
    assert report["backtest"]["methods"]["latest price"]["mape"] < report["backtest"]["methods"]["rolling median"]["mape"]
    assert report["supplier_used"] is False and len(report["backtest"]["months"]) == 12


def test_a_learnable_pattern_keeps_a_learner_and_flags_unusual_prices():
    """Prices rising every purchase: a learner predicts the rise and beats both baselines."""
    lines = []
    for n, product in enumerate(["p1", "p2", "p3", "p4", "p5"]):
        lines += series(product, date(2024, 6, 1) + timedelta(days=n), 110,
                        lambda i, b=50 * (n + 1): b * 1.01 ** i, every=6, first=1000 * n)
    artifact, report = train_item_price(lines, today=TODAY, business_id="shop")
    assert report["learner_kept"] is True and report["chosen"] in LEARNERS
    methods = report["backtest"]["methods"]
    assert methods[report["chosen"]]["mape"] < min(methods[name]["mape"] for name in BASELINES)
    assert report["range_check"]["inside_pct"] is not None and report["flags_in_history"]["checked"] > 0

    model = ItemPriceModel.from_bytes(to_bytes(artifact))
    usable, _, _ = clean(lines, today=TODAY)
    history = [p for p in usable if p.product_id == "p1"]
    expected = model.predict(history, history[-1].day + timedelta(days=6), 10)
    assert expected.is_baseline is False and expected.low < expected.expected < expected.high
    assert expected.expected == pytest.approx(history[-1].unit_price * 1.01, rel=0.01)
    assert model.check(expected, expected.expected).status == "inside"
    high = model.check(expected, expected.expected * 1.4)
    assert high.status == "above" and "pack size" in high.message and "overcharg" not in high.message
    assert model.check(expected, expected.expected * 0.6).status == "below"


def test_too_little_history_is_refused():
    """Without enough repeat purchases there is nothing to learn: the screens keep the baseline."""
    with pytest.raises(ValueError, match="Not enough purchase history"):
        train_item_price(series("p1", date(2026, 1, 1), 10, lambda i: 100), today=TODAY, business_id="shop")


# ---- using the model ----------------------------------------------------------------------------
def baseline_model() -> ItemPriceModel:
    """A Model 1 that uses the latest price with a -2% / +10% range."""
    return ItemPriceModel({"format": 1, "model": "item_price", "version": "test", "method": "latest price",
                           "learned": False, "estimator": None, "range_log": (math.log(0.98), math.log(1.10)),
                           "min_history": MIN_HISTORY})


def test_thin_history_shows_a_labelled_baseline_and_no_flag():
    """Fewer than MIN_HISTORY earlier purchases: the median, labelled as a baseline, nothing flagged."""
    model = baseline_model()
    usable, _, _ = clean(series("p1", date(2026, 1, 1), 2, lambda i: [100, 120][i]), today=TODAY)
    thin = model.predict(usable, date(2026, 2, 1), 5)
    assert (thin.expected, thin.low, thin.is_baseline, thin.method) == (110.0, None, True, "historical median")
    assert model.check(thin, 500).status == "no_range"
    assert model.predict([], date(2026, 2, 1), 5).expected is None
    later_purchase_ignored = model.predict(usable, date(2026, 1, 3), 5)  # only purchases up to the day count
    assert later_purchase_ignored.earlier_purchases == 1


def test_baseline_model_range_and_check():
    """The latest price with the saved range; prices outside it are flagged either way."""
    model = baseline_model()
    usable, _, _ = clean(series("p1", date(2026, 1, 1), 5, lambda i: 200), today=TODAY)
    expected = model.predict(usable, date(2026, 3, 1), 10)
    assert (expected.expected, expected.low, expected.high, expected.is_baseline) == (200.0, 196.0, 220.0, True)
    assert model.check(expected, 215).status == "inside"
    assert (model.check(expected, 230).status, model.check(expected, 230).difference_pct) == ("above", 15.0)
    assert model.check(expected, 150).status == "below"


@pytest.mark.anyio
async def test_saved_model_loads_from_storage():
    """The file ml/train.py uploads loads back from the shop's models bucket; other files are refused."""
    storage = MemoryStorage()
    assert await ItemPriceModel.from_storage(storage, "shop") is None
    data = to_bytes({"format": 1, "model": "item_price", "version": "v1", "method": "rolling median", "learned": False,
                     "estimator": None, "range_log": (-0.02, 0.05), "min_history": MIN_HISTORY})
    await storage.put("shop/item_price.joblib", data, "application/octet-stream")
    model = await ItemPriceModel.from_storage(storage, "shop")
    assert model is not None and (model.version, model.method) == ("v1", "rolling median")
    with pytest.raises(ValueError):
        ItemPriceModel({"format": 1, "model": "purchase_spend"})
