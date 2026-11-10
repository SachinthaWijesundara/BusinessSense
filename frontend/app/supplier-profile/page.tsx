"use client";

/**
 * Supplier Profile screen (proposal): the supplier list, adding a supplier, the payments waiting
 * for the owner's confirmation (all suppliers), and one supplier's profile (?supplier=<id>):
 * details, usual credit days, totals and outstanding balance (calculated by the backend), invoices
 * with paid / not-paid marking, payments (record, upload a receipt, check, confirm, reject) and the
 * item lines bought.
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { DocumentUpload } from "@/components/DocumentUpload";
import { StatusBadge } from "@/components/InvoiceList";
import { PaymentForm } from "@/components/PaymentForm";
import { PaymentList } from "@/components/PaymentList";
import { SupplierForm } from "@/components/SupplierForm";
import {
  errorMessage,
  getSupplierProfile,
  listPayments,
  listSuppliers,
  setPaymentStatus,
  updateSupplier,
} from "@/lib/api";
import { money, numberText, parseNumber } from "@/lib/money";
import type { Supplier, SupplierPayment, SupplierProfile } from "@/lib/types";

/** One total from the backend, shown as a small card. */
function Total({ label, value }: { label: string; value: string }) {
  return (
    <div className="bs-card !p-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">{label}</p>
      <p className="mt-1 text-lg font-semibold text-[var(--brand-deep)]">{value}</p>
    </div>
  );
}

