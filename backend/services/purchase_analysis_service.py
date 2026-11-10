"""What the shop bought: purchase totals over time and per supplier (proposal: "monthly purchases",
"supplier spending" and the Supplier Profile's item lines), and Purchase Analytics with Model 1.

Totals are plain Python over verified invoices only. Purchase Analytics adds, for each product,
its comparable unit prices over time with Model 1's expected range, the prices that fell outside
it ("check the unit price and pack size"), and the expected price of the next purchase. Each price
is judged only from the purchases before it. Products with too little history, or a shop whose
Model 1 is not trained yet, show the median of recent prices labelled as a baseline.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from database.client import Invoice, InvoiceSummary, ModelPrediction, SupplierLine
from ml.features import MIN_HISTORY, Purchase, by_product, clean, latest_price, rolling_median
from ml.inference import ItemPriceModel, PriceCheck, PricePrediction

from .cash_flow_service import (
    OverdueBalances,
    PayableDay,
    money_sum,
    overdue_balances,
    payable_cash_series,
)

if TYPE_CHECKING:
    from main import AppServices


class MonthTotal(BaseModel):
    """Verified purchases in one month ("2026-10")."""

    month: str
    total: float


class MonthPurchasePaid(BaseModel):
    """Verified purchases and amount_paid for invoices dated in one month."""

    month: str
    purchases: float
    paid: float


class SupplierSpend(BaseModel):
    """Verified purchases from one supplier."""

    supplier_id: str
    supplier_name: str
    total: float


class SpendSlice(BaseModel):
    """One pie slice (supplier or item) with share of period spend."""

    key: str
    name: str
    total: float
    share: float


class CashOverview(BaseModel):
    """Dashboard cash / purchase charts: payable series, overdue, monthly purchases+paid, pies."""

    payable_days: int
    months: int
    payable_series: list[PayableDay]
    overdue: OverdueBalances
    monthly: list[MonthPurchasePaid]
    spend_by_supplier: list[SpendSlice]
    spend_by_item: list[SpendSlice]


def last_months(today: date, count: int = 12) -> list[str]:
    """The last ``count`` months as "YYYY-MM", oldest first, ending with this month."""
    months = []
    year, month = today.year, today.month
    for _ in range(count):
        months.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(months))


def monthly_purchases(invoices: list[InvoiceSummary], *, today: date, months: int = 12) -> list[MonthTotal]:
    """Verified purchases per month by invoice date, for the last ``months`` months (0 when none)."""
    keys = last_months(today, months)
    by_month: dict[str, list[float | None]] = {key: [] for key in keys}
    for inv in invoices:
        if inv.status == "verified" and inv.invoice_date is not None:
            key = inv.invoice_date.strftime("%Y-%m")
            if key in by_month:
                by_month[key].append(inv.total)
    return [MonthTotal(month=key, total=money_sum(by_month[key])) for key in keys]


def monthly_purchases_and_paid(
    invoices: list[InvoiceSummary], *, today: date, months: int = 3
) -> list[MonthPurchasePaid]:
    """Verified purchases and amount_paid per invoice month for the last ``months`` months."""
    keys = last_months(today, months)
    purchases: dict[str, list[float | None]] = {key: [] for key in keys}
    paid: dict[str, list[float | None]] = {key: [] for key in keys}
    for inv in invoices:
        if inv.status != "verified" or inv.invoice_date is None:
            continue
        key = inv.invoice_date.strftime("%Y-%m")
        if key not in purchases:
            continue
        purchases[key].append(inv.total)
        paid[key].append(inv.amount_paid or 0.0)
    return [
        MonthPurchasePaid(month=key, purchases=money_sum(purchases[key]), paid=money_sum(paid[key]))
        for key in keys
    ]


def supplier_spend(invoices: list[InvoiceSummary], *, limit: int = 10) -> list[SupplierSpend]:
    """Verified purchases per supplier, highest first (top ``limit``)."""
    by_supplier: dict[str, tuple[str, list[float | None]]] = {}
    for inv in invoices:
        if inv.status == "verified" and inv.supplier_id:
            name, totals = by_supplier.setdefault(inv.supplier_id, (inv.supplier_name or "Unknown supplier", []))
            totals.append(inv.total)
    spend = [SupplierSpend(supplier_id=sid, supplier_name=name, total=money_sum(totals)) for sid, (name, totals) in by_supplier.items()]
    return sorted(spend, key=lambda row: row.total, reverse=True)[:limit]


def _month_window_bounds(today: date, months: int) -> tuple[date, date]:
    """Inclusive invoice_date window covering the last ``months`` calendar months."""
    keys = last_months(today, months)
    first = keys[0]
    year, month = int(first[:4]), int(first[5:7])
    return date(year, month, 1), today


def spend_slices_by_supplier(
    invoices: list[InvoiceSummary], *, today: date, months: int = 3, slice_limit: int = 8
) -> list[SpendSlice]:
    """Verified spend by supplier in the lookback, top slices + Other."""
    start, end = _month_window_bounds(today, months)
    by_supplier: dict[str, tuple[str, list[float | None]]] = {}
    for inv in invoices:
        if inv.status != "verified" or inv.invoice_date is None:
            continue
        if inv.invoice_date < start or inv.invoice_date > end:
            continue
        sid = inv.supplier_id or "unknown"
        name = inv.supplier_name or "Unknown supplier"
        _, totals = by_supplier.setdefault(sid, (name, []))
        if inv.supplier_name:
            by_supplier[sid] = (inv.supplier_name, totals)
        totals.append(inv.total)
    ranked = sorted(
        (
            SpendSlice(key=sid, name=name, total=money_sum(totals), share=0.0)
            for sid, (name, totals) in by_supplier.items()
        ),
        key=lambda row: row.total,
        reverse=True,
    )
    return _with_other_slices(ranked, slice_limit=slice_limit)


def spend_slices_by_item(lines: list[SupplierLine], *, slice_limit: int = 8) -> list[SpendSlice]:
    """Verified line spend by product_id; unmatched lines bucket to Unmatched."""
    by_item: dict[str, tuple[str, list[float | None]]] = {}
    for line in lines:
        if line.product_id:
            key, name = line.product_id, (line.product_name or line.item_name or "Item")
        else:
            key, name = "unmatched", "Unmatched"
        _, totals = by_item.setdefault(key, (name, []))
        if line.product_id and line.product_name:
            by_item[key] = (line.product_name, totals)
        totals.append(line.line_total)
    ranked = sorted(
        (
            SpendSlice(key=key, name=name, total=money_sum(totals), share=0.0)
            for key, (name, totals) in by_item.items()
        ),
        key=lambda row: row.total,
        reverse=True,
    )
    return _with_other_slices(ranked, slice_limit=slice_limit)


def _with_other_slices(ranked: list[SpendSlice], *, slice_limit: int) -> list[SpendSlice]:
    """Keep the top slices, fold the rest into Other, and fill share."""
    total = money_sum([row.total for row in ranked]) or 1.0
    top = ranked[:slice_limit]
    rest = ranked[slice_limit:]
    other_total = money_sum([row.total for row in rest])
    slices = [
        SpendSlice(key=row.key, name=row.name, total=row.total, share=row.total / total)
        for row in top
    ]
    if other_total > 0:
        slices.append(SpendSlice(key="other", name="Other", total=other_total, share=other_total / total))
    return slices


async def cash_overview(
    services: AppServices,
    business_id: str,
    *,
    today: date,
    payable_days: int = 30,
    months: int = 3,
) -> CashOverview:
    """Payable cash-out series plus monthly purchases/paid and supplier/item pies."""
    payable_days = max(1, min(int(payable_days), 365))
    months = max(1, min(int(months), 36))
    invoices = await services.db.list_all_invoices(business_id)
    start, end = _month_window_bounds(today, months)
    lines = await services.db.list_verified_lines(business_id, date_from=start, date_to=end)
    return CashOverview(
        payable_days=payable_days,
        months=months,
        payable_series=payable_cash_series(invoices, today=today, window_days=payable_days),
        overdue=overdue_balances(invoices, today=today),
        monthly=monthly_purchases_and_paid(invoices, today=today, months=months),
        spend_by_supplier=spend_slices_by_supplier(invoices, today=today, months=months),
        spend_by_item=spend_slices_by_item(lines),
    )


async def supplier_lines(services: AppServices, business_id: str, supplier_id: str) -> list[SupplierLine]:
    """The item lines of a supplier's verified invoices, newest first."""
    return await services.db.list_supplier_lines(business_id, supplier_id)



