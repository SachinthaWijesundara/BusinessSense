"use client";

/**
 * Supplier payment form: type in a new payment (POST /payments) or correct one, e.g. a payment
 * read from a receipt photo (POST /payments/{id}/fix). The owner picks the supplier and the invoice
 * it pays; every rule (what is still owed, whether it can be confirmed) is checked by the backend.
 * Values the reading agent was unsure of are highlighted.
 */

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { ApiError, confirmPayment, createPayment, errorMessage, fixPayment, listInvoices, listSuppliers } from "@/lib/api";
import { money, numberText, parseNumber, textOrNull } from "@/lib/money";
import type { InvoiceSummary, PaymentMethod, Supplier, SupplierPayment } from "@/lib/types";

type Props = {
  /** The payment to correct; leave out to record a new one. */
  payment?: SupplierPayment;
  /** Fix the supplier (recording from that supplier's profile). */
  supplierId?: string;
  /** Pre-select the invoice this payment is for. */
  invoiceId?: string;
  onSaved: (payment: SupplierPayment) => void;
  /** The corrections were saved but the payment could not be confirmed: refresh, keep the form open. */
  onChanged?: () => void;
  onCancel?: () => void;
};

const METHODS: { value: PaymentMethod; label: string }[] = [
  { value: "cash", label: "Cash" },
  { value: "cheque", label: "Cheque" },
  { value: "bank_transfer", label: "Bank transfer" },
];

/** The form's text boxes, as typed. */
type Values = {
  supplierId: string;
  invoiceId: string;
  invoiceNumberRaw: string;
  amount: string;
  paidOn: string;
  method: string;
  receiptNumber: string;
  chequeNumber: string;
  bankName: string;
  chequeDate: string;
};

/** Start from the payment being corrected, or from empty boxes. */
function initialValues(payment: SupplierPayment | undefined, supplierId?: string, invoiceId?: string): Values {
  return {
    supplierId: payment?.supplier_id ?? supplierId ?? "",
    invoiceId: payment?.invoice_id ?? invoiceId ?? "",
    invoiceNumberRaw: payment?.invoice_number_raw ?? "",
    amount: numberText(payment?.amount),
    paidOn: payment?.paid_on ?? "",
    method: payment?.method ?? "",
    receiptNumber: payment?.receipt_number ?? "",
    chequeNumber: payment?.cheque_number ?? "",
    bankName: payment?.bank_name ?? "",
    chequeDate: payment?.cheque_date ?? "",
  };
}

