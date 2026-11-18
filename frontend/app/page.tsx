"use client";

/**
 * Dashboard screen (proposal): invoices waiting for review, supplier payments due soon, payments
 * waiting for confirmation, plus cash-overview charts (payable area, overdue, monthly purchases+paid,
 * supplier/item pies). Attention figures come from GET /dashboard; charts from GET /analytics/cash-overview.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { DashboardCharts } from "@/components/DashboardCharts";
import { errorMessage, getCashOverview, getDashboard } from "@/lib/api";
import { EmptyState } from "@/components/ui/EmptyState";
import { IconAnalytics, IconCheck, IconClock } from "@/components/ui/Icons";
import { PageHeader } from "@/components/ui/PageHeader";
import { PageSkeleton, SkeletonCard } from "@/components/ui/Skeleton";
import { StatCard } from "@/components/ui/StatCard";
import { money } from "@/lib/money";
import type { CashOverview, Dashboard } from "@/lib/types";

/** The dashboard: what needs attention first, then cash / purchase charts. */
export default function DashboardPage() {
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [cash, setCash] = useState<CashOverview | null>(null);
  const [error, setError] = useState("");
  const [cashError, setCashError] = useState("");
  const [payableDays, setPayableDays] = useState(30);
  const [months, setMonths] = useState(3);
  const [cashLoadedFor, setCashLoadedFor] = useState("");
  const cashKey = `${payableDays}:${months}`;
  const cashLoading = cashLoadedFor !== cashKey;

  useEffect(() => {
    getDashboard()
      .then(setDashboard)
      .catch((err) => setError(errorMessage(err)));
  }, []);

  useEffect(() => {
    let cancelled = false;
    getCashOverview({ payable_days: payableDays, months })
      .then((overview) => {
        if (!cancelled) {
          setCash(overview);
          setCashError("");
        }
      })
      .catch((err) => {
        if (!cancelled) setCashError(errorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setCashLoadedFor(`${payableDays}:${months}`);
      });
    return () => {
      cancelled = true;
    };
  }, [payableDays, months]);

  if (error) {
    return (
      <div className="space-y-4">
        <PageHeader title="Dashboard" />
        <p className="banner-error">{error}</p>
      </div>
    );
  }
  if (!dashboard) return <PageSkeleton />;

  const overdue = dashboard.payments_due.filter((due) => due.days_left < 0);
  const overdueCount = overdue.length;
  const overdueOwed = overdue.reduce((sum, due) => sum + (due.still_owed ?? 0), 0);
  const owedTotal = dashboard.payments_due.reduce((sum, due) => sum + (due.still_owed ?? 0), 0);

  return (
    <div className="space-y-5">
      <PageHeader
        title="Dashboard"
        description="Here is what needs your attention. Verified invoices only, so every figure traces back to a checked bill."
        actions={
          <Link href="/invoice-manager" className="btn btn-primary">
            + Add invoice
          </Link>
        }
      />

      <div className="bs-card bs-attention" aria-label="What needs your attention">
        <Link href="/invoice-manager?status=draft" className="bs-attention-item">
          <span className={`bs-dot ${dashboard.review_queue > 0 ? "tone-warn" : "tone-good"}`} aria-hidden="true" />
          <span>
            <span className="block font-semibold">
              {dashboard.review_queue} {dashboard.review_queue === 1 ? "invoice" : "invoices"} to review
            </span>
            <span className="block text-sm text-[var(--muted)]">
              {dashboard.review_queue > 0 ? "Drafts waiting for you" : "All caught up"}
            </span>
          </span>
        </Link>
        <div className="bs-attention-item">
          <span className={`bs-dot ${overdueCount > 0 ? "tone-bad" : "tone-good"}`} aria-hidden="true" />
          <span>
            <span className="block font-semibold">
              {overdueCount} {overdueCount === 1 ? "bill" : "bills"} overdue
            </span>
            <span className="block text-sm text-[var(--muted)]">
              {overdueCount > 0 ? `Rs ${money(overdueOwed)} still owed` : "Nothing overdue"}
            </span>
          </span>
        </div>
        <Link href="/supplier-profile" className="bs-attention-item">
          <span className={`bs-dot ${dashboard.unconfirmed_payments > 0 ? "tone-warn" : "tone-good"}`} aria-hidden="true" />
          <span>
            <span className="block font-semibold">
              {dashboard.unconfirmed_payments} {dashboard.unconfirmed_payments === 1 ? "payment" : "payments"} to confirm
            </span>
            <span className="block text-sm text-[var(--muted)]">
              {dashboard.unconfirmed_payments > 0 ? "Not counted as paid yet" : "Nothing waiting"}
            </span>
          </span>
        </Link>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <StatCard
          label="Bills due (next 30 days)"
          value={`Rs ${money(owedTotal)}`}
          icon={<IconClock />}
          tone={overdueCount > 0 ? "bad" : "default"}
          note={`${dashboard.payments_due.length} ${dashboard.payments_due.length === 1 ? "bill" : "bills"}${overdueCount > 0 ? `, ${overdueCount} overdue` : ""}`}
        />
        <StatCard
          label="Cash to set aside"
          value={dashboard.forecast ? `Rs ${money(dashboard.forecast.reserve)}` : "—"}
          icon={<IconAnalytics />}
          tone="info"
          note={dashboard.forecast ? "Bills due plus expected purchases" : dashboard.forecast_note}
          cta="Open the Cash Planner"
          href="/cash-planner"
        />
        <StatCard
          label="Verified invoices"
          value={String(dashboard.verified_invoices)}
          icon={<IconCheck />}
          note="Every figure traces to a checked bill"
        />
      </div>

      {dashboard.payment_reminders.length > 0 ? (
        <div className="banner-partial">
          <p className="font-semibold">
            Reminder: {dashboard.payment_reminders.length} payment(s) have waited more than two days for your confirmation.
          </p>
          <ul className="mt-1 list-disc pl-5 text-sm">
            {dashboard.payment_reminders.map((payment) => (
              <li key={payment.id}>
                {payment.supplier_name ?? payment.supplier_name_raw ?? "Supplier not picked"} · Rs {money(payment.amount)}
                {payment.receipt_number ? ` · receipt ${payment.receipt_number}` : ""}
                {payment.paid_on ? ` · paid ${payment.paid_on}` : ""}
              </li>
            ))}
          </ul>
          <Link href="/supplier-profile" className="mt-1 inline-block text-sm font-medium underline">
            Check and confirm them on Supplier Profile
          </Link>
          <p className="mt-1 text-xs">They stay unconfirmed, and are not counted as paid, until you confirm them.</p>
        </div>
      ) : null}

      {cashError ? <p className="banner-error">{cashError}</p> : null}
      {cash ? (
        <DashboardCharts
          data={cash}
          payableDays={payableDays}
          months={months}
          onPayableDays={setPayableDays}
          onMonths={setMonths}
          loading={cashLoading}
        />
      ) : (
        <SkeletonCard lines={6} height={320} />
      )}

      <div className="bs-card overflow-x-auto">
        <p className="bs-card-title mb-3">Supplier payments due soon</p>
        {dashboard.payments_due.length === 0 ? (
          <EmptyState title="Nothing due in the next 30 days" icon={<IconCheck />}>Supplier bills that fall due will be listed here.</EmptyState>
        ) : (
          <table className="entry-table">
            <thead>
              <tr>
                <th>Due</th>
                <th>Supplier</th>
                <th>Invoice #</th>
                <th className="text-right">Total</th>
                <th className="text-right">Still owed</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {dashboard.payments_due.map((due) => (
                <tr key={due.invoice_id}>
                  <td className="px-2 py-2">
                    {due.due_date}{" "}
                    <span className={`badge ${due.days_left < 0 ? "badge-bad" : "badge-warn"}`}>
                      {due.days_left < 0 ? `${-due.days_left} days overdue` : `in ${due.days_left} days`}
                    </span>
                  </td>
                  <td className="px-2 py-2">{due.supplier_name}</td>
                  <td className="px-2 py-2">{due.invoice_number ?? "—"}</td>
                  <td className="px-2 py-2 text-right">{money(due.total)}</td>
                  <td className="px-2 py-2 text-right">{money(due.still_owed)}</td>
                  <td className="px-2 py-2 text-right">
                    <Link href={`/invoice-manager/${due.invoice_id}`} className="text-sm text-[var(--brand)] hover:underline">
                      Open
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="bs-card">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-sm font-semibold text-[var(--brand-deep)]">Next 30 days: purchasing forecast</p>
          <Link href="/cash-planner" className="text-sm text-[var(--brand)] hover:underline">
            Open the Cash Planner
          </Link>
        </div>
        {dashboard.forecast ? (
          <>
            <div className="mt-3 grid gap-3 sm:grid-cols-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
                  Expected purchases{dashboard.forecast.is_baseline ? " (baseline)" : ""}
                </p>
                <p className="mt-1 text-xl font-semibold text-[var(--brand-deep)]">Rs {money(dashboard.forecast.expected)}</p>
                {dashboard.forecast.low !== null && dashboard.forecast.high !== null ? (
                  <p className="text-xs text-[var(--muted)]">
                    likely Rs {money(dashboard.forecast.low)} – {money(dashboard.forecast.high)}
                  </p>
                ) : null}
              </div>
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
                  Baseline ({dashboard.forecast.baseline_name})
                </p>
                <p className="mt-1 text-xl font-semibold text-[var(--brand-deep)]">Rs {money(dashboard.forecast.baseline)}</p>
              </div>
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">Supplier bills due then</p>
                <p className="mt-1 text-xl font-semibold text-[var(--brand-deep)]">Rs {money(dashboard.forecast.bills_due)}</p>
                <p className="text-xs text-[var(--muted)]">Cash to set aside: Rs {money(dashboard.forecast.reserve)}</p>
              </div>
            </div>
            <p className="mt-2 text-xs text-[var(--muted)]">{dashboard.forecast.model_note}</p>
          </>
        ) : (
          <p className="mt-1 text-sm text-[var(--muted)]">{dashboard.forecast_note}</p>
        )}
      </div>
    </div>
  );
}
