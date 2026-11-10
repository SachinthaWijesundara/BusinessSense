"use client";

/**
 * Supplier Profile analytics pane: KPIs and month-by-month charts from GET /suppliers/{id}/profile.
 * Charts stay primary; press a mark for the real value. Ledger & payments hold the tables.
 */

import { useMemo, useState } from "react";
import { money } from "@/lib/money";
import {
  buildSupplierDashboard,
  currentMonth,
  dataMonthBounds,
  defaultRange,
  lastNMonths,
  type MonthRange,
  type MonthPoint,
  type MonthSkuPie,
  type SkuSpend,
  type SupplierDashboardMetrics,
} from "@/lib/supplierMetrics";
import type { SupplierProfile } from "@/lib/types";

const PAID_COLOUR = "#c4a35a";

const TREND_COLOURS = ["#1f6f54", "#c4a35a", "#2b6cb0"];
const PIE_COLOURS = ["#1f6f54", "#2b6cb0", "#c4a35a", "#6b8f71", "#9b2c2c", "#5a6f66", "#8a9a92"];

type ChartTip = { id: string; label: string; value: string };

/** Format a signed percent for KPI deltas. */
function deltaText(value: number | null): string | null {
  if (value === null || Number.isNaN(value)) return null;
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}% vs prior period`;
}

/** One KPI card. */
function Kpi({
  label,
  value,
  hint,
  note,
}: {
  label: string;
  value: string;
  hint?: string | null;
  note?: string;
}) {
  const up = hint?.startsWith("+");
  const down = hint?.startsWith("-");
  return (
    <div className="bs-card supplier-kpi">
      <p className="supplier-kpi-label">{label}</p>
      <p className="supplier-kpi-value">{value}</p>
      {hint ? (
        <p className={`supplier-kpi-hint ${up ? "is-up" : down ? "is-down" : ""}`}>{hint}</p>
      ) : note ? (
        <p className="supplier-kpi-hint">{note}</p>
      ) : null}
    </div>
  );
}

/** Floating value tip shown after pressing a chart mark. */
function ChartValueTip({ tip, onDismiss }: { tip: ChartTip | null; onDismiss: () => void }) {
  if (!tip) return null;
  return (
    <div className="supplier-chart-tip" role="status">
      <div>
        <p className="supplier-chart-tip-label">{tip.label}</p>
        <p className="supplier-chart-tip-value">{tip.value}</p>
      </div>
      <button type="button" className="supplier-chart-tip-close" onClick={onDismiss} aria-label="Dismiss value">
        ×
      </button>
    </div>
  );
}

/** Toggle tip: same id dismisses; otherwise replaces. */
function toggleTip(current: ChartTip | null, next: ChartTip): ChartTip | null {
  return current?.id === next.id ? null : next;
}

/** Presets + from/to month inputs (same control as the column chart period). */
function RangeControl({
  range,
  onChange,
  bounds,
  label = "Period",
  embedded = false,
}: {
  range: MonthRange;
  onChange: (next: MonthRange) => void;
  bounds: MonthRange | null;
  label?: string;
  embedded?: boolean;
}) {
  const maxMonth = currentMonth();
  const minMonth = bounds?.fromMonth ?? shiftSafe(maxMonth, -36);

  function applyPreset(months: number | "all") {
    if (months === "all") {
      onChange(bounds ?? lastNMonths(12));
      return;
    }
    onChange(lastNMonths(months));
  }

  return (
    <div className={embedded ? "supplier-range supplier-range-embedded" : "supplier-range bs-card"}>
      <div className="supplier-range-presets">
        <span className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">{label}</span>
        <button type="button" className="btn btn-secondary !py-1 !px-2 text-xs" onClick={() => applyPreset(3)}>
          3 mo
        </button>
        <button type="button" className="btn btn-secondary !py-1 !px-2 text-xs" onClick={() => applyPreset(6)}>
          6 mo
        </button>
        <button type="button" className="btn btn-secondary !py-1 !px-2 text-xs" onClick={() => applyPreset(12)}>
          12 mo
        </button>
        <button type="button" className="btn btn-secondary !py-1 !px-2 text-xs" onClick={() => applyPreset("all")}>
          All time
        </button>
      </div>
      <div className="supplier-range-custom">
        <label className="text-sm">
          From
          <input
            className="field ml-2"
            type="month"
            min={minMonth}
            max={range.toMonth}
            value={range.fromMonth}
            onChange={(event) => onChange({ ...range, fromMonth: event.target.value })}
          />
        </label>
        <label className="text-sm">
          To
          <input
            className="field ml-2"
            type="month"
            min={range.fromMonth}
            max={maxMonth}
            value={range.toMonth}
            onChange={(event) => onChange({ ...range, toMonth: event.target.value })}
          />
        </label>
      </div>
    </div>
  );
}

function shiftSafe(month: string, delta: number): string {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

/** Monthly purchases + paid as grouped bars — press a bar for the exact value. */
function MonthlySpendChart({ points }: { points: MonthPoint[] }) {
  const [tip, setTip] = useState<ChartTip | null>(null);
  const series =
    points.length > 0 ? points : [{ month: "—", label: "—", total: 0, paid: 0, quantity: 0 }];
  const max = Math.max(...series.flatMap((p) => [p.total, p.paid]), 1);
  const width = Math.max(360, series.length * 52);
  const height = 220;
  const pad = { top: 16, right: 12, bottom: 40, left: 56 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const gap = 8;
  const groupW = Math.max(16, innerW / series.length - gap);
  const barW = Math.max(6, (groupW - 2) / 2);
  const empty = points.length === 0 || points.every((p) => p.total === 0 && p.paid === 0);

  return (
    <div className="supplier-chart-frame" onClick={() => setTip(null)}>
      {empty ? <p className="supplier-chart-empty">No verified spend in this range — axes still show the months.</p> : null}
      <ChartValueTip tip={tip} onDismiss={() => setTip(null)} />
      <svg viewBox={`0 0 ${width} ${height}`} className="supplier-chart-svg" role="img" aria-label="Monthly purchases and paid">
        {[0, 0.5, 1].map((t) => {
          const y = pad.top + innerH * (1 - t);
          return (
            <g key={t}>
              <line x1={pad.left} x2={width - pad.right} y1={y} y2={y} stroke="#e3ebe6" />
              <text x={pad.left - 6} y={y + 3} textAnchor="end" fontSize="10" fill="#5a6f66">
                {money(max * t)}
              </text>
            </g>
          );
        })}
        {series.map((point, index) => {
          const groupX = pad.left + index * (groupW + gap) + gap / 2;
          const purchH = (point.total / max) * innerH;
          const paidH = (point.paid / max) * innerH;
          const purchY = pad.top + innerH - purchH;
          const paidY = pad.top + innerH - paidH;
          const purchActive = tip?.id === `purch-${point.month}`;
          const paidActive = tip?.id === `paid-${point.month}`;
          return (
            <g key={point.month + index}>
              <rect
                className="supplier-chart-hit"
                x={groupX}
                y={pad.top}
                width={barW}
                height={innerH}
                fill="transparent"
                onClick={(event) => {
                  event.stopPropagation();
                  setTip((current) =>
                    toggleTip(current, {
                      id: `purch-${point.month}`,
                      label: `${point.label} · Purchases`,
                      value: `Rs ${money(point.total)}`,
                    }),
                  );
                }}
              />
              <rect
                x={groupX}
                y={purchY}
                width={barW}
                height={Math.max(purchH, point.total > 0 ? 3 : 0)}
                fill={purchActive ? "#0f4a38" : "#1f6f54"}
                rx="2"
                pointerEvents="none"
              />
              <rect
                className="supplier-chart-hit"
                x={groupX + barW + 2}
                y={pad.top}
                width={barW}
                height={innerH}
                fill="transparent"
                onClick={(event) => {
                  event.stopPropagation();
                  setTip((current) =>
                    toggleTip(current, {
                      id: `paid-${point.month}`,
                      label: `${point.label} · Paid`,
                      value: `Rs ${money(point.paid)}`,
                    }),
                  );
                }}
              />
              <rect
                x={groupX + barW + 2}
                y={paidY}
                width={barW}
                height={Math.max(paidH, point.paid > 0 ? 3 : 0)}
                fill={paidActive ? "#9a7d3a" : PAID_COLOUR}
                rx="2"
                pointerEvents="none"
              />
              <text
                x={groupX + groupW / 2}
                y={height - 12}
                textAnchor="middle"
                fontSize="9"
                fill="#5a6f66"
                transform={series.length > 8 ? `rotate(-35 ${groupX + groupW / 2} ${height - 12})` : undefined}
              >
                {point.label.split(" ")[0]}
              </text>
            </g>
          );
        })}
      </svg>
      <ul className="mt-2 flex flex-wrap gap-3 text-xs text-[var(--muted)]">
        <li className="flex items-center gap-1">
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: "#1f6f54" }} />
          Purchases
        </li>
        <li className="flex items-center gap-1">
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: PAID_COLOUR }} />
          Paid
        </li>
      </ul>
    </div>
  );
}

/** Multi-series SVG line chart — press a point for the exact SKU value. */
function SkuTrendChart({ metrics }: { metrics: SupplierDashboardMetrics }) {
  const [tip, setTip] = useState<ChartTip | null>(null);
  const { skuTrend, topSkuKeys } = metrics;
  const months = skuTrend.length > 0 ? skuTrend : [{ month: "—", label: "—", series: [] as { key: string; name: string; spend: number }[] }];
  const width = 480;
  const height = 220;
  const pad = { top: 16, right: 12, bottom: 40, left: 56 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const max = Math.max(...months.flatMap((row) => row.series.map((s) => s.spend)), 1);
  const x = (i: number) => pad.left + (months.length <= 1 ? innerW / 2 : (i / (months.length - 1)) * innerW);
  const y = (v: number) => pad.top + (1 - v / max) * innerH;
  const empty = topSkuKeys.length === 0;

  return (
    <div className="supplier-chart-frame" onClick={() => setTip(null)}>
      {empty ? <p className="supplier-chart-empty">No SKU lines in this range — chart frame kept for layout.</p> : null}
      <ChartValueTip tip={tip} onDismiss={() => setTip(null)} />
      <svg viewBox={`0 0 ${width} ${height}`} className="supplier-chart-svg" role="img" aria-label="SKU spend by month">
        {[0, 0.5, 1].map((t) => {
          const yy = pad.top + innerH * (1 - t);
          return (
            <g key={t}>
              <line x1={pad.left} x2={width - pad.right} y1={yy} y2={yy} stroke="#e3ebe6" />
              <text x={pad.left - 6} y={yy + 3} textAnchor="end" fontSize="10" fill="#5a6f66">
                {money(max * t)}
              </text>
            </g>
          );
        })}
        {topSkuKeys.map((sku, seriesIndex) => {
          const pts = months.map((row, i) => {
            const spend = row.series.find((s) => s.key === sku.key)?.spend ?? 0;
            return `${x(i)},${y(spend)}`;
          });
          return (
            <polyline
              key={sku.key}
              fill="none"
              stroke={TREND_COLOURS[seriesIndex % TREND_COLOURS.length]}
              strokeWidth="2.5"
              points={pts.join(" ")}
              pointerEvents="none"
            />
          );
        })}
        {topSkuKeys.map((sku, seriesIndex) =>
          months.map((row, i) => {
            const spend = row.series.find((s) => s.key === sku.key)?.spend ?? 0;
            const id = `line-${sku.key}-${row.month}`;
            const active = tip?.id === id;
            return (
              <circle
                key={id}
                className="supplier-chart-hit"
                cx={x(i)}
                cy={y(spend)}
                r={active ? 6 : 5}
                fill={TREND_COLOURS[seriesIndex % TREND_COLOURS.length]}
                stroke="#fff"
                strokeWidth={active ? 2 : 1}
                onClick={(event) => {
                  event.stopPropagation();
                  setTip((current) =>
                    toggleTip(current, {
                      id,
                      label: `${sku.name} · ${row.label}`,
                      value: `Rs ${money(spend)}`,
                    }),
                  );
                }}
              />
            );
          }),
        )}
        {months.map((row, i) => (
          <text key={row.month} x={x(i)} y={height - 12} textAnchor="middle" fontSize="9" fill="#5a6f66">
            {row.label.split(" ")[0]}
          </text>
        ))}
      </svg>
      {!empty ? (
        <ul className="mt-2 flex flex-wrap gap-3 text-xs text-[var(--muted)]">
          {topSkuKeys.map((sku, i) => (
            <li key={sku.key} className="flex items-center gap-1">
              <span className="inline-block h-2 w-2 rounded-full" style={{ background: TREND_COLOURS[i % TREND_COLOURS.length] }} />
              {sku.name}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** Polar arc path for a pie slice (angles in radians from -π/2). */
function pieSlicePath(cx: number, cy: number, r: number, start: number, end: number): string {
  if (end - start >= 2 * Math.PI - 0.001) {
    return `M ${cx} ${cy - r} A ${r} ${r} 0 1 1 ${cx} ${cy + r} A ${r} ${r} 0 1 1 ${cx} ${cy - r} Z`;
  }
  const x1 = cx + r * Math.cos(start);
  const y1 = cy + r * Math.sin(start);
  const x2 = cx + r * Math.cos(end);
  const y2 = cy + r * Math.sin(end);
  const large = end - start > Math.PI ? 1 : 0;
  return `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2} Z`;
}

/** One month's SKU quantity mix — press a slice for qty / share / spend. */
function MonthSkuPieChart({ pie }: { pie: MonthSkuPie }) {
  const [tip, setTip] = useState<ChartTip | null>(null);
  const size = 160;
  const cx = size / 2;
  const cy = size / 2;
  const r = 68;
  const starts = pie.slices.map((_, i) =>
    pie.slices.slice(0, i).reduce((sum, s) => sum + Math.max(s.share * Math.PI * 2, 0.001), -Math.PI / 2),
  );

  if (pie.totalQty <= 0 || pie.slices.length === 0) {
    return (
      <div className="supplier-pie-card">
        <p className="supplier-pie-title">{pie.label}</p>
        <div className="supplier-chart-frame supplier-pie-frame">
          <svg viewBox={`0 0 ${size} ${size}`} className="supplier-chart-svg" role="img" aria-label={`${pie.label} no items`}>
            <circle cx={cx} cy={cy} r={r} fill="#eef3f0" stroke="#c5d4cc" />
            <text x={cx} y={cy + 4} textAnchor="middle" fontSize="11" fill="#5a6f66">
              No items
            </text>
          </svg>
        </div>
      </div>
    );
  }

  return (
    <div className="supplier-pie-card" onClick={() => setTip(null)}>
      <p className="supplier-pie-title">{pie.label}</p>
      <p className="supplier-pie-sub">Qty {pie.totalQty}</p>
      <div className="supplier-chart-frame supplier-pie-frame">
        <ChartValueTip tip={tip} onDismiss={() => setTip(null)} />
        <svg viewBox={`0 0 ${size} ${size}`} className="supplier-chart-svg" role="img" aria-label={`${pie.label} SKU mix`}>
          {pie.slices.map((slice, index) => {
            const sweep = slice.share * Math.PI * 2;
            const start = starts[index];
            const end = start + Math.max(sweep, 0.001);
            const id = `pie-${pie.month}-${slice.key}`;
            const active = tip?.id === id;
            return (
              <path
                key={slice.key}
                className="supplier-chart-hit"
                d={pieSlicePath(cx, cy, r, start, end)}
                fill={PIE_COLOURS[index % PIE_COLOURS.length]}
                opacity={active || !tip ? 1 : 0.55}
                stroke={active ? "#0f4a38" : "none"}
                strokeWidth={active ? 2 : 0}
                onClick={(event) => {
                  event.stopPropagation();
                  setTip((current) =>
                    toggleTip(current, {
                      id,
                      label: slice.name,
                      value: `${slice.quantity} items · ${(slice.share * 100).toFixed(0)}% · Rs ${money(slice.spend)}`,
                    }),
                  );
                }}
              />
            );
          })}
        </svg>
      </div>
      <ul className="supplier-pie-legend">
        {pie.slices.map((slice, index) => (
          <li key={slice.key}>
            <span className="supplier-pie-swatch" style={{ background: PIE_COLOURS[index % PIE_COLOURS.length] }} />
            <span className="truncate">{slice.name}</span>
            <span className="shrink-0 text-[var(--muted)]">{(slice.share * 100).toFixed(0)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Grid of one pie chart per month in the selected range. */
function MonthlySkuPies({ pies }: { pies: MonthSkuPie[] }) {
  if (pies.length === 0) {
    return <p className="text-sm text-[var(--muted)]">No months in this range.</p>;
  }
  return (
    <div className="supplier-pie-grid">
      {pies.map((pie) => (
        <MonthSkuPieChart key={pie.month} pie={pie} />
      ))}
    </div>
  );
}

/** Horizontal share bars — press a row for the exact spend. */
function SpendBySku({ rows }: { rows: SkuSpend[] }) {
  const [tip, setTip] = useState<ChartTip | null>(null);
  if (rows.length === 0) return <p className="text-sm text-[var(--muted)]">No verified lines in this range.</p>;
  return (
    <div onClick={() => setTip(null)}>
      <ChartValueTip tip={tip} onDismiss={() => setTip(null)} />
      <ul className="space-y-2">
        {rows.map((row) => (
          <li key={row.key}>
            <button
              type="button"
              className="supplier-bar-hit w-full text-left"
              onClick={(event) => {
                event.stopPropagation();
                setTip((current) =>
                  toggleTip(current, {
                    id: `hbar-${row.key}`,
                    label: row.name,
                    value: `Rs ${money(row.spend)} · ${(row.share * 100).toFixed(1)}% · qty ${row.quantity}`,
                  }),
                );
              }}
            >
              <div className="mb-1 flex justify-between gap-2 text-sm">
                <span className="truncate font-medium text-[var(--brand-deep)]">{row.name}</span>
                <span className="shrink-0 text-[var(--muted)]">
                  {money(row.spend)} · {(row.share * 100).toFixed(1)}%
                </span>
              </div>
              <div className="bar-track">
                <div
                  className="bar-fill"
                  style={{
                    width: `${Math.max(row.share * 100, row.spend > 0 ? 2 : 0)}%`,
                    opacity: tip?.id === `hbar-${row.key}` ? 1 : undefined,
                  }}
                />
              </div>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Invoice pipeline funnel. */
function Funnel({ metrics }: { metrics: SupplierDashboardMetrics }) {
  const max = Math.max(...metrics.funnel.map((s) => s.count), 1);
  return (
    <ul className="space-y-2">
      {metrics.funnel.map((step) => (
        <li key={step.key}>
          <div className="mb-1 flex justify-between text-sm">
            <span>{step.label}</span>
            <span className="font-medium text-[var(--brand-deep)]">{step.count}</span>
          </div>
          <div className="bar-track">
            <div className="bar-fill" style={{ width: `${(step.count / max) * 100}%`, opacity: 0.55 + step.share * 0.45 }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

/** Top SKUs table. */
function TopSkus({ rows }: { rows: SkuSpend[] }) {
  if (rows.length === 0) return <p className="text-sm text-[var(--muted)]">No SKUs in this range.</p>;
  return (
    <table className="entry-table">
      <thead>
        <tr>
          <th>SKU / item</th>
          <th className="text-right">Qty</th>
          <th className="text-right">Spend</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.key}>
            <td className="px-2 py-2">
              <div className="font-medium">{row.name}</div>
              {row.product_id ? <div className="text-xs text-[var(--muted)]">{row.product_id.slice(0, 8)}…</div> : null}
            </td>
            <td className="px-2 py-2 text-right">{row.quantity || "—"}</td>
            <td className="px-2 py-2 text-right">{money(row.spend)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Dashboard body for one loaded supplier profile. */
export function SupplierDashboard({ profile }: { profile: SupplierProfile }) {
  const bounds = useMemo(() => dataMonthBounds(profile), [profile]);
  const [range, setRange] = useState<MonthRange>(() => defaultRange(profile));
  const [pieRange, setPieRange] = useState<MonthRange>(() => defaultRange(profile));
  const metrics = useMemo(() => buildSupplierDashboard(profile, range), [profile, range]);
  const pieMetrics = useMemo(() => buildSupplierDashboard(profile, pieRange), [profile, pieRange]);
  const { kpis } = metrics;

  /** A new charts period also resets the pie charts' period to it. */
  function changeRange(next: MonthRange) {
    setRange(next);
    setPieRange(next);
  }

  return (
    <div className="space-y-4">
      <RangeControl range={range} onChange={changeRange} bounds={bounds} label="Charts period" />

      <div className="supplier-kpi-grid">
        <Kpi
          label="Verified spend"
          value={money(kpis.verifiedSpend)}
          hint={deltaText(kpis.verifiedSpendDeltaPct)}
        />
        <Kpi label="Outstanding" value={money(kpis.outstanding)} note="As of now" />
        <Kpi label="Distinct SKUs" value={String(kpis.distinctSkus)} />
        <Kpi label="Avg invoice" value={kpis.avgInvoice === null ? "—" : money(kpis.avgInvoice)} />
        <Kpi
          label="SKU match rate"
          value={kpis.matchRate === null ? "—" : `${kpis.matchRate.toFixed(0)}%`}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="bs-card">
          <p className="mb-2 text-sm font-semibold text-[var(--brand-deep)]">Monthly purchases &amp; paid</p>
          <p className="mb-3 text-xs text-[var(--muted)]">
            Press a bar for the exact purchases or paid amount that month.
          </p>
          <MonthlySpendChart points={metrics.monthlySpend} />
        </div>
        <div className="bs-card">
          <p className="mb-2 text-sm font-semibold text-[var(--brand-deep)]">Top SKU spend trend</p>
          <p className="mb-3 text-xs text-[var(--muted)]">Press a line point for the exact SKU amount.</p>
          <SkuTrendChart metrics={metrics} />
        </div>
      </div>

      <div className="bs-card">
        <p className="mb-2 text-sm font-semibold text-[var(--brand-deep)]">Items by SKU — pie per month</p>
        <p className="mb-3 text-xs text-[var(--muted)]">
          Press a slice for quantity, share, and spend. Filter months the same way as the column chart.
        </p>
        <RangeControl range={pieRange} onChange={setPieRange} bounds={bounds} label="Pie months" embedded />
        <div className="mt-3">
          <MonthlySkuPies pies={pieMetrics.monthlySkuPies} />
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="bs-card">
          <p className="mb-2 text-sm font-semibold text-[var(--brand-deep)]">Spend by SKU</p>
          <SpendBySku rows={metrics.spendBySku} />
        </div>
        <div className="bs-card">
          <p className="mb-2 text-sm font-semibold text-[var(--brand-deep)]">Invoice pipeline</p>
          <Funnel metrics={metrics} />
        </div>
        <div className="bs-card overflow-x-auto">
          <p className="mb-2 text-sm font-semibold text-[var(--brand-deep)]">Top SKUs</p>
          <TopSkus rows={metrics.topSkus} />
        </div>
      </div>

      <div className="bs-card overflow-x-auto">
        <p className="mb-2 text-sm font-semibold text-[var(--brand-deep)]">Credit &amp; payables (current)</p>
        <table className="entry-table">
          <tbody>
            <tr>
              <td className="px-2 py-2 text-[var(--muted)]">Usual credit days</td>
              <td className="px-2 py-2 font-medium">{profile.supplier.credit_days ?? "—"}</td>
            </tr>
            <tr>
              <td className="px-2 py-2 text-[var(--muted)]">Next due</td>
              <td className="px-2 py-2 font-medium">{profile.totals.next_due_date ?? "—"}</td>
            </tr>
            <tr>
              <td className="px-2 py-2 text-[var(--muted)]">Paid (confirmed)</td>
              <td className="px-2 py-2 font-medium">{money(profile.totals.paid_total)}</td>
            </tr>
            <tr>
              <td className="px-2 py-2 text-[var(--muted)]">Waiting confirmation</td>
              <td className="px-2 py-2 font-medium">
                {profile.totals.unconfirmed_count
                  ? `${money(profile.totals.unconfirmed_total)} (${profile.totals.unconfirmed_count})`
                  : "—"}
              </td>
            </tr>
            <tr>
              <td className="px-2 py-2 text-[var(--muted)]">Invoices / drafts (all time)</td>
              <td className="px-2 py-2 font-medium">
                {profile.totals.invoice_count} / {profile.totals.draft_count}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
