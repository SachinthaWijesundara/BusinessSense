/**
 * Client-side rollups for the Supplier Profile dashboard.
 * All maths here are display aggregations of GET /suppliers/{id}/profile — no business rules.
 */

import type { InvoiceSummary, SupplierLine, SupplierPayment, SupplierProfile } from "@/lib/types";

/** Inclusive YYYY-MM window. */
export type MonthRange = { fromMonth: string; toMonth: string };

export type MonthPoint = {
  month: string;
  label: string;
  /** Verified purchase total for the month. */
  total: number;
  /** Amount already paid toward that month's verified invoices. */
  paid: number;
  quantity: number;
};

export type SkuSpend = {
  key: string;
  product_id: string | null;
  name: string;
  spend: number;
  quantity: number;
  share: number;
};

/** Purchases rolled up by SKU for a period (qty + average unit price + spend). */
export type SkuPurchase = {
  key: string;
  product_id: string | null;
  name: string;
  quantity: number;
  avgPrice: number | null;
  spend: number;
};

export type FunnelStep = { key: string; label: string; count: number; share: number };

export type SupplierKpis = {
  verifiedSpend: number;
  outstanding: number;
  distinctSkus: number;
  avgInvoice: number | null;
  matchRate: number | null;
  invoiceCount: number;
  /** vs previous equal-length window; null when not enough history */
  verifiedSpendDeltaPct: number | null;
};

/** One month's SKU mix for a pie chart (by quantity purchased). */
export type MonthSkuPie = {
  month: string;
  label: string;
  totalQty: number;
  slices: { key: string; name: string; quantity: number; spend: number; share: number }[];
};

export type SupplierDashboardMetrics = {
  range: MonthRange;
  kpis: SupplierKpis;
  monthlySpend: MonthPoint[];
  skuTrend: { month: string; label: string; series: { key: string; name: string; spend: number }[] }[];
  topSkuKeys: { key: string; name: string }[];
  spendBySku: SkuSpend[];
  funnel: FunnelStep[];
  topSkus: SkuSpend[];
  /** One pie of SKU item quantities for each month in the selected range. */
  monthlySkuPies: MonthSkuPie[];
};

/** Current calendar month as YYYY-MM. */
export function currentMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