# =============================================================================================
# Purchase Analytics (Model 1)
# =============================================================================================

FLAG_WINDOW_DAYS = 365  # flagged prices from the last year are listed
CheckStatus = Literal["inside", "above", "below", "no_range"]


class ModelInfo(BaseModel):
    """Which Model 1 the shop has, for the screen to say what its figures are."""

    trained: bool
    version: str | None = None
    method: str | None = None  # the method in use: a learner or a baseline
    learner_kept: bool = False
    trained_at: str | None = None
    range_low_pct: float | None = None
    range_high_pct: float | None = None
    note: str


class ExpectedPrice(BaseModel):
    """Model 1's expectation for a purchase (or a labelled baseline)."""

    expected: float | None
    low: float | None
    high: float | None
    method: str
    is_baseline: bool
    earlier_purchases: int
    note: str


class PricePoint(BaseModel):
    """One past purchase of a product, judged from the purchases before it."""

    day: date
    invoice_id: str
    invoice_number: str | None
    supplier_name: str | None
    quantity: float
    unit_price: float
    expected: float | None
    low: float | None
    high: float | None
    status: CheckStatus
    difference_pct: float | None


class ProductSummary(BaseModel):
    """One product in the Purchase Analytics list."""

    product_id: str
    product_name: str
    purchases: int
    first_bought: date
    last_bought: date
    last_price: float
    total_spend: float
    days_between: float | None  # median days between purchases (purchase frequency)
    next: ExpectedPrice
    last_status: CheckStatus


