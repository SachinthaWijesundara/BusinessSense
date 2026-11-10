"use client";

/**
 * Main Dashboard charts: payable cash-out area, overdue meter, monthly purchases+paid bars,
 * and supplier / item pies. All figures come from GET /analytics/cash-overview.
 */

import Link from "next/link";
import { useMemo, useState, type ReactNode } from "react";
import { money } from "@/lib/money";
import type {
  CashOverview,
  MonthPurchasePaid,
  OverdueBalances,
  PayableDay,
  SpendSlice,
} from "@/lib/types";

const PURCHASE_COLOUR = "#1f6f54";
const PAID_COLOUR = "#c4a35a";
const AREA_COLOUR = "#2b6cb0";
const PIE_COLOURS = ["#1f6f54", "#2b6cb0", "#c4a35a", "#6b8f71", "#9b2c2c", "#5a6f66", "#8a9a92", "#4a5568"];

type Tip = { id: string; title: string; body: ReactNode };

function tipToggle(current: Tip | null, next: Tip): Tip | null {
  return current?.id === next.id ? null : next;
}

function ChartTip({ tip, onDismiss }: { tip: Tip | null; onDismiss: () => void }) {
  if (!tip) return null;
  return (
    <div className="supplier-chart-tip dash-chart-tip" role="status">
      <div className="min-w-0">
        <p className="supplier-chart-tip-label">{tip.title}</p>
        <div className="supplier-chart-tip-value">{tip.body}</div>
      </div>
      <button type="button" className="supplier-chart-tip-close" onClick={onDismiss} aria-label="Dismiss">
        ×
      </button>
    </div>
  );
}

function monthLabel(month: string): string {
  const [y, m] = month.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("en-GB", { month: "short", year: "numeric" });
}

