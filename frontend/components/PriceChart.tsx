"use client";

/**
 * Price history chart for one product (Purchase Analytics): the unit price paid on each purchase,
 * Model 1's expected range at that time as a shaded band, each purchase coloured by its check
 * (green inside the range, red outside it, grey without enough history) and the next expected
 * price at today. Drawn as plain SVG from the backend's numbers; no calculations here.
 */

import { money } from "@/lib/money";
import type { ExpectedPrice, PricePoint } from "@/lib/types";

const WIDTH = 760;
const HEIGHT = 260;
const PAD = { left: 74, right: 24, top: 16, bottom: 34 };
const COLOURS = { inside: "#2f855a", above: "#c53030", below: "#c53030", no_range: "#8a9a92" } as const;

/** Milliseconds of a YYYY-MM-DD date. */
function time(day: string): number {
  return new Date(`${day}T00:00:00`).getTime();
}

/** A short date label: "Mar 2026". */
function monthLabel(ms: number): string {
  return new Date(ms).toLocaleDateString("en-GB", { month: "short", year: "numeric" });
}

/** What a dot's tooltip says. */
function describe(point: PricePoint): string {
  const range = point.low !== null && point.high !== null ? ` (expected Rs ${money(point.low)} to Rs ${money(point.high)})` : "";
  const status =
    point.status === "above" || point.status === "below"
      ? `${Math.abs(point.difference_pct ?? 0)}% ${point.status} the range`
      : point.status === "inside"
        ? "inside the range"
        : "not enough earlier purchases to check";
  return `${point.day} · Rs ${money(point.unit_price)} · ${status}${range} · invoice ${point.invoice_number ?? "—"}`;
}

/** The chart, its axes and a small legend. */
export function PriceChart({ points, next, today }: { points: PricePoint[]; next: ExpectedPrice | null; today: string }) {
  if (points.length === 0) return <p className="text-sm text-[var(--muted)]">No purchases to draw.</p>;
  const start = time(points[0].day);
  const end = Math.max(time(today), time(points[points.length - 1].day)) + 1;
  const values = points.flatMap((p) => [p.unit_price, p.low, p.high]).concat(next ? [next.expected, next.low, next.high] : []);
  const known = values.filter((v): v is number => v !== null);
  const lowest = Math.min(...known);
  const highest = Math.max(...known);
  const spread = Math.max(highest - lowest, highest * 0.05, 1);
  const bottom = Math.max(0, lowest - spread * 0.1);
  const top = highest + spread * 0.1;
  const x = (day: string | number) =>
    PAD.left + ((typeof day === "string" ? time(day) : day) - start) / (end - start || 1) * (WIDTH - PAD.left - PAD.right);
  const y = (value: number) => PAD.top + (1 - (value - bottom) / (top - bottom)) * (HEIGHT - PAD.top - PAD.bottom);

  // The expected range as bands over runs of purchases that had one.
  const bands: PricePoint[][] = [];
  for (const point of points) {
    if (point.low === null || point.high === null) continue;
    const run = bands[bands.length - 1];
    const previous = run?.[run.length - 1];
    if (run && previous && points.indexOf(previous) === points.indexOf(point) - 1) run.push(point);
    else bands.push([point]);
  }
  const ticks = [0, 1, 2, 3].map((i) => bottom + ((top - bottom) * i) / 3);
  const last = points[points.length - 1];

  return (
    <div>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="h-auto w-full" role="img" aria-label="Unit price over time with the expected range">
        {ticks.map((tick) => (
          <g key={tick}>
            <line x1={PAD.left} x2={WIDTH - PAD.right} y1={y(tick)} y2={y(tick)} stroke="#e3ebe6" />
            <text x={PAD.left - 8} y={y(tick) + 4} textAnchor="end" fontSize="11" fill="#5a6f66">
              {money(tick)}
            </text>
          </g>
        ))}
        {[start, (start + end) / 2, end].map((ms, i) => (
          <text key={i} x={x(ms)} y={HEIGHT - 10} textAnchor={i === 0 ? "start" : i === 2 ? "end" : "middle"} fontSize="11" fill="#5a6f66">
            {monthLabel(ms)}
          </text>
        ))}
        {bands.map((run, i) =>
          run.length === 1 ? (
            <line key={i} x1={x(run[0].day)} x2={x(run[0].day)} y1={y(run[0].high as number)} y2={y(run[0].low as number)}
              stroke="#cfe3d8" strokeWidth="8" />
          ) : (
            <polygon key={i} fill="#dcece3"
              points={[...run.map((p) => `${x(p.day)},${y(p.high as number)}`), ...[...run].reverse().map((p) => `${x(p.day)},${y(p.low as number)}`)].join(" ")} />
          ),
        )}
        <polyline fill="none" stroke="var(--brand)" strokeWidth="1.6" points={points.map((p) => `${x(p.day)},${y(p.unit_price)}`).join(" ")} />
        {next && next.expected !== null ? (
          <g>
            <line x1={x(last.day)} x2={x(today)} y1={y(last.unit_price)} y2={y(next.expected)} stroke="var(--brand)" strokeDasharray="4 4" />
            {next.low !== null && next.high !== null ? (
              <line x1={x(today)} x2={x(today)} y1={y(next.high)} y2={y(next.low)} stroke="#9cc7b0" strokeWidth="8" />
            ) : null}
            <rect x={x(today) - 5} y={y(next.expected) - 5} width="10" height="10" transform={`rotate(45 ${x(today)} ${y(next.expected)})`}
              fill="var(--brand-deep)">
              <title>{`Next purchase: Rs ${money(next.expected)}${next.low !== null && next.high !== null ? ` (Rs ${money(next.low)} to Rs ${money(next.high)})` : ""}${next.is_baseline ? " · baseline" : ""}`}</title>
            </rect>
          </g>
        ) : null}
        {points.map((point) => (
          <circle key={`${point.invoice_id}-${point.day}`} cx={x(point.day)} cy={y(point.unit_price)}
            r={point.status === "above" || point.status === "below" ? 5 : 3.5} fill={COLOURS[point.status]} stroke="#fff" strokeWidth="1">
            <title>{describe(point)}</title>
          </circle>
        ))}
      </svg>
      <div className="mt-1 flex flex-wrap gap-4 text-xs text-[var(--muted)]">
        <span><span className="mr-1 inline-block h-2 w-3 rounded-sm bg-[#dcece3] align-middle" />expected range</span>
        <span><span className="mr-1 inline-block h-2 w-2 rounded-full bg-[#2f855a] align-middle" />inside the range</span>
        <span><span className="mr-1 inline-block h-2 w-2 rounded-full bg-[#c53030] align-middle" />outside: check the unit price and pack size</span>
        <span><span className="mr-1 inline-block h-2 w-2 rounded-full bg-[#8a9a92] align-middle" />too little history to check</span>
        <span><span className="mr-1 inline-block h-2 w-2 rotate-45 bg-[var(--brand-deep)] align-middle" />next purchase (today)</span>
      </div>
    </div>
  );
}
