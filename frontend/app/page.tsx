"use client";

/**
 * Dashboard screen (proposal): invoices waiting for review, supplier payments due soon, monthly
 * purchases, supplier spending and the forecast slot. Every number comes from GET /dashboard.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { errorMessage, getDashboard } from "@/lib/api";
import { money } from "@/lib/money";
import type { Dashboard } from "@/lib/types";

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

/** The dashboard: what needs attention first. */
export default function DashboardPage() {
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [error, setError] = useState("");

  // Load the dashboard figures from the backend.
  useEffect(() => {
    getDashboard()
      .then(setDashboard)
      .catch((err) => setError(errorMessage(err)));
  }, []);

  if (error) {
    return (
      <div>
        <h1 className="text-2xl font-semibold text-[var(--brand-deep)]">Dashboard</h1>
        <p className="banner-error mt-4">{error}</p>
      </div>
    );
  }
  if (!dashboard) return <p className="text-[var(--muted)]">Loading dashboard…</p>;

  // Bar lengths are only for drawing: each month relative to the largest month.
  const largestMonth = Math.max(1, ...dashboard.monthly_purchases.map((month) => month.total));

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold text-[var(--brand-deep)]">Dashboard</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">Verified invoices only, so every figure traces back to a checked bill.</p>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Waiting for review" value={String(dashboard.review_queue)} href="/invoice-manager?status=draft" />
        <Stat label="Verified invoices" value={String(dashboard.verified_invoices)} />
        <Stat label="Payments due (30 days)" value={String(dashboard.payments_due.length)} />
      </div>

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

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="bs-card">
          <p className="mb-3 text-sm font-semibold text-[var(--brand-deep)]">Monthly purchases</p>
          {dashboard.monthly_purchases.length === 0 ? (
            <p className="text-sm text-[var(--muted)]">No verified invoices yet.</p>
          ) : (
            <ul className="space-y-2">
              {dashboard.monthly_purchases.map((month) => (
                <li key={month.month} className="grid grid-cols-[5rem_1fr_7rem] items-center gap-3 text-sm">
                  <span>{month.month}</span>
                  <div className="bar-track">
                    <div className="bar-fill" style={{ width: `${(month.total / largestMonth) * 100}%` }} />
                  </div>
                  <span className="text-right">{money(month.total)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="bs-card overflow-x-auto">
          <p className="mb-3 text-sm font-semibold text-[var(--brand-deep)]">Spending by supplier</p>
          {dashboard.supplier_spend.length === 0 ? (
            <p className="text-sm text-[var(--muted)]">No verified invoices yet.</p>
          ) : (
            <table className="entry-table">
              <tbody>
                {dashboard.supplier_spend.map((supplier) => (
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
          )}
        </div>
      </div>

      <div className="bs-card">
        <p className="text-sm font-semibold text-[var(--brand-deep)]">Next-period purchasing forecast</p>
        <p className="mt-1 text-sm text-[var(--muted)]">{dashboard.forecast_note}</p>
      </div>
    </div>
  );
}
