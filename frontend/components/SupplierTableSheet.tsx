"use client";

/**
 * Bottom sheet that slides up over the supplier charts with month-scoped
 * invoices and receipt (payment) tables, plus image preview.
 */

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ImagePreviewModal } from "@/components/ImagePreviewModal";
import { StatusBadge } from "@/components/InvoiceList";
import { PaymentStatusBadge } from "@/components/PaymentList";
import { SkuPurchasesTable } from "@/components/SkuPurchasesTable";
import { errorMessage, getInvoice, setPaymentStatus } from "@/lib/api";
import { money } from "@/lib/money";
import {
  currentMonth,
  focusMonthPie,
  invoicesInMonth,
  monthFocusSummary,
  monthLabel,
  paymentsInMonth,
  previousMonth,
  skuPurchasesInMonth,
  type MonthSkuPie,
} from "@/lib/supplierMetrics";
import type { InvoiceSummary, SupplierPayment, SupplierProfile } from "@/lib/types";

const PIE_COLOURS = ["#1f6f54", "#2b6cb0", "#c4a35a", "#6b8f71", "#9b2c2c", "#5a6f66", "#8a9a92"];
const METHOD_LABELS: Record<string, string> = { cash: "Cash", cheque: "Cheque", bank_transfer: "Bank transfer" };

export type SheetTab = "invoices" | "receipts" | "purchases";

type Props = {
  open: boolean;
  profile: SupplierProfile;
  focusMonth: string;
  tab: SheetTab;
  onTabChange: (tab: SheetTab) => void;
  onFocusMonthChange: (month: string) => void;
  onClose: () => void;
  onChanged?: () => void;
};

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