class FlaggedPrice(BaseModel):
    """A purchase whose unit price fell outside its expected range."""

    product_id: str
    product_name: str
    day: date
    invoice_id: str
    invoice_number: str | None
    supplier_name: str | None
    unit_price: float
    expected: float
    low: float
    high: float
    status: Literal["above", "below"]
    difference_pct: float
    message: str


class AnalyticsOverview(BaseModel):
    """Everything the Purchase Analytics screen shows first."""

    model: ModelInfo
    products: list[ProductSummary]
    flagged: list[FlaggedPrice]
    monthly_purchases: list[MonthTotal]
    supplier_spend: list[SupplierSpend]


class ProductHistory(BaseModel):
    """One product's price history with its expected range, and its next expected price."""

    model: ModelInfo
    product: ProductSummary
    points: list[PricePoint]
    spend_by_month: list[MonthTotal]


class LineCheck(BaseModel):
    """The price check of one invoice line against the product's earlier purchases."""

    line_id: str
    item_name: str
    product_id: str | None
    unit_price: float | None
    expected: float | None
    low: float | None
    high: float | None
    status: Literal["inside", "above", "below", "no_range", "no_product"]
    is_baseline: bool
    difference_pct: float | None
    message: str


def model_info(model: ItemPriceModel | None) -> ModelInfo:
    """What the screen says about the shop's Model 1."""
    if model is None:
        return ModelInfo(trained=False, note="Model 1 has not been trained for this shop yet: prices are compared "
                                             "with the median of recent purchases, shown as a baseline.")
    report = model.report or {}
    range_pct = report.get("range_pct") or {}
    note = (f"Model 1 uses {model.method}: it beat both baselines on later purchases."
            if model.learned else
            f"Model 1 uses the {model.method} baseline: no learner beat both baselines on later purchases.")
    return ModelInfo(trained=True, version=model.version, method=model.method, learner_kept=model.learned,
                     trained_at=report.get("trained_at"), range_low_pct=range_pct.get("low"),
                     range_high_pct=range_pct.get("high"), note=note)