function dayLabel(day: string): string {
  return new Date(`${day}T12:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

function pieSlicePath(cx: number, cy: number, r: number, start: number, end: number): string {
  const x1 = cx + r * Math.cos(start);
  const y1 = cy + r * Math.sin(start);
  const x2 = cx + r * Math.cos(end);
  const y2 = cy + r * Math.sin(end);
  const large = end - start > Math.PI ? 1 : 0;
  return `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2} Z`;
}

/** Payable cash expected out — SVG area chart; press a day for invoice detail. */
function PayableAreaChart({ series }: { series: PayableDay[] }) {
  const [tip, setTip] = useState<Tip | null>(null);
  const width = Math.max(420, series.length * 14);
  const height = 220;
  const pad = { top: 16, right: 12, bottom: 36, left: 56 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const max = Math.max(...series.map((d) => d.total_owed), 1);
  const x = (i: number) => pad.left + (series.length <= 1 ? innerW / 2 : (i / (series.length - 1)) * innerW);
  const y = (v: number) => pad.top + (1 - v / max) * innerH;
  const linePts = series.map((d, i) => `${x(i)},${y(d.total_owed)}`).join(" ");
  const areaPts = series.length
    ? `${x(0)},${pad.top + innerH} ${linePts} ${x(series.length - 1)},${pad.top + innerH}`
    : "";

  return (
    <div className="supplier-chart-frame" onClick={() => setTip(null)}>
      <ChartTip tip={tip} onDismiss={() => setTip(null)} />
      {series.every((d) => d.total_owed === 0) ? (
        <p className="supplier-chart-empty">Nothing due in this window.</p>
      ) : null}
      <svg viewBox={`0 0 ${width} ${height}`} className="supplier-chart-svg" role="img" aria-label="Cash expected out">
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
        {areaPts ? <polygon points={areaPts} fill={AREA_COLOUR} opacity="0.22" /> : null}
        {linePts ? <polyline fill="none" stroke={AREA_COLOUR} strokeWidth="2.5" points={linePts} /> : null}
        {series.map((day, i) => (
          <circle
            key={day.day}
            className="supplier-chart-hit"
            cx={x(i)}
            cy={y(day.total_owed)}
            r={tip?.id === `day-${day.day}` ? 6 : 4}
            fill={AREA_COLOUR}
            stroke="#fff"
            strokeWidth="1.5"
            onClick={(event) => {
              event.stopPropagation();
              setTip((current) =>
                tipToggle(current, {
                  id: `day-${day.day}`,
                  title: `${dayLabel(day.day)} · Rs ${money(day.total_owed)}`,
                  body:
                    day.invoices.length === 0 ? (
                      <span className="text-sm font-normal text-[var(--muted)]">No invoices this day.</span>
                    ) : (
                      <ul className="mt-1 space-y-1 text-left text-xs font-normal">
                        {day.invoices.map((inv) => (
                          <li key={inv.invoice_id}>
                            <Link
                              href={`/invoice-manager/${inv.invoice_id}`}
                              className="text-[var(--brand)] hover:underline"
                              onClick={(e) => e.stopPropagation()}
                            >
                              {inv.supplier_name ?? "Supplier"} · {inv.invoice_number ?? "invoice"} · Rs{" "}
                              {money(inv.still_owed)}
                              {inv.days_left < 0 ? ` · ${-inv.days_left}d overdue` : ""}
                            </Link>
                          </li>
                        ))}
                      </ul>
                    ),
                }),
              );
            }}
          />
        ))}
        {series
          .filter((_, i) => i === 0 || i === series.length - 1 || i % Math.ceil(series.length / 6) === 0)
          .map((day) => {
            const i = series.findIndex((d) => d.day === day.day);
            return (
              <text key={`lbl-${day.day}`} x={x(i)} y={height - 10} textAnchor="middle" fontSize="9" fill="#5a6f66">
                {dayLabel(day.day)}
              </text>
            );
          })}
      </svg>
    </div>
  );
}

/** Overdue meter: light red at the base → darker red as the fill rises. */
function OverdueMeter({ overdue, scaleMax }: { overdue: OverdueBalances; scaleMax: number }) {
  const [open, setOpen] = useState(false);
  const cap = Math.max(scaleMax, overdue.total_owed, 1);
  const fillPct =
    overdue.total_owed <= 0 ? 0 : Math.min(100, Math.max(10, (overdue.total_owed / cap) * 100));

  return (
    <div className="dash-overdue">
      <button type="button" className="dash-overdue-toggle" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <div className="dash-overdue-meter" aria-hidden>
          <div
            className={`dash-overdue-fill ${overdue.total_owed > 0 ? "has-overdue" : ""}`}
            style={{ height: `${fillPct}%` }}
          />
        </div>
        <div className="min-w-0 text-left">
          <p className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">Overdue balances</p>
          <p className="mt-1 text-xl font-semibold text-[var(--brand-deep)]">
            {overdue.total_owed > 0 ? money(overdue.total_owed) : "—"}
          </p>
          <p className="mt-1 text-xs text-[var(--muted)]">
            {overdue.invoice_count === 0
              ? "Nothing overdue"
              : `${overdue.invoice_count} invoice${overdue.invoice_count === 1 ? "" : "s"} · press for list`}
          </p>
        </div>
      </button>
      {open ? (
        <div className="dash-overdue-list">
          {overdue.invoices.length === 0 ? (
            <p className="text-sm text-[var(--muted)]">No overdue invoices.</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {overdue.invoices.map((inv) => (
                <li key={inv.invoice_id} className="flex flex-wrap items-baseline justify-between gap-2">
                  <Link href={`/invoice-manager/${inv.invoice_id}`} className="text-[var(--brand)] hover:underline">
                    {inv.supplier_name ?? "Supplier"} · {inv.invoice_number ?? "invoice"}
                  </Link>
                  <span className="text-[var(--muted)]">
                    {inv.days_overdue}d overdue · Rs {money(inv.still_owed)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}

/** Grouped monthly purchases + paid bars. */
function MonthlyPurchasePaidChart({ rows }: { rows: MonthPurchasePaid[] }) {
  const [tip, setTip] = useState<Tip | null>(null);
  const series = rows.length ? rows : [{ month: "—", purchases: 0, paid: 0 }];
  const max = Math.max(...series.flatMap((r) => [r.purchases, r.paid]), 1);
  const width = Math.max(360, series.length * 52);
  const height = 220;
  const pad = { top: 16, right: 12, bottom: 40, left: 56 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const gap = 8;
  const groupW = Math.max(16, innerW / series.length - gap);
  const barW = Math.max(6, (groupW - 2) / 2);

  return (
    <div className="supplier-chart-frame" onClick={() => setTip(null)}>
      <ChartTip tip={tip} onDismiss={() => setTip(null)} />
      <svg viewBox={`0 0 ${width} ${height}`} className="supplier-chart-svg" role="img" aria-label="Monthly purchases and paid">
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
        {series.map((point, index) => {
          const groupX = pad.left + index * (groupW + gap) + gap / 2;
          const purchH = (point.purchases / max) * innerH;
          const paidH = (point.paid / max) * innerH;
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
                  setTip((c) =>
                    tipToggle(c, {
                      id: `purch-${point.month}`,
                      title: `${monthLabel(point.month)} · Purchases`,
                      body: `Rs ${money(point.purchases)}`,
                    }),
                  );
                }}
              />
              <rect
                x={groupX}
                y={pad.top + innerH - purchH}
                width={barW}
                height={Math.max(purchH, point.purchases > 0 ? 3 : 0)}
                fill={PURCHASE_COLOUR}
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
                  setTip((c) =>
                    tipToggle(c, {
                      id: `paid-${point.month}`,
                      title: `${monthLabel(point.month)} · Paid`,
                      body: `Rs ${money(point.paid)}`,
                    }),
                  );
                }}
              />
              <rect
                x={groupX + barW + 2}
                y={pad.top + innerH - paidH}
                width={barW}
                height={Math.max(paidH, point.paid > 0 ? 3 : 0)}
                fill={PAID_COLOUR}
                rx="2"
                pointerEvents="none"
              />
              <text x={groupX + groupW / 2} y={height - 12} textAnchor="middle" fontSize="9" fill="#5a6f66">
                {monthLabel(point.month).split(" ")[0]}
              </text>
            </g>
          );
        })}
      </svg>
      <ul className="mt-2 flex flex-wrap gap-3 text-xs text-[var(--muted)]">
        <li className="flex items-center gap-1">
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: PURCHASE_COLOUR }} />
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

/** Spend pie with press-for-value slices. */
function SpendPie({ title, slices }: { title: string; slices: SpendSlice[] }) {
  const [tip, setTip] = useState<Tip | null>(null);
  const size = 180;
  const cx = size / 2;
  const cy = size / 2;
  const r = 72;
  let angle = -Math.PI / 2;

  if (slices.length === 0 || slices.every((s) => s.total <= 0)) {
    return (
      <div>
        <p className="mb-2 text-sm font-semibold text-[var(--brand-deep)]">{title}</p>
        <p className="text-sm text-[var(--muted)]">No spend in this period.</p>
      </div>
    );
  }

  return (
    <div onClick={() => setTip(null)}>
      <p className="mb-2 text-sm font-semibold text-[var(--brand-deep)]">{title}</p>
      <div className="supplier-chart-frame relative" style={{ minHeight: 180 }}>
        <ChartTip tip={tip} onDismiss={() => setTip(null)} />
        <svg viewBox={`0 0 ${size} ${size}`} className="supplier-chart-svg mx-auto max-w-[11rem]" role="img" aria-label={title}>
          {slices.map((slice, index) => {
            const sweep = slice.share * Math.PI * 2;
            const start = angle;
            const end = angle + Math.max(sweep, 0.001);
            angle = end;
            const id = `pie-${title}-${slice.key}`;
            return (
              <path
                key={slice.key}
                className="supplier-chart-hit"
                d={pieSlicePath(cx, cy, r, start, end)}
                fill={PIE_COLOURS[index % PIE_COLOURS.length]}
                opacity={!tip || tip.id === id ? 1 : 0.55}
                onClick={(event) => {
                  event.stopPropagation();
                  setTip((c) =>
                    tipToggle(c, {
                      id,
                      title: slice.name,
                      body: `Rs ${money(slice.total)} · ${(slice.share * 100).toFixed(0)}%`,
                    }),
                  );
                }}
              />
            );
          })}
        </svg>
      </div>
      <ul className="mt-2 space-y-1 text-xs">
        {slices.map((slice, index) => (
          <li key={slice.key} className="flex items-center gap-2">
            <span className="supplier-pie-swatch" style={{ background: PIE_COLOURS[index % PIE_COLOURS.length] }} />
            <span className="truncate">{slice.name}</span>
            <span className="ml-auto shrink-0 text-[var(--muted)]">{(slice.share * 100).toFixed(0)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const PAYABLE_PRESETS = [7, 14, 30, 60, 90] as const;
const MONTH_PRESETS: { label: string; months: number }[] = [
  { label: "This month", months: 1 },
  { label: "Last 2 mo", months: 2 },
  { label: "3 mo", months: 3 },
  { label: "6 mo", months: 6 },
  { label: "12 mo", months: 12 },
];

type Props = {
  data: CashOverview;
  payableDays: number;
  months: number;
  onPayableDays: (days: number) => void;
  onMonths: (months: number) => void;
  loading?: boolean;
};

/** Chart block for the main Dashboard (cash-overview payload). */
export function DashboardCharts({ data, payableDays, months, onPayableDays, onMonths, loading }: Props) {
  const monthly = useMemo(() => data.monthly, [data.monthly]);
  const overdueScale = useMemo(() => {
    const upcoming = data.payable_series.reduce((sum, day) => sum + day.total_owed, 0);
    return Math.max(upcoming, data.overdue.total_owed, 1);
  }, [data.payable_series, data.overdue.total_owed]);

  return (
    <div className={`space-y-4 ${loading ? "opacity-70" : ""}`}>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(14rem,0.8fr)]">
        <div className="bs-card">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="text-sm font-semibold text-[var(--brand-deep)]">Cash expected out</p>
              <p className="text-xs text-[var(--muted)]">
                From invoice due dates (credit). Press a point for supplier and invoice.
              </p>
            </div>
            <div className="flex flex-wrap gap-1">
              {PAYABLE_PRESETS.map((days) => (
                <button
                  key={days}
                  type="button"
                  className={`btn !py-1 !px-2 text-xs ${payableDays === days ? "btn-primary" : "btn-secondary"}`}
                  onClick={() => onPayableDays(days)}
                >
                  {days}d
                </button>
              ))}
            </div>
          </div>
          <PayableAreaChart series={data.payable_series} />
        </div>
        <div className="bs-card">
          <OverdueMeter overdue={data.overdue} scaleMax={overdueScale} />
        </div>
      </div>

      <div className="bs-card">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-sm font-semibold text-[var(--brand-deep)]">Monthly purchases &amp; paid</p>
            <p className="text-xs text-[var(--muted)]">Paid is amount_paid on invoices dated that month. Press a bar for the exact figure.</p>
          </div>
          <div className="flex flex-wrap gap-1">
            {MONTH_PRESETS.map((preset) => (
              <button
                key={preset.months}
                type="button"
                className={`btn !py-1 !px-2 text-xs ${months === preset.months ? "btn-primary" : "btn-secondary"}`}
                onClick={() => onMonths(preset.months)}
              >
                {preset.label}
              </button>
            ))}
          </div>
        </div>
        <MonthlyPurchasePaidChart rows={monthly} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="bs-card">
          <SpendPie title="Purchases by supplier" slices={data.spend_by_supplier} />
        </div>
        <div className="bs-card">
          <SpendPie title="Purchases by item" slices={data.spend_by_item} />
        </div>
      </div>
    </div>
  );
}
