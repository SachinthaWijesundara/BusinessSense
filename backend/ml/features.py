"""Model 1 (Item Price Intelligence) data: clean the purchase history and describe each purchase.

Shared by training (ml/train.py) and prediction (ml/inference.py), so both see prices the same way.

1. ``clean``  turns verified, product-linked invoice lines (``Database.purchase_lines``) into
   purchases with a comparable unit price: what was paid per unit after line discounts
   (line_total / quantity), whichever data-entry style the line was typed in. Lines that cannot
   be compared are dropped, with the reason counted in a cleaning report:
     * no invoice date, a date in the future or before the shop's archive
     * no quantity or no amount, or a zero / negative one
     * the same line entered twice on an invoice
     * a price far from the product's neighbouring purchases (left out of training only;
       such a price is what the model's range is there to flag)
2. ``describe`` turns one purchase into the model's inputs, using ONLY the product's earlier
   purchases (proposal: purchase dates, product identity, comparable unit prices, supplier where
   recorded reliably). Products with fewer than MIN_HISTORY earlier purchases are not described:
   for them the screens show recent prices or the historical median, labelled as a baseline.

Pack sizes are not converted: a product's history is only comparable if it is bought in the same
unit, which the owner confirms when linking a line to a product.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

from database.client import PurchaseLine

MIN_HISTORY = 3  # earlier purchases a product needs before the learned model is used
NEIGHBOURS = 2  # purchases on each side used to judge whether a price is far from its neighbours
FAR_FROM_NEIGHBOURS = 0.20  # more than 20% away from the neighbours' median: left out of training
EARLIEST_DATE = date(2015, 1, 1)  # the shop's paper archive starts in 2019

# The model's inputs, in a fixed order (the saved model remembers this list).
FEATURES = (
    "med3_vs_last",  # median of the last 3 prices compared with the last price (log ratio)
    "med5_vs_last",  # the same over the last 5 prices
    "last_change",  # the last price compared with the one before it (log ratio)
    "yearly_trend",  # how fast this product's price has moved per year (log ratio per year)
    "log_days_since_last",  # how long since the product was last bought
    "log_purchases",  # how many earlier purchases there are
    "qty_vs_usual",  # this order's quantity compared with the product's usual quantity (log ratio)
    "month_sin",  # the time of year, as a point on a circle
    "month_cos",
)


@dataclass(frozen=True)
class Purchase:
    """One comparable purchase of a product."""

    product_id: str
    product_name: str
    supplier_id: str | None
    day: date
    quantity: float
    unit_price: float
    invoice_id: str
    invoice_number: str | None = None
    supplier_name: str | None = None


@dataclass
class CleaningReport:
    """How many lines came in, how many were kept, and why the others were dropped."""

    lines_in: int = 0
    kept: int = 0
    dropped: Counter = field(default_factory=Counter)
    left_out_of_training: int = 0  # prices far from their neighbours (kept for checking, not learning)
    products: int = 0
    products_with_history: int = 0  # products with enough purchases for the learned model

    def as_dict(self) -> dict:
        """The report as JSON."""
        return {"lines_in": self.lines_in, "kept": self.kept, "dropped": dict(self.dropped),
                "left_out_of_training": self.left_out_of_training, "products": self.products,
                "products_with_history": self.products_with_history}


def _drop_reason(line: PurchaseLine, today: date) -> str | None:
    """Why a line cannot be used as a comparable price, or None."""
    if line.invoice_date is None:
        return "no invoice date"
    if line.invoice_date > today:
        return "invoice date in the future"
    if line.invoice_date < EARLIEST_DATE:
        return "invoice date before the archive"
    if line.quantity is None or line.quantity <= 0:
        return "no quantity"
    if line.line_total is None or line.line_total <= 0:
        return "no amount"
    return None


def _far_from_neighbours(prices: list[float], index: int) -> bool:
    """True when a price is more than FAR_FROM_NEIGHBOURS away from the median of the purchases
    around it. Needs at least three neighbours: the median of two is their average, which one odd
    price would drag along."""
    around = prices[max(0, index - NEIGHBOURS):index] + prices[index + 1:index + 1 + NEIGHBOURS]
    if len(around) < 3:
        return False
    middle = statistics.median(around)
    return abs(math.log(prices[index] / middle)) > math.log(1 + FAR_FROM_NEIGHBOURS)


def clean(lines: list[PurchaseLine], *, today: date) -> tuple[list[Purchase], list[Purchase], CleaningReport]:
    """Comparable purchases, oldest first: (all usable ones, the ones to learn from, report).

    The learning set leaves out prices far from their neighbours, so a mistyped or unusual price
    does not teach the model; those prices stay in the first list so they can be checked.
    """
    report = CleaningReport(lines_in=len(lines))
    seen: set[tuple] = set()
    usable: list[Purchase] = []
    for line in lines:
        reason = _drop_reason(line, today)
        if reason is None:
            key = (line.invoice_id, line.product_id, line.quantity, line.line_total)
            if key in seen:
                reason = "same line entered twice"
            seen.add(key)
        if reason:
            report.dropped[reason] += 1
            continue
        assert line.invoice_date is not None and line.quantity and line.line_total
        usable.append(Purchase(product_id=line.product_id, product_name=line.product_name,
                               supplier_id=line.supplier_id, day=line.invoice_date, quantity=float(line.quantity),
                               unit_price=round(float(line.line_total) / float(line.quantity), 4),
                               invoice_id=line.invoice_id, invoice_number=line.invoice_number,
                               supplier_name=line.supplier_name))
    usable.sort(key=lambda p: (p.day, p.invoice_id, p.product_id))

    learn: list[Purchase] = []
    for purchases in by_product(usable).values():
        prices = [p.unit_price for p in purchases]
        for index, purchase in enumerate(purchases):
            if _far_from_neighbours(prices, index):
                report.left_out_of_training += 1
            else:
                learn.append(purchase)
    learn.sort(key=lambda p: (p.day, p.invoice_id, p.product_id))

    report.kept = len(usable)
    counts = Counter(p.product_id for p in usable)
    report.products = len(counts)
    report.products_with_history = sum(1 for n in counts.values() if n > MIN_HISTORY)
    return usable, learn, report


def by_product(purchases: list[Purchase]) -> dict[str, list[Purchase]]:
    """Purchases grouped by product, each group oldest first."""
    groups: dict[str, list[Purchase]] = defaultdict(list)
    for purchase in sorted(purchases, key=lambda p: (p.day, p.invoice_id)):
        groups[purchase.product_id].append(purchase)
    return dict(groups)


def supplier_recorded_reliably(purchases: list[Purchase]) -> bool:
    """Proposal: supplier is used only when it is recorded consistently and actually varies.

    True when some product is bought from more than one supplier, each with at least MIN_HISTORY
    purchases. When every product comes from a single supplier, supplier adds nothing.
    """
    for group in by_product(purchases).values():
        per_supplier = Counter(p.supplier_id for p in group if p.supplier_id)
        if sum(1 for n in per_supplier.values() if n >= MIN_HISTORY) > 1:
            return True
    return False


def describe(history: list[Purchase], day: date, quantity: float) -> list[float] | None:
    """The model's inputs for buying a product on ``day``, from its EARLIER purchases only.

    None when there are fewer than MIN_HISTORY earlier purchases (the baseline is used then).
    """
    if len(history) < MIN_HISTORY:
        return None
    prices = [p.unit_price for p in history]
    last = prices[-1]
    first_day = history[0].day
    years = max((history[-1].day - first_day).days / 365.25, 0.25)
    usual_qty = statistics.median(p.quantity for p in history)
    values = {
        "med3_vs_last": math.log(statistics.median(prices[-3:]) / last),
        "med5_vs_last": math.log(statistics.median(prices[-5:]) / last),
        "last_change": math.log(last / prices[-2]),
        "yearly_trend": math.log(last / prices[0]) / years,
        "log_days_since_last": math.log1p(max((day - history[-1].day).days, 0)),
        "log_purchases": math.log(len(history)),
        "qty_vs_usual": math.log(max(quantity, 1e-6) / max(usual_qty, 1e-6)),
        "month_sin": math.sin(2 * math.pi * day.month / 12),
        "month_cos": math.cos(2 * math.pi * day.month / 12),
    }
    return [values[name] for name in FEATURES]


def latest_price(history: list[Purchase]) -> float | None:
    """Baseline 1: the latest comparable price."""
    return history[-1].unit_price if history else None


def rolling_median(history: list[Purchase], window: int = 5) -> float | None:
    """Baseline 2: the median of the last ``window`` comparable prices."""
    return statistics.median(p.unit_price for p in history[-window:]) if history else None