/** Credit-days editor: the owner records the supplier's usual credit period. */
function CreditDaysEditor({ supplier, onSaved }: { supplier: Supplier; onSaved: () => void }) {
  const [days, setDays] = useState(numberText(supplier.credit_days));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  /** Save the credit days through the backend. */
  async function save() {
    const value = parseNumber(days);
    if (value !== null && (Number.isNaN(value) || value < 0 || !Number.isInteger(value))) {
      setError("Credit days must be a whole number (0 or more).");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await updateSupplier(supplier.id, { credit_days: value });
      onSaved();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-wrap items-end gap-2">
      <label className="block">
        <span className="mb-1 block text-sm font-medium">Usual credit days</span>
        <input className="field w-28" inputMode="numeric" value={days} onChange={(event) => setDays(event.target.value)} />
      </label>
      <button type="button" className="btn btn-secondary" disabled={saving} onClick={save}>
        {saving ? "Saving…" : "Save"}
      </button>
      {error ? <p className="banner-error w-full">{error}</p> : null}
    </div>
  );
}

/** Payments of every supplier still waiting for the owner's confirmation (GET /payments?status=unconfirmed). */
function WaitingPayments({ refreshKey, onChanged }: { refreshKey: number; onChanged: () => void }) {
  const [result, setResult] = useState<{ payments: SupplierPayment[]; error: string } | null>(null);

  // Load (or reload) the waiting payments whenever something changed.
  useEffect(() => {
    let cancelled = false;
    listPayments({ status: "unconfirmed" })
      .then((payments) => {
        if (!cancelled) setResult({ payments, error: "" });
      })
      .catch((error) => {
        if (!cancelled) setResult({ payments: [], error: errorMessage(error) });
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  if (!result) return null;
  if (result.error) return <p className="banner-error">{result.error}</p>;
  if (result.payments.length === 0) return null;
  const reminders = result.payments.filter((payment) => payment.reminder_due).length;

  return (
    <div className="bs-card overflow-x-auto">
      <p className="text-sm font-semibold text-[var(--brand-deep)]">
        Payments waiting for your confirmation ({result.payments.length})
      </p>
      <p className="mb-2 mt-1 text-sm text-[var(--muted)]">
        A payment counts only after you confirm it.
        {reminders > 0 ? ` ${reminders} of them have waited more than two days; they stay unconfirmed until you answer.` : ""}
      </p>
      <PaymentList payments={result.payments} showSupplier onChanged={onChanged} />
    </div>
  );
}

/** One supplier's profile, loaded from GET /suppliers/{id}/profile. */
function ProfileView({ supplierId, refreshKey, onChanged }: { supplierId: string; refreshKey: number; onChanged: () => void }) {
  const [version, setVersion] = useState(0);
  const [result, setResult] = useState<{ key: string; profile: SupplierProfile | null; error: string } | null>(null);
  const [actionError, setActionError] = useState("");
  const [adding, setAdding] = useState<"" | "typed" | "photo">("");
  const [notice, setNotice] = useState("");
  const requestKey = `${supplierId}:${version}:${refreshKey}`;

  // Load (or reload) the profile whenever the supplier, version or a payment elsewhere changes.
  useEffect(() => {
    let cancelled = false;
    const key = `${supplierId}:${version}:${refreshKey}`;
    getSupplierProfile(supplierId)
      .then((profile) => {
        if (!cancelled) setResult({ key, profile, error: "" });
      })
      .catch((error) => {
        if (!cancelled) setResult({ key, profile: null, error: errorMessage(error) });
      });
    return () => {
      cancelled = true;
    };
  }, [supplierId, version, refreshKey]);

  /** Reload this profile and tell the page the supplier list may have changed. */
  function reload() {
    setVersion((current) => current + 1);
    onChanged();
  }

  /** Mark one verified invoice paid or unpaid, then reload the totals. */
  async function markPayment(invoiceId: string, status: "paid" | "unpaid") {
    setActionError("");
    try {
      await setPaymentStatus(invoiceId, status);
      reload();
    } catch (error) {
      setActionError(errorMessage(error));
    }
  }

  if (!result || (result.key !== requestKey && !result.profile)) {
    return <p className="text-[var(--muted)]">Loading supplier…</p>;
  }
  if (result.error) return <p className="banner-error">{result.error}</p>;
  const profile = result.profile;
  if (!profile) return null;
  const { supplier, totals } = profile;

  return (
    <div className="space-y-4">
      <div className="bs-card">
        <h2 className="text-xl font-semibold text-[var(--brand-deep)]">{supplier.name}</h2>
        <p className="mt-1 text-sm text-[var(--muted)]">
          {[supplier.phone1, supplier.phone2, supplier.email, supplier.address].filter(Boolean).join(" · ") ||
            "No contact details."}
        </p>
        <div className="mt-3">
          <CreditDaysEditor key={`${supplier.id}-${supplier.credit_days}`} supplier={supplier} onSaved={reload} />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Total label="Invoices" value={String(totals.invoice_count)} />
        <Total label="Drafts" value={String(totals.draft_count)} />
        <Total label="Verified total" value={money(totals.verified_total)} />
        <Total label="Outstanding" value={money(totals.outstanding)} />
        <Total label="Next due" value={totals.next_due_date ?? "—"} />
        <Total label="Paid (confirmed)" value={money(totals.paid_total)} />
        <Total
          label="Waiting confirmation"
          value={totals.unconfirmed_count ? `${money(totals.unconfirmed_total)} (${totals.unconfirmed_count})` : "—"}
        />
      </div>

      {actionError ? <p className="banner-error">{actionError}</p> : null}
      {notice ? <p className="banner-ok">{notice}</p> : null}

      <div className="bs-card overflow-x-auto">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm font-semibold text-[var(--brand-deep)]">Payments</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn btn-primary" onClick={() => setAdding(adding === "typed" ? "" : "typed")}>
              Record payment
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => setAdding(adding === "photo" ? "" : "photo")}>
              Upload receipt
            </button>
          </div>
        </div>
        {adding === "typed" ? (
          <div className="mb-3 rounded border border-[var(--line)] p-3">
            <PaymentForm
              supplierId={supplier.id}
              onSaved={(payment) => {
                setAdding("");
                setNotice(
                  payment.status === "confirmed"
                    ? "Payment saved and confirmed."
                    : "Payment saved. Confirm it once you have checked it.",
                );
                reload();
              }}
              onCancel={() => setAdding("")}
            />
          </div>
        ) : null}
        {adding === "photo" ? (
          <DocumentUpload
            className="mb-3"
            supplierId={supplier.id}
            onReceiptSaved={(receipt) => {
              setAdding("");
              setNotice(
                `Receipt ${receipt.receipt_number ?? ""} read: ${receipt.payments.length} payment(s) saved. ` +
                  "Check each one against the photo and confirm it.",
              );
              reload();
            }}
          />
        ) : null}
        <PaymentList
          payments={profile.payments}
          onChanged={() => {
            setNotice("");
            reload();
          }}
        />
      </div>

      <div className="bs-card overflow-x-auto">
        <p className="mb-2 text-sm font-semibold text-[var(--brand-deep)]">Invoices</p>
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
            {profile.invoices.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-3 py-4 text-[var(--muted)]">
                  No invoices from this supplier yet.
                </td>
              </tr>
            ) : (
              profile.invoices.map((invoice) => (
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
                    {invoice.status === "verified" ? (
                      invoice.payment_status === "paid" ? (
                        <button type="button" className="btn btn-secondary" onClick={() => markPayment(invoice.id, "unpaid")}>
                          Mark unpaid
                        </button>
                      ) : (
                        <button type="button" className="btn btn-secondary" onClick={() => markPayment(invoice.id, "paid")}>
                          Mark paid
                        </button>
                      )
                    ) : null}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="bs-card overflow-x-auto">
        <p className="mb-2 text-sm font-semibold text-[var(--brand-deep)]">Items bought (verified invoices)</p>
        {profile.lines.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">No verified item lines yet.</p>
        ) : (
          <table className="entry-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Invoice #</th>
                <th>Printed item</th>
                <th>Product</th>
                <th className="text-right">Qty</th>
                <th className="text-right">Rate</th>
                <th className="text-right">Line total</th>
              </tr>
            </thead>
            <tbody>
              {profile.lines.map((line, index) => (
                <tr key={`${line.invoice_id}-${index}`}>
                  <td className="px-2 py-2">{line.invoice_date ?? "—"}</td>
                  <td className="px-2 py-2">{line.invoice_number ?? "—"}</td>
                  <td className="px-2 py-2">{line.item_name}</td>
                  <td className="px-2 py-2">{line.product_name ?? "—"}</td>
                  <td className="px-2 py-2 text-right">{line.quantity ?? "—"}</td>
                  <td className="px-2 py-2 text-right">{money(line.mro)}</td>
                  <td className="px-2 py-2 text-right">{money(line.line_total)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

/** The supplier list, the add-supplier form and the selected supplier's profile. */
function SupplierProfileScreen() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const selectedId = searchParams.get("supplier");
  const [listVersion, setListVersion] = useState(0);
  const [suppliers, setSuppliers] = useState<Supplier[] | null>(null);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(false);
  const [paymentsVersion, setPaymentsVersion] = useState(0);

  // Load the supplier list (again after a supplier is added or changed).
  useEffect(() => {
    let cancelled = false;
    listSuppliers()
      .then((rows) => {
        if (!cancelled) setSuppliers(rows);
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [listVersion]);

  /** A new supplier was saved: refresh the list and open its profile. */
  function supplierCreated(supplier: Supplier) {
    setAdding(false);
    setListVersion((current) => current + 1);
    router.push(`/supplier-profile?supplier=${supplier.id}`);
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-[var(--brand-deep)]">Supplier Profile</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Invoices, items, prices, credit terms and what is still owed to each supplier.
          </p>
        </div>
        {!adding ? (
          <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>
            Add supplier
          </button>
        ) : null}
      </div>

      {adding ? (
        <div className="bs-card max-w-2xl">
          <SupplierForm onCreated={supplierCreated} onCancel={() => setAdding(false)} />
        </div>
      ) : null}

      {error ? <p className="banner-error">{error}</p> : null}

      <WaitingPayments refreshKey={paymentsVersion} onChanged={() => setPaymentsVersion((current) => current + 1)} />

      <div className="grid gap-4 lg:grid-cols-[16rem_1fr]">
        <div className="bs-card !p-2">
          {suppliers === null ? (
            <p className="p-2 text-sm text-[var(--muted)]">Loading suppliers…</p>
          ) : suppliers.length === 0 ? (
            <p className="p-2 text-sm text-[var(--muted)]">No suppliers yet.</p>
          ) : (
            <ul>
              {suppliers.map((supplier) => (
                <li key={supplier.id}>
                  <Link
                    href={`/supplier-profile?supplier=${supplier.id}`}
                    className={`block rounded px-3 py-2 text-sm hover:bg-[#eef5f1] ${
                      supplier.id === selectedId ? "bg-[#e6f4ec] font-semibold" : ""
                    }`}
                  >
                    {supplier.name}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          {selectedId ? (
            <ProfileView
              key={selectedId}
              supplierId={selectedId}
              refreshKey={paymentsVersion}
              onChanged={() => {
                setListVersion((current) => current + 1);
                setPaymentsVersion((current) => current + 1);
              }}
            />
          ) : (
            <div className="bs-card">
              <div className="bs-empty text-sm">Choose a supplier to see its profile.</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** Supplier Profile page; Suspense is needed because the screen reads ?supplier= from the address. */
export default function SupplierProfilePage() {
  return (
    <Suspense fallback={<p className="text-[var(--muted)]">Loading…</p>}>
      <SupplierProfileScreen />
    </Suspense>
  );
}
