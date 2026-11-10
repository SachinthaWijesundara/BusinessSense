"use client";

/**
 * Supplier payments table (Supplier Profile). Each payment shows its date, receipt, the invoice it
 * pays, method, amount and status. An unconfirmed payment can be checked and corrected, confirmed
 * by the owner (POST /payments/{id}/confirm) or rejected with a reason; the backend says why a
 * payment cannot be confirmed yet. A payment waiting more than two days is marked as a reminder.
 */

import Link from "next/link";
import { Fragment, useState } from "react";
import { PaymentForm } from "@/components/PaymentForm";
import { ApiError, confirmPayment, errorMessage, rejectPayment } from "@/lib/api";
import { money } from "@/lib/money";
import type { SupplierPayment, SupplierPaymentStatus } from "@/lib/types";

type Props = {
  payments: SupplierPayment[];
  /** Show the supplier column (the list of payments waiting across all suppliers). */
  showSupplier?: boolean;
  /** Something changed: reload the payments and totals. */
  onChanged: () => void;
  emptyText?: string;
};

const METHOD_LABELS: Record<string, string> = { cash: "Cash", cheque: "Cheque", bank_transfer: "Bank transfer" };

/** A coloured label for a payment's status. */
export function PaymentStatusBadge({ status }: { status: SupplierPaymentStatus }) {
  const tone = status === "confirmed" ? "badge-good" : status === "rejected" ? "badge-bad" : "badge-warn";
  return <span className={`badge ${tone}`}>{status}</span>;
}

/** The table, with an inline editor, reject box and refusal reasons per row. */
export function PaymentList({ payments, showSupplier = false, onChanged, emptyText = "No payments recorded yet." }: Props) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState<{ id: string; reason: string } | null>(null);
  const [problem, setProblem] = useState<{ id: string; text: string; reasons: string[] } | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const columns = showSupplier ? 8 : 7;

  /** Run one action on a payment, show its refusal reasons, and reload on success. */
  async function act(paymentId: string, action: () => Promise<unknown>) {
    setBusyId(paymentId);
    setProblem(null);
    try {
      await action();
      setRejecting(null);
      onChanged();
    } catch (error) {
      setProblem({ id: paymentId, text: errorMessage(error), reasons: error instanceof ApiError ? error.reasons ?? [] : [] });
    } finally {
      setBusyId(null);
    }
  }

  if (payments.length === 0) return <p className="text-sm text-[var(--muted)]">{emptyText}</p>;

  return (
    <table className="entry-table">
      <thead>
        <tr>
          <th>Paid on</th>
          {showSupplier ? <th>Supplier</th> : null}
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
          <Fragment key={payment.id}>
            <tr className={payment.status === "rejected" ? "text-[var(--muted)]" : ""}>
              <td className="px-2 py-2">{payment.paid_on ?? "—"}</td>
              {showSupplier ? (
                <td className="px-2 py-2">
                  {payment.supplier_id ? (
                    <Link href={`/supplier-profile?supplier=${payment.supplier_id}`} className="text-[var(--brand)] hover:underline">
                      {payment.supplier_name}
                    </Link>
                  ) : (
                    <span className="badge badge-bad">pick supplier{payment.supplier_name_raw ? `: “${payment.supplier_name_raw}”` : ""}</span>
                  )}
                </td>
              ) : null}
              <td className="px-2 py-2">{payment.receipt_number ?? "—"}</td>
              <td className="px-2 py-2">
                {payment.invoice_id ? (
                  <Link href={`/invoice-manager/${payment.invoice_id}`} className="text-[var(--brand)] hover:underline">
                    {payment.invoice_number ?? "invoice"}
                  </Link>
                ) : payment.invoice_number_raw ? (
                  <span className="badge badge-warn" title="Not saved for this supplier">
                    {payment.invoice_number_raw} not saved
                  </span>
                ) : (
                  "—"
                )}
              </td>
              <td className="px-2 py-2">
                {payment.method ? METHOD_LABELS[payment.method] : "—"}
                {payment.cheque_number ? <span className="block text-xs text-[var(--muted)]">No. {payment.cheque_number}</span> : null}
              </td>
              <td className="px-2 py-2 text-right">{money(payment.amount)}</td>
              <td className="px-2 py-2">
                <div className="flex flex-wrap gap-1">
                  <PaymentStatusBadge status={payment.status} />
                  {payment.reminder_due ? <span className="badge badge-bad">waiting 2+ days</span> : null}
                  {payment.source === "agent" ? <span className="badge">from photo</span> : null}
                </div>
              </td>
              <td className="px-2 py-2 text-right">
                <div className="flex flex-wrap justify-end gap-2">
                  {payment.image_url ? (
                    <a href={payment.image_url} target="_blank" rel="noreferrer" className="btn btn-secondary">
                      Photo
                    </a>
                  ) : null}
                  {payment.status === "unconfirmed" ? (
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={busyId === payment.id}
                      onClick={() => void act(payment.id, () => confirmPayment(payment.id))}
                    >
                      Confirm
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={() => setEditingId(editingId === payment.id ? null : payment.id)}
                  >
                    {payment.status === "unconfirmed" ? "Check / correct" : "Correct"}
                  </button>
                  {payment.status === "unconfirmed" ? (
                    <button type="button" className="btn btn-danger" onClick={() => setRejecting({ id: payment.id, reason: "" })}>
                      Reject
                    </button>
                  ) : null}
                </div>
              </td>
            </tr>

            {problem?.id === payment.id ? (
              <tr>
                <td colSpan={columns} className="px-2 pb-2">
                  <div className="banner-partial">
                    <p className="font-semibold">{problem.text}</p>
                    {problem.reasons.length > 0 ? (
                      <ul className="mt-1 list-disc pl-5 text-sm">
                        {problem.reasons.map((reason) => (
                          <li key={reason}>{reason}</li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                </td>
              </tr>
            ) : null}

            {rejecting?.id === payment.id ? (
              <tr>
                <td colSpan={columns} className="px-2 pb-2">
                  <div className="flex flex-wrap items-end gap-2">
                    <input
                      className="field w-72"
                      placeholder="Why reject this payment?"
                      value={rejecting.reason}
                      onChange={(event) => setRejecting({ id: payment.id, reason: event.target.value })}
                    />
                    <button
                      type="button"
                      className="btn btn-danger"
                      disabled={busyId === payment.id || !rejecting.reason.trim()}
                      onClick={() => void act(payment.id, () => rejectPayment(payment.id, rejecting.reason.trim()))}
                    >
                      Reject payment
                    </button>
                    <button type="button" className="btn btn-secondary" onClick={() => setRejecting(null)}>
                      Cancel
                    </button>
                  </div>
                </td>
              </tr>
            ) : null}

            {editingId === payment.id ? (
              <tr>
                <td colSpan={columns} className="bg-[#f7faf8] px-3 py-3">
                  {payment.status !== "unconfirmed" ? (
                    <p className="mb-2 text-sm text-[var(--muted)]">
                      Saving corrections puts this payment back to unconfirmed until you confirm it again.
                    </p>
                  ) : null}
                  <PaymentForm
                    payment={payment}
                    onSaved={() => {
                      setEditingId(null);
                      onChanged();
                    }}
                    onChanged={onChanged}
                    onCancel={() => setEditingId(null)}
                  />
                </td>
              </tr>
            ) : null}
          </Fragment>
        ))}
      </tbody>
    </table>
  );
}