def _expect(model: ItemPriceModel | None, history: list[Purchase], day: date, quantity: float) -> PricePrediction:
    """Model 1's expectation, or the median of recent prices when the shop has no model yet."""
    if model is not None:
        return model.predict(history, day, quantity)
    history = [p for p in history if p.day <= day]
    median = rolling_median(history)
    return PricePrediction(None if median is None else round(median, 2), None, None, "rolling median", True,
                           len(history), latest_price(history),
                           "Median of the last prices (Model 1 is not trained yet), shown as a baseline.")


def _check(model: ItemPriceModel | None, expected: PricePrediction, unit_price: float) -> PriceCheck:
    """Model 1's price check (no range, so no flag, without a model)."""
    if model is None:
        return PriceCheck("no_range", None, None, "Model 1 is not trained yet, so prices are not checked.")
    return model.check(expected, unit_price)


def _expected(prediction: PricePrediction) -> ExpectedPrice:
    """An expectation for the screen."""
    return ExpectedPrice(expected=prediction.expected, low=prediction.low, high=prediction.high, method=prediction.method,
                         is_baseline=prediction.is_baseline, earlier_purchases=prediction.earlier_purchases,
                         note=prediction.note)


def _points(model: ItemPriceModel | None, history: list[Purchase]) -> list[tuple[Purchase, PricePrediction, PriceCheck]]:
    """Every purchase of one product judged from the purchases before it."""
    out = []
    for k, purchase in enumerate(history):
        prediction = _expect(model, history[:k], purchase.day, purchase.quantity)
        out.append((purchase, prediction, _check(model, prediction, purchase.unit_price)))
    return out


def _summary(model: ItemPriceModel | None, history: list[Purchase], judged, today: date) -> ProductSummary:
    """One product's line in the list, with its next expected price."""
    gaps = [(b.day - a.day).days for a, b in zip(history, history[1:], strict=False)]
    usual_qty = statistics.median(p.quantity for p in history)
    return ProductSummary(
        product_id=history[0].product_id, product_name=history[0].product_name, purchases=len(history),
        first_bought=history[0].day, last_bought=history[-1].day, last_price=history[-1].unit_price,
        total_spend=money_sum([p.unit_price * p.quantity for p in history]),
        days_between=float(statistics.median(gaps)) if gaps else None,
        next=_expected(_expect(model, history, today, usual_qty)),
        last_status=judged[-1][2].status,
    )


async def _history(services: AppServices, business_id: str, today: date, product_id: str | None = None) -> dict[str, list[Purchase]]:
    """Comparable purchases per product (verified, product-linked, cleaned)."""
    usable, _, _ = clean(await services.db.purchase_lines(business_id, product_id), today=today)
    return by_product(usable)


async def analytics_overview(services: AppServices, business_id: str, *, today: date) -> AnalyticsOverview:
    """Products with their next expected price, flagged prices of the last year, and spend."""
    model = await services.price_model(business_id)
    products, flagged = [], []
    since = today - timedelta(days=FLAG_WINDOW_DAYS)
    for history in (await _history(services, business_id, today)).values():
        judged = _points(model, history)
        products.append(_summary(model, history, judged, today))
        for purchase, prediction, check in judged:
            if check.status in ("above", "below") and purchase.day >= since:
                assert prediction.expected and prediction.low and prediction.high and check.difference_pct is not None
                flagged.append(FlaggedPrice(
                    product_id=purchase.product_id, product_name=purchase.product_name, day=purchase.day,
                    invoice_id=purchase.invoice_id, invoice_number=purchase.invoice_number,
                    supplier_name=purchase.supplier_name, unit_price=purchase.unit_price, expected=prediction.expected,
                    low=prediction.low, high=prediction.high, status=check.status,
                    difference_pct=check.difference_pct, message=check.message))
    invoices = await services.db.list_all_invoices(business_id)
    return AnalyticsOverview(
        model=model_info(model),
        products=sorted(products, key=lambda p: p.total_spend, reverse=True),
        flagged=sorted(flagged, key=lambda f: f.day, reverse=True),
        monthly_purchases=monthly_purchases(invoices, today=today),
        supplier_spend=supplier_spend(invoices),
    )