/** Compact SKU pie for the focus month inside the sheet. */
function CompactPie({ pie }: { pie: MonthSkuPie }) {
  const size = 120;
  const cx = size / 2;
  const cy = size / 2;
  const r = 48;
  const starts = pie.slices.map((_, i) =>
    pie.slices.slice(0, i).reduce((sum, s) => sum + Math.max(s.share * Math.PI * 2, 0.001), -Math.PI / 2),
  );
  if (pie.totalQty <= 0 || pie.slices.length === 0) {
    return <p className="text-xs text-[var(--muted)]">No SKU mix for {pie.label}.</p>;
  }
  return (
    <div className="supplier-sheet-pie">
      <svg viewBox={`0 0 ${size} ${size}`} className="supplier-sheet-pie-svg" role="img" aria-label={`${pie.label} SKU mix`}>
        {pie.slices.map((slice, index) => {
          const sweep = slice.share * Math.PI * 2;
          const start = starts[index];
          const end = start + Math.max(sweep, 0.001);
          return (
            <path key={slice.key} d={pieSlicePath(cx, cy, r, start, end)} fill={PIE_COLOURS[index % PIE_COLOURS.length]}>
              <title>{`${slice.name}: ${slice.quantity} · Rs ${money(slice.spend)}`}</title>
            </path>
          );
        })}
      </svg>
      <ul className="supplier-sheet-pie-legend">
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

/** Invoices table for the focus month. */
function InvoicesTable({
  invoices,
  onPreview,
  onMark,
  busyId,
}: {
  invoices: InvoiceSummary[];
  onPreview: (invoice: InvoiceSummary) => void;
  onMark: (invoiceId: string, status: "paid" | "unpaid") => void;
  busyId: string | null;
}) {
  if (invoices.length === 0) {
    return <p className="text-sm text-[var(--muted)]">No invoices in this month.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="entry-table">
        <thead>
          <tr>
            <th>Invoice #</th>
            <th>Date</th>
            <th>Due</th>
            <th className="text-right">Total</th>
            <th className="text-right">Paid</th>
            <th>Status</th>
            <th>Payment</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {invoices.map((invoice) => (
            <tr key={invoice.id}>
              <td className="px-2 py-2">
                <Link href={`/invoice-manager/${invoice.id}`} className="text-[var(--brand)] hover:underline">
                  {invoice.invoice_number ?? "—"}
                </Link>
              </td>
              <td className="px-2 py-2">{invoice.invoice_date ?? "—"}</td>
              <td className="px-2 py-2">{invoice.due_date ?? "—"}</td>
              <td className="px-2 py-2 text-right">{money(invoice.total)}</td>
              <td className="px-2 py-2 text-right">{invoice.amount_paid > 0 ? money(invoice.amount_paid) : "—"}</td>
              <td className="px-2 py-2">
                <StatusBadge status={invoice.status} />
              </td>
              <td className="px-2 py-2">{invoice.payment_status}</td>
              <td className="px-2 py-2 text-right">
                <div className="flex flex-wrap justify-end gap-1">
                  {invoice.image_path || invoice.source === "agent" ? (
                    <button type="button" className="btn btn-secondary" onClick={() => onPreview(invoice)}>
                      Preview
                    </button>
                  ) : null}
                  {invoice.status === "verified" ? (
                    invoice.payment_status === "paid" ? (
                      <button
                        type="button"
                        className="btn btn-secondary"
                        disabled={busyId === invoice.id}
                        onClick={() => onMark(invoice.id, "unpaid")}
                      >
                        Mark unpaid
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="btn btn-secondary"
                        disabled={busyId === invoice.id}
                        onClick={() => onMark(invoice.id, "paid")}
                      >
                        Mark paid
                      </button>
                    )
                  ) : null}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Receipts / payments table for the focus month. */
function ReceiptsTable({
  payments,
  onPreview,
}: {
  payments: SupplierPayment[];
  onPreview: (payment: SupplierPayment) => void;
}) {
  if (payments.length === 0) {
    return <p className="text-sm text-[var(--muted)]">No receipts / payments in this month.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="entry-table">
        <thead>
          <tr>
            <th>Paid on</th>
            <th>Receipt #</th>
            <th>Invoice</th>
            <th>Method</th>
            <th className="text-right">Amount</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {payments.map((payment) => (
            <tr key={payment.id}>
              <td className="px-2 py-2">{payment.paid_on ?? "—"}</td>
              <td className="px-2 py-2">{payment.receipt_number ?? "—"}</td>
              <td className="px-2 py-2">
                {payment.invoice_id ? (
                  <Link href={`/invoice-manager/${payment.invoice_id}`} className="text-[var(--brand)] hover:underline">
                    {payment.invoice_number ?? "invoice"}
                  </Link>
                ) : (
                  payment.invoice_number_raw ?? "—"
                )}
              </td>
              <td className="px-2 py-2">{payment.method ? METHOD_LABELS[payment.method] ?? payment.method : "—"}</td>
              <td className="px-2 py-2 text-right">{money(payment.amount)}</td>
              <td className="px-2 py-2">
                <PaymentStatusBadge status={payment.status} />
              </td>
              <td className="px-2 py-2 text-right">
                {payment.image_url ? (
                  <button type="button" className="btn btn-secondary" onClick={() => onPreview(payment)}>
                    Preview
                  </button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Slide-up sheet over the supplier dashboard charts. */
export function SupplierTableSheet({
  open,
  profile,
  focusMonth,
  tab,
  onTabChange,
  onFocusMonthChange,
  onClose,
  onChanged,
}: Props) {
  const summary = useMemo(() => monthFocusSummary(profile, focusMonth), [profile, focusMonth]);
  const pie = useMemo(() => focusMonthPie(profile, focusMonth), [profile, focusMonth]);
  const invoices = useMemo(() => invoicesInMonth(profile, focusMonth), [profile, focusMonth]);
  const payments = useMemo(() => paymentsInMonth(profile, focusMonth), [profile, focusMonth]);
  const purchases = useMemo(() => skuPurchasesInMonth(profile, focusMonth), [profile, focusMonth]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState("");
  const [preview, setPreview] = useState<{
    title: string;
    url: string | null;
    loading: boolean;
    error: string;
  } | null>(null);

  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape" && !preview) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, preview]);

  async function previewInvoice(invoice: InvoiceSummary) {
    setPreview({ title: invoice.invoice_number ? `Invoice ${invoice.invoice_number}` : "Invoice", url: null, loading: true, error: "" });
    try {
      const full = await getInvoice(invoice.id);
      setPreview({
        title: full.invoice_number ? `Invoice ${full.invoice_number}` : "Invoice",
        url: full.image_url,
        loading: false,
        error: full.image_url ? "" : "No photo on this invoice.",
      });
    } catch (error) {
      setPreview({
        title: invoice.invoice_number ? `Invoice ${invoice.invoice_number}` : "Invoice",
        url: null,
        loading: false,
        error: errorMessage(error),
      });
    }
  }

  function previewPayment(payment: SupplierPayment) {
    setPreview({
      title: payment.receipt_number ? `Receipt ${payment.receipt_number}` : "Receipt",
      url: payment.image_url,
      loading: false,
      error: payment.image_url ? "" : "No photo on this payment.",
    });
  }

  async function markPayment(invoiceId: string, status: "paid" | "unpaid") {
    setActionError("");
    setBusyId(invoiceId);
    try {
      await setPaymentStatus(invoiceId, status);
      onChanged?.();
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <>
      <div
        className={`supplier-sheet-scrim ${open ? "is-open" : ""}`}
        aria-hidden={!open}
        onClick={onClose}
      />
      <div
        className={`supplier-sheet ${open ? "is-open" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-hidden={!open}
        aria-label={`Tables for ${monthLabel(focusMonth)}`}
      >
        <div className="supplier-sheet-handle" onClick={onClose} aria-hidden />
        <div className="supplier-sheet-header">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">Purchases</p>
            <h3 className="text-lg font-semibold text-[var(--brand-deep)]">{summary.label}</h3>
          </div>
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="supplier-sheet-controls">
          <button
            type="button"
            className={`btn ${focusMonth === currentMonth() ? "btn-primary" : "btn-secondary"}`}
            onClick={() => onFocusMonthChange(currentMonth())}
          >
            This month
          </button>
          <button
            type="button"
            className={`btn ${focusMonth === previousMonth() ? "btn-primary" : "btn-secondary"}`}
            onClick={() => onFocusMonthChange(previousMonth())}
          >
            Last month
          </button>
          <label className="text-sm">
            Month
            <input
              className="field ml-2"
              type="month"
              max={currentMonth()}
              value={focusMonth}
              onChange={(event) => onFocusMonthChange(event.target.value)}
            />
          </label>
        </div>

        <div className="supplier-sheet-summary">
          <div>
            <p className="supplier-kpi-label">Verified spend</p>
            <p className="font-semibold text-[var(--brand-deep)]">{money(summary.verifiedSpend)}</p>
          </div>
          <div>
            <p className="supplier-kpi-label">Invoices</p>
            <p className="font-semibold text-[var(--brand-deep)]">{summary.invoiceCount}</p>
          </div>
          <div>
            <p className="supplier-kpi-label">Receipts</p>
            <p className="font-semibold text-[var(--brand-deep)]">{summary.paymentCount}</p>
          </div>
          <div>
            <p className="supplier-kpi-label">Receipt total</p>
            <p className="font-semibold text-[var(--brand-deep)]">{money(summary.paymentTotal)}</p>
          </div>
        </div>

        <CompactPie pie={pie} />

        <div className="supplier-sheet-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "purchases"}
            className={`supplier-sheet-tab ${tab === "purchases" ? "is-active" : ""}`}
            onClick={() => onTabChange("purchases")}
          >
            By SKU ({purchases.length})
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "invoices"}
            className={`supplier-sheet-tab ${tab === "invoices" ? "is-active" : ""}`}
            onClick={() => onTabChange("invoices")}
          >
            Invoices ({invoices.length})
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "receipts"}
            className={`supplier-sheet-tab ${tab === "receipts" ? "is-active" : ""}`}
            onClick={() => onTabChange("receipts")}
          >
            Receipts ({payments.length})
          </button>
        </div>

        {actionError ? <p className="banner-error">{actionError}</p> : null}

        <div className="supplier-sheet-body">
          {tab === "purchases" ? (
            <SkuPurchasesTable rows={purchases} emptyText={`No SKU purchases in ${summary.label}.`} />
          ) : tab === "invoices" ? (
            <InvoicesTable invoices={invoices} onPreview={previewInvoice} onMark={markPayment} busyId={busyId} />
          ) : (
            <ReceiptsTable payments={payments} onPreview={previewPayment} />
          )}
        </div>
      </div>

      {preview ? (
        <ImagePreviewModal
          url={preview.url}
          title={preview.title}
          loading={preview.loading}
          error={preview.error}
          onClose={() => setPreview(null)}
        />
      ) : null}
    </>
  );
}
