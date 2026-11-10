"use client";

/**
 * Purchasing Cash Planner screen (proposal): a dated list of supplier obligations, the Model 2
 * forecast with its baseline, and the recommended purchasing cash reserve for the next 14, 30, 60
 * or 90 days. Every figure comes from the backend (GET /analytics/cash-plan), where it is worked
 * out by plain rules that can be checked by hand. Unconfirmed payments are shown as an assumed
 * scenario only; opening cash, inflows and other expenses are not entered.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { errorMessage, getCashPlan } from "@/lib/api";
import { money } from "@/lib/money";
import type { CashPlan } from "@/lib/types";

const PERIODS = [14, 30, 60, 90];

/** One headline figure. */
function Figure({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="bs-card !p-4">
      <p className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-[var(--brand-deep)]">{value}</p>
      {note ? <p className="mt-1 text-xs text-[var(--muted)]">{note}</p> : null}
    </div>
  );
}

/** The whole planner. */
export default function CashPlannerPage() {
  const [days, setDays] = useState(30);
  const [result, setResult] = useState<{ days: number; plan: CashPlan | null; error: string } | null>(null);

  // Load the plan for the chosen period.
  useEffect(() => {
    let cancelled = false;
    getCashPlan(days)
      .then((plan) => {
        if (!cancelled) setResult({ days, plan, error: "" });
      })
      .catch((err) => {
        if (!cancelled) setResult({ days, plan: null, error: errorMessage(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [days]);

  const plan = result?.plan ?? null;
  const loading = result?.days !== days;
  const largestDay = Math.max(1, ...(plan?.due_by_day ?? []).map((d) => d.amount));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-[var(--brand-deep)]">Purchasing Cash Planner</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Supplier bills falling due, expected purchases, and the cash to set aside for them.
          </p>
        </div>
        <div className="flex gap-1" role="group" aria-label="Planning period">
          {PERIODS.map((period) => (
            <button
              key={period}
              type="button"
              className={`btn ${period === days ? "btn-primary" : "btn-secondary"}`}
              aria-pressed={period === days}
              onClick={() => setDays(period)}
            >
              {period} days
            </button>
          ))}
        </div>
      </div>

      {result?.error ? <p className="banner-error">{result.error}</p> : null}
      {!plan ? <p className="text-[var(--muted)]">Loading the plan…</p> : null}

      {plan ? (
        <div className={`space-y-5 ${loading ? "opacity-60" : ""}`}>
          <div className="bs-card">
            <p className="text-sm font-semibold text-[var(--brand-deep)]">Model 2 · purchase spend</p>
            <p className="mt-1 text-sm">{plan.model.note}</p>
            {plan.model.trained ? (
              <p className="mt-1 text-xs text-[var(--muted)]">
                {plan.model.version}
                {plan.model.trained_at ? ` · trained ${new Date(plan.model.trained_at).toLocaleDateString("en-GB")}` : ""}
                {plan.model.range_low_pct !== null && plan.model.range_high_pct !== null
                  ? ` · likely range ${plan.model.range_low_pct.toFixed(1)}% to +${plan.model.range_high_pct.toFixed(1)}% around the forecast`
                  : ""}
              </p>
            ) : null}
          </div>

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Figure
              label={`Cash to set aside (${plan.days} days)`}
              value={`Rs ${money(plan.reserve)}`}
              note={plan.reserve_high !== null ? `Up to Rs ${money(plan.reserve_high)} if purchases run high` : undefined}
            />
            <Figure
              label="Supplier bills due"
              value={`Rs ${money(plan.bills_due)}`}
              note={plan.overdue > 0 ? `Rs ${money(plan.overdue)} of it is already overdue` : "Nothing overdue"}
            />
            <Figure
              label="New purchases falling due"
              value={`Rs ${money(plan.new_purchases_due.expected)}`}
              note={`${plan.new_purchases_due.share_due_pct}% of the purchases expected in the period`}
            />
            <Figure
              label={`Expected purchases${plan.period_spend.is_baseline ? " (baseline)" : ""}`}
              value={`Rs ${money(plan.period_spend.expected)}`}
              note={`Baseline (${plan.period_spend.baseline_name}): Rs ${money(plan.period_spend.baseline)}`}
            />
          </div>

          <div className="bs-card space-y-1 text-sm">
            <p className="font-semibold text-[var(--brand-deep)]">How the cash to set aside is worked out</p>
            <p>
              Bills still owed that fall due by {plan.period_end} (overdue ones included): <strong>Rs {money(plan.bills_due)}</strong>
            </p>
            <p>
              + new purchases expected in the period that fall due inside it: <strong>Rs {money(plan.new_purchases_due.expected)}</strong>
            </p>
            <p className="text-xs text-[var(--muted)]">{plan.new_purchases_due.note}</p>
            <p>
              = <strong>Rs {money(plan.reserve)}</strong>
              {plan.reserve_baseline !== plan.reserve ? ` (with the baseline forecast: Rs ${money(plan.reserve_baseline)})` : ""}
            </p>
            {plan.unconfirmed_total > 0 ? (
              <p className="banner-partial mt-2">
                Rs {money(plan.unconfirmed_total)} of these bills has payments waiting for your confirmation. If they are real,
                the cash to set aside would be Rs {money(plan.reserve_if_unconfirmed_are_paid)}. They stay unconfirmed until you
                confirm them on{" "}
                <Link href="/supplier-profile" className="underline">
                  Supplier Profile
                </Link>
                .
              </p>
            ) : null}
            {plan.notes.map((note) => (
              <p key={note} className="text-xs text-[var(--muted)]">
                {note}
              </p>
            ))}
            <p className="text-xs text-[var(--muted)]">{plan.cash_credit_split.note}</p>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <div className="bs-card">
              <p className="mb-3 text-sm font-semibold text-[var(--brand-deep)]">Bills falling due, by date</p>
              {plan.due_by_day.length === 0 ? (
                <p className="text-sm text-[var(--muted)]">No supplier bills fall due in this period.</p>
              ) : (
                <ul className="space-y-2">
                  {plan.due_by_day.map((day) => (
                    <li key={day.day} className="grid grid-cols-[6rem_1fr_7rem_7rem] items-center gap-3 text-sm">
                      <span>{day.day === plan.today ? "Today*" : day.day}</span>
                      <div className="bar-track">
                        <div className="bar-fill" style={{ width: `${(day.amount / largestDay) * 100}%` }} />
                      </div>
                      <span className="text-right">{money(day.amount)}</span>
                      <span className="text-right text-xs text-[var(--muted)]">total {money(day.cumulative)}</span>
                    </li>
                  ))}
                </ul>
              )}
              {plan.overdue > 0 ? (
                <p className="mt-2 text-xs text-[var(--muted)]">* Today includes the bills that are already overdue.</p>
              ) : null}
            </div>

            <div className="bs-card overflow-x-auto">
              <p className="mb-3 text-sm font-semibold text-[var(--brand-deep)]">Expected purchases by month</p>
              <table className="entry-table">
                <thead>
                  <tr>
                    <th>Month</th>
                    <th className="text-right">Expected</th>
                    <th className="text-right">Likely range</th>
                    <th className="text-right">Baseline</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.months.map((month) => (
                    <tr key={month.month}>
                      <td className="px-2 py-2">{month.month}</td>
                      <td className="px-2 py-2 text-right">
                        {money(month.expected)}
                        {month.is_baseline ? <span className="badge ml-1">baseline</span> : null}
                      </td>
                      <td className="px-2 py-2 text-right">
                        {month.low !== null && month.high !== null ? `${money(month.low)} – ${money(month.high)}` : "—"}
                      </td>
                      <td className="px-2 py-2 text-right">
                        {money(month.baseline)} <span className="text-xs text-[var(--muted)]">({month.baseline_name})</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-2 text-xs text-[var(--muted)]">
                Already bought this month: Rs {money(plan.period_spend.spent_so_far_this_month)}.
              </p>
            </div>
          </div>

          <div className="bs-card overflow-x-auto">
            <p className="mb-2 text-sm font-semibold text-[var(--brand-deep)]">Supplier bills to pay by {plan.period_end}</p>
            {plan.obligations.length === 0 ? (
              <p className="text-sm text-[var(--muted)]">Nothing to pay in this period.</p>
            ) : (
              <table className="entry-table">
                <thead>
                  <tr>
                    <th>Due</th>
                    <th>Supplier</th>
                    <th>Invoice #</th>
                    <th className="text-right">Still owed</th>
                    <th className="text-right">Payment waiting</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.obligations.map((bill) => (
                    <tr key={bill.invoice_id}>
                      <td className="px-2 py-2">
                        {bill.due_date}{" "}
                        {bill.days_left !== null ? (
                          <span className={`badge ${bill.days_left < 0 ? "badge-bad" : "badge-warn"}`}>
                            {bill.days_left < 0 ? `${-bill.days_left} days overdue` : bill.days_left === 0 ? "today" : `in ${bill.days_left} days`}
                          </span>
                        ) : null}
                      </td>
                      <td className="px-2 py-2">{bill.supplier_name ?? "—"}</td>
                      <td className="px-2 py-2">
                        <Link href={`/invoice-manager/${bill.invoice_id}`} className="text-[var(--brand)] hover:underline">
                          {bill.invoice_number ?? "open"}
                        </Link>
                      </td>
                      <td className="px-2 py-2 text-right">{money(bill.still_owed)}</td>
                      <td className="px-2 py-2 text-right">{bill.unconfirmed_paid > 0 ? money(bill.unconfirmed_paid) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {plan.no_due_date.length > 0 ? (
              <p className="mt-3 text-sm text-[var(--muted)]">
                {plan.no_due_date.length} bill{plan.no_due_date.length === 1 ? "" : "s"} (Rs{" "}
                {money(plan.no_due_date.reduce((sum, bill) => sum + bill.still_owed, 0))}) cannot be dated because the supplier has
                no credit days. Set them on{" "}
                <Link href="/supplier-profile" className="underline">
                  Supplier Profile
                </Link>
                .
              </p>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