async def product_history(services: AppServices, business_id: str, product_id: str, *, today: date) -> ProductHistory | None:
    """One product's prices over time with the expected range; None when it has no verified purchases.

    The next-purchase expectation is kept in model_predictions (once per day and model version),
    so it can later be compared with the price actually paid.
    """
    model = await services.price_model(business_id)
    history = (await _history(services, business_id, today, product_id)).get(product_id)
    if not history:
        return None
    judged = _points(model, history)
    summary = _summary(model, history, judged, today)
    if model is not None and summary.next.expected is not None:
        await services.db.save_prediction(business_id, ModelPrediction(
            model_name="item_price", model_version=model.version, product_id=product_id, target_date=today,
            horizon="next purchase", predicted_value=summary.next.expected, lower_bound=summary.next.low,
            upper_bound=summary.next.high, baseline_name="rolling median", baseline_value=rolling_median(history),
            payload={"method": summary.next.method, "is_baseline": summary.next.is_baseline,
                     "earlier_purchases": summary.next.earlier_purchases, "latest_price": summary.last_price}))
    by_month: dict[str, list[float | None]] = {}
    for p in history:
        by_month.setdefault(p.day.strftime("%Y-%m"), []).append(p.unit_price * p.quantity)
    return ProductHistory(
        model=model_info(model), product=summary,
        points=[PricePoint(day=p.day, invoice_id=p.invoice_id, invoice_number=p.invoice_number,
                           supplier_name=p.supplier_name, quantity=p.quantity, unit_price=p.unit_price,
                           expected=pred.expected, low=pred.low, high=pred.high, status=check.status,
                           difference_pct=check.difference_pct)
                for p, pred, check in judged],
        spend_by_month=[MonthTotal(month=month, total=money_sum(values)) for month, values in sorted(by_month.items())],
    )


async def invoice_price_check(services: AppServices, business_id: str, invoice: Invoice, *, today: date) -> list[LineCheck]:
    """Each line's unit price against its product's earlier purchases (proposal: check the unit
    price and package size before the invoice is accepted). The invoice itself is left out."""
    model = await services.price_model(business_id)
    day = invoice.invoice_date or today
    linked = {line.product_id for line in invoice.lines if line.product_id}
    histories: dict[str, list[Purchase]] = {}
    for product_id in linked:
        assert product_id is not None
        purchases = (await _history(services, business_id, today, product_id)).get(product_id, [])
        histories[product_id] = [p for p in purchases if p.invoice_id != invoice.id and p.day <= day]
    checks = []
    for line in invoice.lines:
        unit = (round(float(line.line_total) / float(line.quantity), 4)
                if line.line_total and line.quantity and line.quantity > 0 and line.line_total > 0 else None)
        if not line.product_id or unit is None:
            checks.append(LineCheck(line_id=line.id, item_name=line.item_name, product_id=line.product_id, unit_price=unit,
                                    expected=None, low=None, high=None, status="no_product", is_baseline=True,
                                    difference_pct=None,
                                    message="Link the line to a product (with its quantity and amount) to check its price."))
            continue
        prediction = _expect(model, histories[line.product_id], day, float(line.quantity or 1))
        check = _check(model, prediction, unit)
        message = check.message if check.status != "no_range" else (
            prediction.note if prediction.earlier_purchases < MIN_HISTORY else check.message)
        checks.append(LineCheck(line_id=line.id, item_name=line.item_name, product_id=line.product_id, unit_price=unit,
                                expected=prediction.expected, low=prediction.low, high=prediction.high,
                                status=check.status, is_baseline=prediction.is_baseline,
                                difference_pct=check.difference_pct, message=message))
    return checks
