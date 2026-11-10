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
import { money } from "@/lib/money";
import type { CashOverview, Dashboard } from "@/lib/types";

/** One headline number with its label. */
function Stat({ label, value, href }: { label: string; value: string; href?: string }) {
  const body = (
    <div className="bs-card !p-4">
      <p className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-[var(--brand-deep)]">{value}</p>
    </div>
  );
  return href ? <Link href={href}>{body}</Link> : body;
}

/** The dashboard: what needs attention first, then cash / purchase charts. */
export default function DashboardPage() {
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [cash, setCash] = useState<CashOverview | null>(null);
  const [error, setError] = useState("");
  const [cashError, setCashError] = useState("");
  const [payableDays, setPayableDays] = useState(30);
  const [months, setMonths] = useState(3);
  const [cashLoading, setCashLoading] = useState(false);

  useEffect(() => {
    getDashboard()
      .then(setDashboard)
      .catch((err) => setError(errorMessage(err)));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setCashLoading(true);
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
        if (!cancelled) setCashLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [payableDays, months]);

  if (error) {
    return (
      <div>
        <h1 className="text-2xl font-semibold text-[var(--brand-deep)]">Dashboard</h1>
        <p className="banner-error mt-4">{error}</p>
      </div>
    );
  }
  if (!dashboard) return <p className="text-[var(--muted)]">Loading dashboard…</p>;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold text-[var(--brand-deep)]">Dashboard</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">Verified invoices only, so every figure traces back to a checked bill.</p>
      </div>

      <div className="grid gap-4 sm:grid-cols-4">
        <Stat label="Waiting for review" value={String(dashboard.review_queue)} href="/invoice-manager?status=draft" />
        <Stat label="Verified invoices" value={String(dashboard.verified_invoices)} />
        <Stat label="Payments due (30 days)" value={String(dashboard.payments_due.length)} />
        <Stat label="Payments to confirm" value={String(dashboard.unconfirmed_payments)} href="/supplier-profile" />
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
        <p className="text-sm text-[var(--muted)]">Loading cash charts…</p>
      )}

      <div className="bs-card overflow-x-auto">
        <p className="mb-2 text-sm font-semibold text-[var(--brand-deep)]">Supplier payments due soon</p>
        {dashboard.payments_due.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">Nothing due in the next 30 days.</p>
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
        <p className="text-sm font-semibold text-[var(--brand-deep)]">Next-period purchasing forecast</p>
        <p className="mt-1 text-sm text-[var(--muted)]">{dashboard.forecast_note}</p>
      </div>
    </div>
  );
}
