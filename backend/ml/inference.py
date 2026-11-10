"""Use a trained Model 1 (Item Price Intelligence): the expected price of a product and the check
that flags a new price far from it (proposal section 6).

The model file is made by ml/train.py and kept in the shop's private "models" bucket. Only load
model files from there: joblib files can run code when loaded, so never load one from elsewhere.

    model = await ItemPriceModel.from_storage(storage, business_id)   # None until trained
    expected = model.predict(earlier_purchases, day, quantity)
    check = model.check(expected, unit_price_on_the_new_invoice)

The flag is advice for the owner ("check the unit price and pack size"), never a judgement that
the supplier overcharged.
"""

from __future__ import annotations

import io
import math
import statistics
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

import joblib
import numpy as np

from .features import MIN_HISTORY, Purchase, describe, latest_price, rolling_median

MODEL_PATH = "{business_id}/item_price.joblib"


@dataclass(frozen=True)
class PricePrediction:
    """What a product is expected to cost on a day, and what that is based on."""

    expected: float | None
    low: float | None  # the expected range (None when there is too little history)
    high: float | None
    method: str  # "linear (ridge)", "gradient boosting", "latest price", "rolling median" or "historical median"
    is_baseline: bool  # True when the screen must label the figure as a baseline
    earlier_purchases: int
    latest_price: float | None
    note: str


@dataclass(frozen=True)
class PriceCheck:
    """A new price compared with the expectation."""

    status: Literal["inside", "above", "below", "no_range"]
    difference: float | None  # rupees above (+) or below (-) the expected price
    difference_pct: float | None
    message: str


class ItemPriceModel:
    """A trained Model 1, loaded from its file."""

    def __init__(self, artifact: dict[str, Any]) -> None:
        """Keep the saved method, learner, range and the training report."""
        if artifact.get("model") != "item_price" or artifact.get("format") != 1:
            raise ValueError("not a Model 1 (item price) file")
        self.version: str = artifact["version"]
        self.method: str = artifact["method"]
        self.learned: bool = artifact["learned"]
        self.min_history: int = artifact.get("min_history", MIN_HISTORY)
        self.report: dict[str, Any] = artifact.get("report", {})
        self._estimator = artifact.get("estimator")
        self._low, self._high = artifact["range_log"]

    @classmethod
    def from_bytes(cls, data: bytes) -> ItemPriceModel:
        """Load a model file made by ml/train.py."""
        return cls(joblib.load(io.BytesIO(data)))

    @classmethod
    async def from_storage(cls, storage: Any, business_id: str) -> ItemPriceModel | None:
        """The shop's model from its private "models" bucket, or None if it has not been trained."""
        data = await storage.download(MODEL_PATH.format(business_id=business_id))
        return cls.from_bytes(data) if data else None

    def predict(self, history: list[Purchase], day: date, quantity: float) -> PricePrediction:
        """The expected unit price of a product bought on ``day``, from its earlier purchases."""
        history = sorted((p for p in history if p.day <= day), key=lambda p: p.day)
        last = latest_price(history)
        if not history:
            return PricePrediction(None, None, None, "historical median", True, 0, None,
                                   "No earlier purchases of this product.")
        if len(history) < self.min_history:
            median = statistics.median(p.unit_price for p in history)
            return PricePrediction(round(median, 2), None, None, "historical median", True, len(history), last,
                                   f"Only {len(history)} earlier purchase(s): this is their median, shown as a baseline.")
        assert last is not None
        if self.learned and self._estimator is not None:
            features = describe(history, day, quantity)
            assert features is not None
            expected = last * math.exp(float(self._estimator.predict(np.array([features]))[0]))
            note = f"Expected by Model 1 ({self.method}) from {len(history)} earlier purchases."
        elif self.method == "rolling median":
            expected = rolling_median(history) or last
            note = f"Median of the last {min(len(history), 5)} prices (baseline: no learner beat it)."
        else:
            expected = last
            note = "The latest price (baseline: no learner beat it)."
        return PricePrediction(round(expected, 2), round(expected * math.exp(self._low), 2),
                               round(expected * math.exp(self._high), 2), self.method, not self.learned,
                               len(history), last, note)

    def check(self, prediction: PricePrediction, unit_price: float) -> PriceCheck:
        """Flag a new unit price outside the expected range (a transparent rule on the gap)."""
        if prediction.expected is None or prediction.low is None or prediction.high is None:
            return PriceCheck("no_range", None, None, "Not enough history to check this price.")
        difference = round(unit_price - prediction.expected, 2)
        pct = round(100 * difference / prediction.expected, 1)
        if unit_price > prediction.high:
            return PriceCheck("above", difference, pct,
                              f"The unit price is above the expected range (Rs {prediction.low:,.2f} to "
                              f"Rs {prediction.high:,.2f}). Check the unit price and pack size before accepting the invoice.")
        if unit_price < prediction.low:
            return PriceCheck("below", difference, pct,
                              f"The unit price is below the expected range (Rs {prediction.low:,.2f} to "
                              f"Rs {prediction.high:,.2f}). Check the unit price and pack size before accepting the invoice.")
        return PriceCheck("inside", difference, pct, "Within the expected range.")