/** The payment fields and the Save / Save and confirm buttons. */
export function PaymentForm({ payment, supplierId, invoiceId, onSaved, onChanged, onCancel }: Props) {
  const [values, setValues] = useState<Values>(() => initialValues(payment, supplierId, invoiceId));
  const [suppliers, setSuppliers] = useState<Supplier[] | null>(null);
  const [invoiceList, setInvoiceList] = useState<{ supplierId: string; rows: InvoiceSummary[] } | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [reasons, setReasons] = useState<string[]>([]);
  const unsure = new Set((payment?.low_confidence_fields ?? []).map((path) => path.split(".").pop() ?? path));

  // Load the suppliers to choose from (unless the supplier is fixed).
  useEffect(() => {
    if (supplierId) return;
    listSuppliers()
      .then(setSuppliers)
      .catch((err) => setError(errorMessage(err)));
  }, [supplierId]);

  // Load the chosen supplier's invoices, to pick the one this payment is for.
  useEffect(() => {
    if (!values.supplierId) return;
    let cancelled = false;
    const chosen = values.supplierId;
    listInvoices({ supplier_id: chosen, limit: 500 })
      .then((rows) => {
        if (!cancelled) setInvoiceList({ supplierId: chosen, rows: rows.filter((row) => row.status !== "rejected") });
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [values.supplierId]);

  const invoices = invoiceList && invoiceList.supplierId === values.supplierId ? invoiceList.rows : [];

  /** Change one box. */
  function set<K extends keyof Values>(key: K, value: Values[K]) {
    setValues((current) => ({ ...current, [key]: value }));
  }

  /** CSS for a box the reading agent was unsure of. */
  function tone(...fields: string[]): string {
    return fields.some((name) => unsure.has(name)) ? "field field-unsure" : "field";
  }

  /** Check the boxes and save; with ``confirm`` the owner confirms the payment as well. */
  async function save(confirm: boolean) {
    const amount = parseNumber(values.amount);
    if (amount !== null && (Number.isNaN(amount) || amount <= 0)) {
      setError("The amount must be a number above 0.");
      return;
    }
    if (!payment && (!values.supplierId || amount === null || !values.paidOn)) {
      setError("Choose the supplier and enter the amount and the payment date.");
      return;
    }
    const fields = {
      supplier_id: values.supplierId || null,
      invoice_id: values.invoiceId || null,
      amount,
      paid_on: values.paidOn || null,
      method: (values.method || null) as PaymentMethod | null,
      receipt_number: textOrNull(values.receiptNumber),
      cheque_number: textOrNull(values.chequeNumber),
      bank_name: textOrNull(values.bankName),
      cheque_date: values.chequeDate || null,
    };
    setSaving(true);
    setError("");
    setReasons([]);
    try {
      let saved: SupplierPayment;
      if (payment) {
        saved = await fixPayment(payment.id, {
          ...fields,
          invoice_number_raw: values.invoiceId ? null : textOrNull(values.invoiceNumberRaw),
        });
        if (confirm) {
          try {
            saved = await confirmPayment(saved.id);
          } catch (err) {
            // The corrections are saved; show why it cannot be confirmed yet.
            onChanged?.();
            throw err;
          }
        }
      } else {
        saved = await createPayment({ ...fields, supplier_id: values.supplierId, amount: amount as number,
                                      paid_on: values.paidOn, confirm });
      }
      onSaved(saved);
    } catch (err) {
      setError(errorMessage(err));
      if (err instanceof ApiError && err.reasons) setReasons(err.reasons);
    } finally {
      setSaving(false);
    }
  }

  /** The form was submitted with Enter: a plain save. */
  function submit(event: FormEvent) {
    event.preventDefault();
    void save(false);
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      {error ? (
        <div className="banner-error">
          <p>{error}</p>
          {reasons.length > 0 ? (
            <ul className="mt-1 list-disc pl-5 text-sm">
              {reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {payment?.image_url ? (
        <p className="text-sm">
          <a href={payment.image_url} target="_blank" rel="noreferrer" className="text-[var(--brand)] underline">
            Open the receipt photo
          </a>{" "}
          and check every value against it.
        </p>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2">
        {!supplierId ? (
          <label className="block">
            <span className="mb-1 block text-sm font-medium">Supplier</span>
            <select
              className={values.supplierId ? tone("name") : "field field-missing"}
              value={values.supplierId}
              onChange={(event) => setValues((current) => ({ ...current, supplierId: event.target.value, invoiceId: "" }))}
            >
              <option value="">{suppliers === null ? "Loading…" : "Choose a supplier"}</option>
              {(suppliers ?? []).map((supplier) => (
                <option key={supplier.id} value={supplier.id}>
                  {supplier.name}
                </option>
              ))}
            </select>
            {payment?.supplier_name_raw && !values.supplierId ? (
              <span className="mt-1 block text-xs text-[var(--muted)]">
                Read from the receipt: “{payment.supplier_name_raw}”. Not in the list?{" "}
                <Link href="/supplier-profile" className="underline">
                  Add the supplier
                </Link>
                .
              </span>
            ) : null}
          </label>
        ) : null}

        <label className="block">
          <span className="mb-1 block text-sm font-medium">Invoice this pays</span>
          <select
            className={tone("invoice_number")}
            value={values.invoiceId}
            disabled={!values.supplierId}
            onChange={(event) => set("invoiceId", event.target.value)}
          >
            <option value="">Not for a saved invoice</option>
            {invoices.map((invoice) => (
              <option key={invoice.id} value={invoice.id}>
                {invoice.invoice_number ?? "no number"} · {invoice.invoice_date ?? "no date"} · total {money(invoice.total)}
                {invoice.amount_paid > 0 ? ` · paid ${money(invoice.amount_paid)}` : ""}
                {invoice.status === "draft" ? " · draft" : ""}
              </option>
            ))}
          </select>
          {payment?.invoice_number_raw && !values.invoiceId ? (
            <span className="mt-1 block text-xs text-[var(--muted)]">
              The receipt names invoice{" "}
              <input
                className="field mt-1 inline-block w-40"
                aria-label="Invoice number on the receipt"
                value={values.invoiceNumberRaw}
                onChange={(event) => set("invoiceNumberRaw", event.target.value)}
              />{" "}
              which is not saved for this supplier. Save that invoice first, pick it above, or clear the number if it is
              an old paper invoice.
            </span>
          ) : null}
        </label>

        <label className="block">
          <span className="mb-1 block text-sm font-medium">Amount paid (Rs)</span>
          <input
            className={values.amount ? tone("amount") : "field field-missing"}
            inputMode="decimal"
            value={values.amount}
            onChange={(event) => set("amount", event.target.value)}
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-sm font-medium">Payment date</span>
          <input
            className={values.paidOn ? tone("receipt_date") : "field field-missing"}
            type="date"
            value={values.paidOn}
            onChange={(event) => set("paidOn", event.target.value)}
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-sm font-medium">Paid by</span>
          <select className={tone("payment_method")} value={values.method} onChange={(event) => set("method", event.target.value)}>
            <option value="">Not recorded</option>
            {METHODS.map((method) => (
              <option key={method.value} value={method.value}>
                {method.label}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="mb-1 block text-sm font-medium">Receipt number</span>
          <input className={tone("receipt_number")} value={values.receiptNumber} onChange={(event) => set("receiptNumber", event.target.value)} />
        </label>
        {values.method === "cheque" || values.chequeNumber || values.bankName || values.chequeDate ? (
          <>
            <label className="block">
              <span className="mb-1 block text-sm font-medium">Cheque number</span>
              <input className={tone("cheque_number")} value={values.chequeNumber} onChange={(event) => set("chequeNumber", event.target.value)} />
            </label>
            <label className="block">
              <span className="mb-1 block text-sm font-medium">Bank</span>
              <input className={tone("bank_name")} value={values.bankName} onChange={(event) => set("bankName", event.target.value)} />
            </label>
            <label className="block">
              <span className="mb-1 block text-sm font-medium">Cheque date</span>
              <input
                className={tone("cheque_date")}
                type="date"
                value={values.chequeDate}
                onChange={(event) => set("chequeDate", event.target.value)}
              />
            </label>
          </>
        ) : null}
      </div>

      <div className="flex flex-wrap gap-3">
        <button className="btn btn-primary" type="button" disabled={saving} onClick={() => void save(true)}>
          {saving ? "Saving…" : payment ? "Save and confirm" : "Save and confirm payment"}
        </button>
        <button className="btn btn-secondary" type="submit" disabled={saving}>
          {payment ? "Save corrections" : "Save, confirm later"}
        </button>
        {onCancel ? (
          <button type="button" className="btn btn-secondary" onClick={onCancel}>
            Cancel
          </button>
        ) : null}
      </div>
    </form>
  );
}
