"use client";

/**
 * Purchase Analytics screen (proposal): price history for each product with the Model 1 expected
 * range, flagged prices, and spend by month and by supplier. Every number comes from the backend
 * (GET /analytics/overview and /analytics/products/{id}); ?product=<id> opens one product.
 * A flag is advice to check the unit price and pack size, never a judgement that a supplier
 * overcharged. Figures from a baseline (no learner beat it, or too little history) say so.
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { PriceChart } from "@/components/PriceChart";
import { errorMessage, getAnalyticsOverview, getProductHistory } from "@/lib/api";
import { money } from "@/lib/money";
import type { AnalyticsOverview, ExpectedPrice, ModelInfo, PriceStatus, ProductHistory } from "@/lib/types";

/** Today as YYYY-MM-DD in the owner's time zone. */
function todayText(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

/** A coloured label for a price check. */
function CheckBadge({ status, pct }: { status: PriceStatus; pct?: number | null }) {
  if (status === "above" || status === "below") {
    return <span className="badge badge-bad">{`${Math.abs(pct ?? 0)}% ${status}`}</span>;
  }
  if (status === "inside") return <span className="badge badge-good">in range</span>;
  return <span className="badge">not checked</span>;
}

/** An expected price, with its range or a baseline label. */
function Expected({ next }: { next: ExpectedPrice }) {
  if (next.expected === null) return <span className="text-[var(--muted)]">—</span>;
  return (
    <span title={next.note}>
      Rs {money(next.expected)}
      {next.low !== null && next.high !== null ? (
        <span className="block text-xs text-[var(--muted)]">
          {money(next.low)} – {money(next.high)}
        </span>
      ) : null}
      {next.is_baseline ? <span className="badge ml-1">baseline</span> : null}
    </span>
  );
}

/** What Model 1 the shop has, in one card. */
function ModelCard({ model }: { model: ModelInfo }) {
  return (
    <div className="bs-card">
      <p className="text-sm font-semibold text-[var(--brand-deep)]">Model 1 · item price</p>
      <p className="mt-1 text-sm">{model.note}</p>
      {model.trained ? (
        <p className="mt-1 text-xs text-[var(--muted)]">
          {model.version}
          {model.trained_at ? ` · trained ${new Date(model.trained_at).toLocaleDateString("en-GB")}` : ""}
          {model.range_low_pct !== null && model.range_high_pct !== null
            ? ` · expected range ${model.range_low_pct > 0 ? "+" : ""}${model.range_low_pct.toFixed(1)}% to +${model.range_high_pct.toFixed(1)}% around the expected price`
            : ""}
        </p>
      ) : null}
    </div>
  );
}

/** One product: its prices over time, the expected range, and every purchase. */
function ProductDetail({ productId, today, onClose }: { productId: string; today: string; onClose: () => void }) {
  const [result, setResult] = useState<{ id: string; history: ProductHistory | null; error: string } | null>(null);
  const [showAll, setShowAll] = useState(false);

  // Load the product whenever another one is opened.
  useEffect(() => {
    let cancelled = false;
    getProductHistory(productId)
      .then((history) => {
        if (!cancelled) setResult({ id: productId, history, error: "" });
      })
      .catch((error) => {
        if (!cancelled) setResult({ id: productId, history: null, error: errorMessage(error) });
      });
    return () => {
      cancelled = true;
    };
  }, [productId]);

  if (!result || result.id !== productId) return <div className="bs-card text-sm text-[var(--muted)]">Loading product…</div>;
  if (result.error || !result.history) return <p className="banner-error">{result.error}</p>;
  const { product, points } = result.history;
  const newestFirst = [...points].reverse();
  const shown = showAll ? newestFirst : newestFirst.slice(0, 15);

  return (
    <div className="bs-card space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-lg font-semibold text-[var(--brand-deep)]">{product.product_name}</p>
          <p className="text-sm text-[var(--muted)]">
            {product.purchases} purchases · {product.first_bought} to {product.last_bought}
            {product.days_between !== null ? ` · about every ${Math.round(product.days_between)} days` : ""} · Rs{" "}
            {money(product.total_spend)} in all
          </p>
        </div>
        <button type="button" className="btn btn-secondary" onClick={onClose}>
          Close
        </button>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <div className="rounded border border-[var(--line)] p-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">Latest price paid</p>
          <p className="mt-1 text-lg font-semibold">Rs {money(product.last_price)}</p>
        </div>
        <div className="rounded border border-[var(--line)] p-3 sm:col-span-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">Expected next purchase</p>
          <p className="mt-1 text-lg font-semibold">
            <Expected next={product.next} />
          </p>
          <p className="mt-1 text-xs text-[var(--muted)]">{product.next.note}</p>
        </div>
      </div>

      <PriceChart points={points} next={product.next} today={today} />

      <div className="overflow-x-auto">
        <table className="entry-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Invoice #</th>
              <th>Supplier</th>
              <th className="text-right">Qty</th>
              <th className="text-right">Unit price</th>
              <th className="text-right">Expected range</th>
              <th>Check</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((point) => (
              <tr key={`${point.invoice_id}-${point.day}-${point.unit_price}`} className={point.status === "above" || point.status === "below" ? "row-issue" : ""}>
                <td className="px-2 py-2">{point.day}</td>
                <td className="px-2 py-2">
                  <Link href={`/invoice-manager/${point.invoice_id}`} className="text-[var(--brand)] hover:underline">
                    {point.invoice_number ?? "open"}
                  </Link>
                </td>
                <td className="px-2 py-2">{point.supplier_name ?? "—"}</td>
                <td className="px-2 py-2 text-right">{point.quantity}</td>
                <td className="px-2 py-2 text-right">{money(point.unit_price)}</td>
                <td className="px-2 py-2 text-right">
                  {point.low !== null && point.high !== null ? `${money(point.low)} – ${money(point.high)}` : "—"}
                </td>
                <td className="px-2 py-2">
                  <CheckBadge status={point.status} pct={point.difference_pct} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {newestFirst.length > shown.length ? (
          <button type="button" className="btn btn-secondary mt-2" onClick={() => setShowAll(true)}>
            Show all {newestFirst.length} purchases
          </button>
        ) : null}
      </div>
    </div>
  );
}

/** The whole screen. */
function AnalyticsScreen() {
  const router = useRouter();
  const selected = useSearchParams().get("product");
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [showAllFlags, setShowAllFlags] = useState(false);
  const [today] = useState(todayText);
  const detailRef = useRef<HTMLDivElement>(null);

  // Load the products, flagged prices and spend once.
  useEffect(() => {
    getAnalyticsOverview()
      .then(setOverview)
      .catch((err) => setError(errorMessage(err)));
  }, []);

  /** Open one product's history above the lists. */
  function openProduct(productId: string) {
    router.push(`/purchase-analytics?product=${productId}`, { scroll: false });
    detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  if (error) {
    return (
      <div>
        <h1 className="text-2xl font-semibold text-[var(--brand-deep)]">Purchase Analytics</h1>
        <p className="banner-error mt-4">{error}</p>
      </div>
    );
  }
  if (!overview) return <p className="text-[var(--muted)]">Loading Purchase Analytics…</p>;

  const needle = filter.trim().toLowerCase();
  const products = needle ? overview.products.filter((p) => p.product_name.toLowerCase().includes(needle)) : overview.products;
  const flags = showAllFlags ? overview.flagged : overview.flagged.slice(0, 8);
  const largestMonth = Math.max(1, ...overview.monthly_purchases.map((month) => month.total));

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold text-[var(--brand-deep)]">Purchase Analytics</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">
          Prices paid for each product over time, Model 1&apos;s expected range, and prices that fell outside it. Verified
          invoices only.
        </p>
      </div>

      <ModelCard model={overview.model} />

      <div ref={detailRef} className="scroll-mt-4">
        {selected ? (
          <ProductDetail
            key={selected}
            productId={selected}
            today={today}
            onClose={() => router.push("/purchase-analytics", { scroll: false })}
          />
        ) : null}
      </div>

      <div className="bs-card overflow-x-auto">
        <p className="text-sm font-semibold text-[var(--brand-deep)]">Prices to check (last 12 months)</p>
        <p className="mb-2 mt-1 text-sm text-[var(--muted)]">
          A unit price outside its expected range. Check the unit price and pack size against the invoice and, if needed,
          with the supplier.
        </p>
        {overview.flagged.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">
            {overview.model.trained ? "No price fell outside its expected range." : "Prices are checked once Model 1 is trained."}
          </p>
        ) : (
          <>
            <table className="entry-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Product</th>
                  <th>Supplier</th>
                  <th>Invoice #</th>
                  <th className="text-right">Unit price</th>
                  <th className="text-right">Expected range</th>
                  <th>Check</th>
                </tr>
              </thead>
              <tbody>
                {flags.map((flag) => (
                  <tr key={`${flag.invoice_id}-${flag.product_id}-${flag.unit_price}`}>
                    <td className="px-2 py-2">{flag.day}</td>
                    <td className="px-2 py-2">
                      <button type="button" className="text-left text-[var(--brand)] hover:underline" onClick={() => openProduct(flag.product_id)}>
                        {flag.product_name}
                      </button>
                    </td>
                    <td className="px-2 py-2">{flag.supplier_name ?? "—"}</td>
                    <td className="px-2 py-2">
                      <Link href={`/invoice-manager/${flag.invoice_id}`} className="text-[var(--brand)] hover:underline">
                        {flag.invoice_number ?? "open"}
                      </Link>
                    </td>
                    <td className="px-2 py-2 text-right">{money(flag.unit_price)}</td>
                    <td className="px-2 py-2 text-right">
                      {money(flag.low)} – {money(flag.high)}
                    </td>
                    <td className="px-2 py-2">
                      <CheckBadge status={flag.status} pct={flag.difference_pct} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {overview.flagged.length > flags.length ? (
              <button type="button" className="btn btn-secondary mt-2" onClick={() => setShowAllFlags(true)}>
                Show all {overview.flagged.length}
              </button>
            ) : null}
          </>
        )}
      </div>

      <div className="bs-card overflow-x-auto">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm font-semibold text-[var(--brand-deep)]">Products ({overview.products.length})</p>
          <input
            className="field max-w-xs"
            placeholder="Find a product"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            aria-label="Find a product"
          />
        </div>
        <table className="entry-table">
          <thead>
            <tr>
              <th>Product</th>
              <th className="text-right">Purchases</th>
              <th>Last bought</th>
              <th className="text-right">Last price</th>
              <th className="text-right">Expected next</th>
              <th className="text-right">Bought about every</th>
              <th className="text-right">Spend</th>
              <th>Last check</th>
            </tr>
          </thead>
          <tbody>
            {products.map((product) => (
              <tr key={product.product_id} className={product.product_id === selected ? "bg-[#eef5f1]" : ""}>
                <td className="px-2 py-2">
                  <button type="button" className="text-left text-[var(--brand)] hover:underline" onClick={() => openProduct(product.product_id)}>
                    {product.product_name}
                  </button>
                </td>
                <td className="px-2 py-2 text-right">{product.purchases}</td>
                <td className="px-2 py-2">{product.last_bought}</td>
                <td className="px-2 py-2 text-right">{money(product.last_price)}</td>
                <td className="px-2 py-2 text-right">
                  <Expected next={product.next} />
                </td>
                <td className="px-2 py-2 text-right">{product.days_between !== null ? `${Math.round(product.days_between)} days` : "—"}</td>
                <td className="px-2 py-2 text-right">{money(product.total_spend)}</td>
                <td className="px-2 py-2">
                  <CheckBadge status={product.last_status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="bs-card">
          <p className="mb-3 text-sm font-semibold text-[var(--brand-deep)]">Spend by month</p>
          <ul className="space-y-2">
            {overview.monthly_purchases.map((month) => (
              <li key={month.month} className="grid grid-cols-[5rem_1fr_7rem] items-center gap-3 text-sm">
                <span>{month.month}</span>
                <div className="bar-track">
                  <div className="bar-fill" style={{ width: `${(month.total / largestMonth) * 100}%` }} />
                </div>
                <span className="text-right">{money(month.total)}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="bs-card overflow-x-auto">
          <p className="mb-3 text-sm font-semibold text-[var(--brand-deep)]">Spend by supplier</p>
          <table className="entry-table">
            <tbody>
              {overview.supplier_spend.map((supplier) => (
                <tr key={supplier.supplier_id}>
                  <td className="px-2 py-2">
                    <Link href={`/supplier-profile?supplier=${supplier.supplier_id}`} className="text-[var(--brand)] hover:underline">
                      {supplier.supplier_name}
                    </Link>
                  </td>
                  <td className="px-2 py-2 text-right">{money(supplier.total)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/** Purchase Analytics page; Suspense is needed because the screen reads ?product= from the address. */
export default function PurchaseAnalyticsPage() {
  return (
    <Suspense fallback={<p className="text-[var(--muted)]">Loading…</p>}>
      <AnalyticsScreen />
    </Suspense>
  );
}