/** Shift a YYYY-MM by delta months. */
export function shiftMonth(month: string, delta: number): string {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

/** Inclusive list of YYYY-MM from fromMonth through toMonth. */
export function enumerateMonths(fromMonth: string, toMonth: string): string[] {
  if (fromMonth > toMonth) return [];
  const out: string[] = [];
  let cursor = fromMonth;
  while (cursor <= toMonth) {
    out.push(cursor);
    cursor = shiftMonth(cursor, 1);
    if (out.length > 240) break;
  }
  return out;
}

/** Short axis label: "Mar 2026". */
export function monthLabel(month: string): string {
  const [y, m] = month.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("en-GB", { month: "short", year: "numeric" });
}

/** YYYY-MM of an ISO date string, or null. */
export function monthOf(day: string | null | undefined): string | null {
  if (!day || day.length < 7) return null;
  return day.slice(0, 7);
}

/** Last N calendar months ending at `to` (inclusive). */
export function lastNMonths(n: number, to: string = currentMonth()): MonthRange {
  return { fromMonth: shiftMonth(to, -(n - 1)), toMonth: to };
}

/** Earliest/latest month found on invoices and lines; null if none. */
export function dataMonthBounds(profile: SupplierProfile): MonthRange | null {
  const months: string[] = [];
  for (const invoice of profile.invoices) {
    const m = monthOf(invoice.invoice_date);
    if (m) months.push(m);
  }
  for (const line of profile.lines) {
    const m = monthOf(line.invoice_date);
    if (m) months.push(m);
  }
  if (months.length === 0) return null;
  months.sort();
  return { fromMonth: months[0], toMonth: months[months.length - 1] };
}

/** Default range: last 12 months, clipped to available data when shorter. */
export function defaultRange(profile: SupplierProfile): MonthRange {
  const bounds = dataMonthBounds(profile);
  const last12 = lastNMonths(12);
  if (!bounds) return last12;
  const fromMonth = bounds.fromMonth > last12.fromMonth ? bounds.fromMonth : last12.fromMonth;
  const toMonth = bounds.toMonth < last12.toMonth ? bounds.toMonth : last12.toMonth;
  // No overlap between the data window and the last 12 months (e.g. all data is
  // older than 12 months): fall back to the full data window so the dashboard
  // shows real history instead of an inverted, empty range.
  if (fromMonth > toMonth) return bounds;
  return { fromMonth, toMonth };
}

function inRange(month: string | null, range: MonthRange): boolean {
  return month !== null && month >= range.fromMonth && month <= range.toMonth;
}

function isVerified(invoice: InvoiceSummary): boolean {
  return invoice.status === "verified";
}

/** Stable SKU key for grouping (product id preferred). */
export function skuKey(line: SupplierLine): string {
  if (line.product_id) return `p:${line.product_id}`;
  return `i:${(line.item_name || "unknown").trim().toLowerCase()}`;
}

export function skuName(line: SupplierLine): string {
  return (line.product_name || line.item_name || "Unnamed item").trim();
}

/** Invoices whose invoice_date falls in the range. */
export function invoicesInRange(profile: SupplierProfile, range: MonthRange): InvoiceSummary[] {
  return profile.invoices.filter((invoice) => inRange(monthOf(invoice.invoice_date), range));
}

/** Lines whose invoice_date falls in the range. */
export function linesInRange(profile: SupplierProfile, range: MonthRange): SupplierLine[] {
  return profile.lines.filter((line) => inRange(monthOf(line.invoice_date), range));
}

/** Previous window of the same length, ending the month before fromMonth. */
export function previousEqualRange(range: MonthRange): MonthRange {
  const months = enumerateMonths(range.fromMonth, range.toMonth);
  const len = Math.max(months.length, 1);
  const toMonth = shiftMonth(range.fromMonth, -1);
  return { fromMonth: shiftMonth(toMonth, -(len - 1)), toMonth };
}

function verifiedSpend(invoices: InvoiceSummary[]): number {
  return invoices.filter(isVerified).reduce((sum, invoice) => sum + (invoice.total ?? 0), 0);
}

function buildKpis(profile: SupplierProfile, range: MonthRange): SupplierKpis {
  const invoices = invoicesInRange(profile, range);
  const verified = invoices.filter(isVerified);
  const lines = linesInRange(profile, range);
  const keys = new Set(lines.map(skuKey));
  const matched = lines.filter((line) => Boolean(line.product_id)).length;
  const spend = verifiedSpend(invoices);
  const prev = verifiedSpend(invoicesInRange(profile, previousEqualRange(range)));
  let delta: number | null = null;
  if (prev > 0) delta = ((spend - prev) / prev) * 100;
  else if (spend > 0 && prev === 0) delta = null;

  return {
    verifiedSpend: spend,
    outstanding: profile.totals.outstanding,
    distinctSkus: keys.size,
    avgInvoice: verified.length ? spend / verified.length : null,
    matchRate: lines.length ? (matched / lines.length) * 100 : null,
    invoiceCount: verified.length,
    verifiedSpendDeltaPct: delta,
  };
}

function buildMonthlySpend(profile: SupplierProfile, range: MonthRange): MonthPoint[] {
  const months = enumerateMonths(range.fromMonth, range.toMonth);
  const spend = new Map<string, number>();
  const paid = new Map<string, number>();
  const qty = new Map<string, number>();
  for (const month of months) {
    spend.set(month, 0);
    paid.set(month, 0);
    qty.set(month, 0);
  }
  for (const invoice of invoicesInRange(profile, range)) {
    if (!isVerified(invoice)) continue;
    const m = monthOf(invoice.invoice_date);
    if (!m || !spend.has(m)) continue;
    spend.set(m, (spend.get(m) ?? 0) + (invoice.total ?? 0));
    paid.set(m, (paid.get(m) ?? 0) + Math.max(0, invoice.amount_paid ?? 0));
  }
  for (const line of linesInRange(profile, range)) {
    const m = monthOf(line.invoice_date);
    if (!m || !qty.has(m)) continue;
    qty.set(m, (qty.get(m) ?? 0) + (line.quantity ?? 0));
  }
  return months.map((month) => ({
    month,
    label: monthLabel(month),
    total: spend.get(month) ?? 0,
    paid: paid.get(month) ?? 0,
    quantity: qty.get(month) ?? 0,
  }));
}

function buildSkuSpend(lines: SupplierLine[], limit = 8): SkuSpend[] {
  const map = new Map<string, { product_id: string | null; name: string; spend: number; quantity: number }>();
  for (const line of lines) {
    const key = skuKey(line);
    const current = map.get(key) ?? {
      product_id: line.product_id,
      name: skuName(line),
      spend: 0,
      quantity: 0,
    };
    current.spend += line.line_total ?? 0;
    current.quantity += line.quantity ?? 0;
    if (!current.product_id && line.product_id) current.product_id = line.product_id;
    if (line.product_name) current.name = line.product_name;
    map.set(key, current);
  }
  const rows = [...map.entries()]
    .map(([key, value]) => ({ key, ...value }))
    .sort((a, b) => b.spend - a.spend);
  const total = rows.reduce((sum, row) => sum + row.spend, 0) || 1;
  const top = rows.slice(0, limit);
  const rest = rows.slice(limit);
  const otherSpend = rest.reduce((sum, row) => sum + row.spend, 0);
  const otherQty = rest.reduce((sum, row) => sum + row.quantity, 0);
  const withShare: SkuSpend[] = top.map((row) => ({
    ...row,
    share: row.spend / total,
  }));
  if (otherSpend > 0) {
    withShare.push({
      key: "other",
      product_id: null,
      name: "Other",
      spend: otherSpend,
      quantity: otherQty,
      share: otherSpend / total,
    });
  }
  return withShare;
}

function buildFunnel(invoices: InvoiceSummary[]): FunnelStep[] {
  const draft = invoices.filter((invoice) => invoice.status === "draft").length;
  const verified = invoices.filter((invoice) => invoice.status === "verified").length;
  const paid = invoices.filter((invoice) => invoice.status === "verified" && invoice.payment_status === "paid").length;
  const unpaid = invoices.filter((invoice) => invoice.status === "verified" && invoice.payment_status !== "paid").length;
  const base = Math.max(invoices.length, 1);
  const steps = [
    { key: "all", label: "In range", count: invoices.length },
    { key: "draft", label: "Draft", count: draft },
    { key: "verified", label: "Verified", count: verified },
    { key: "unpaid", label: "Verified unpaid", count: unpaid },
    { key: "paid", label: "Paid", count: paid },
  ];
  return steps.map((step) => ({ ...step, share: step.count / base }));
}

function buildSkuTrend(
  lines: SupplierLine[],
  range: MonthRange,
  topKeys: { key: string; name: string }[],
): SupplierDashboardMetrics["skuTrend"] {
  const months = enumerateMonths(range.fromMonth, range.toMonth);
  return months.map((month) => {
    const series = topKeys.map(({ key, name }) => {
      const spend = lines
        .filter((line) => monthOf(line.invoice_date) === month && skuKey(line) === key)
        .reduce((sum, line) => sum + (line.line_total ?? 0), 0);
      return { key, name, spend };
    });
    return { month, label: monthLabel(month), series };
  });
}

/** SKU quantity mix for every month in the range (top slices + Other). */
function buildMonthlySkuPies(lines: SupplierLine[], range: MonthRange, sliceLimit = 5): MonthSkuPie[] {
  return enumerateMonths(range.fromMonth, range.toMonth).map((month) => {
    const monthLines = lines.filter((line) => monthOf(line.invoice_date) === month);
    const map = new Map<string, { name: string; quantity: number; spend: number }>();
    for (const line of monthLines) {
      const key = skuKey(line);
      const current = map.get(key) ?? { name: skuName(line), quantity: 0, spend: 0 };
      current.quantity += line.quantity ?? 0;
      current.spend += line.line_total ?? 0;
      if (line.product_name) current.name = line.product_name;
      map.set(key, current);
    }
    const ranked = [...map.entries()]
      .map(([key, value]) => ({ key, ...value }))
      .sort((a, b) => b.quantity - a.quantity);
    const totalQty = ranked.reduce((sum, row) => sum + row.quantity, 0);
    const top = ranked.slice(0, sliceLimit);
    const rest = ranked.slice(sliceLimit);
    const otherQty = rest.reduce((sum, row) => sum + row.quantity, 0);
    const otherSpend = rest.reduce((sum, row) => sum + row.spend, 0);
    const denom = totalQty || 1;
    const slices = top.map((row) => ({
      key: row.key,
      name: row.name,
      quantity: row.quantity,
      spend: row.spend,
      share: row.quantity / denom,
    }));
    if (otherQty > 0) {
      slices.push({
        key: `${month}-other`,
        name: "Other",
        quantity: otherQty,
        spend: otherSpend,
        share: otherQty / denom,
      });
    }
    return { month, label: monthLabel(month), totalQty, slices };
  });
}

/** Calendar month as a single-month range. */
export function singleMonthRange(month: string): MonthRange {
  return { fromMonth: month, toMonth: month };
}

/** Previous calendar month (YYYY-MM). */
export function previousMonth(month: string = currentMonth()): string {
  return shiftMonth(month, -1);
}

/** Invoices whose invoice_date falls in one calendar month. */
export function invoicesInMonth(profile: SupplierProfile, month: string): InvoiceSummary[] {
  return invoicesInRange(profile, singleMonthRange(month));
}

/** Lines whose invoice_date falls in one calendar month. */
export function linesInMonth(profile: SupplierProfile, month: string): SupplierLine[] {
  return linesInRange(profile, singleMonthRange(month));
}

/** Payments whose paid_on falls in one calendar month (falls back to created_at). */
export function paymentsInMonth(profile: SupplierProfile, month: string): SupplierPayment[] {
  return profile.payments.filter((payment) => {
    const day = payment.paid_on || payment.created_at;
    return monthOf(day) === month;
  });
}

/** Compact month strip for the slide-up sheet header. */
export type MonthFocusSummary = {
  month: string;
  label: string;
  verifiedSpend: number;
  invoiceCount: number;
  paymentCount: number;
  paymentTotal: number;
};

export function monthFocusSummary(profile: SupplierProfile, month: string): MonthFocusSummary {
  const invoices = invoicesInMonth(profile, month);
  const verified = invoices.filter(isVerified);
  const payments = paymentsInMonth(profile, month);
  return {
    month,
    label: monthLabel(month),
    verifiedSpend: verifiedSpend(verified),
    invoiceCount: invoices.length,
    paymentCount: payments.length,
    paymentTotal: payments.reduce((sum, payment) => sum + (payment.amount ?? 0), 0),
  };
}

/** SKU pie for a single focus month (reuse monthly pie builder). */
export function focusMonthPie(profile: SupplierProfile, month: string): MonthSkuPie {
  const pies = buildMonthlySkuPies(linesInMonth(profile, month), singleMonthRange(month));
  return pies[0] ?? { month, label: monthLabel(month), totalQty: 0, slices: [] };
}

/**
 * Group verified item lines by SKU for a period.
 * Avg price is spend ÷ qty when qty > 0; otherwise the latest known unit rate.
 */
export function buildSkuPurchases(lines: SupplierLine[]): SkuPurchase[] {
  const map = new Map<
    string,
    { product_id: string | null; name: string; quantity: number; spend: number; lastRate: number | null }
  >();
  for (const line of lines) {
    const key = skuKey(line);
    const current = map.get(key) ?? {
      product_id: line.product_id,
      name: skuName(line),
      quantity: 0,
      spend: 0,
      lastRate: null,
    };
    current.quantity += line.quantity ?? 0;
    current.spend += line.line_total ?? 0;
    if (line.mro !== null && line.mro !== undefined) current.lastRate = line.mro;
    if (!current.product_id && line.product_id) current.product_id = line.product_id;
    if (line.product_name) current.name = line.product_name;
    map.set(key, current);
  }
  return [...map.entries()]
    .map(([key, value]) => {
      const avgPrice =
        value.quantity > 0 && value.spend > 0
          ? value.spend / value.quantity
          : value.lastRate;
      return {
        key,
        product_id: value.product_id,
        name: value.name,
        quantity: value.quantity,
        avgPrice,
        spend: value.spend,
      };
    })
    .sort((a, b) => b.spend - a.spend || a.name.localeCompare(b.name));
}

/** SKU purchases for one calendar month. */
export function skuPurchasesInMonth(profile: SupplierProfile, month: string): SkuPurchase[] {
  return buildSkuPurchases(linesInMonth(profile, month));
}

/** SKU purchases for an inclusive month range. */
export function skuPurchasesInRange(profile: SupplierProfile, range: MonthRange): SkuPurchase[] {
  return buildSkuPurchases(linesInRange(profile, range));
}

/** sessionStorage key for the shared supplier focus month. */
export const FOCUS_MONTH_STORAGE_KEY = "bs-supplier-focus-month";

/** Read a persisted focus month, or fall back to the current calendar month. */
export function readFocusMonth(): string {
  if (typeof window === "undefined") return currentMonth();
  try {
    const stored = window.sessionStorage.getItem(FOCUS_MONTH_STORAGE_KEY);
    if (stored && /^\d{4}-\d{2}$/.test(stored)) return stored;
  } catch {
    /* ignore */
  }
  return currentMonth();
}

/** Persist focus month so it survives switching suppliers in this tab. */
export function writeFocusMonth(month: string): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(FOCUS_MONTH_STORAGE_KEY, month);
  } catch {
    /* ignore */
  }
}

/** Full dashboard series for one profile + month window. */
export function buildSupplierDashboard(profile: SupplierProfile, range: MonthRange): SupplierDashboardMetrics {
  const safe: MonthRange =
    range.fromMonth <= range.toMonth ? range : { fromMonth: range.toMonth, toMonth: range.fromMonth };
  const lines = linesInRange(profile, safe);
  const invoices = invoicesInRange(profile, safe);
  const spendBySku = buildSkuSpend(lines, 6);
  const topSkus = spendBySku.filter((row) => row.key !== "other");
  const topSkuKeys = topSkus.slice(0, 3).map((row) => ({ key: row.key, name: row.name }));

  return {
    range: safe,
    kpis: buildKpis(profile, safe),
    monthlySpend: buildMonthlySpend(profile, safe),
    skuTrend: buildSkuTrend(lines, safe, topSkuKeys),
    topSkuKeys,
    spendBySku,
    funnel: buildFunnel(invoices),
    topSkus,
    monthlySkuPies: buildMonthlySkuPies(lines, safe),
  };
}
