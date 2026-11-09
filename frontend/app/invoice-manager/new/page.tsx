"use client";

/**
 * Type in an invoice by hand (the proposal's data-entry path). The owner picks the supplier and
 * types every value as printed; the backend (POST /invoices) audits the math, matches products and
 * saves a draft, then the review screen opens.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { InvoiceForm, type InvoiceFormValues } from "@/components/InvoiceForm";
import { ApiError, createInvoice, errorMessage, listSuppliers } from "@/lib/api";
import type { Supplier } from "@/lib/types";

/** The supplier picker and the invoice form. */
export default function NewInvoicePage() {
  const router = useRouter();
  const [suppliers, setSuppliers] = useState<Supplier[] | null>(null);
  const [supplierId, setSupplierId] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [existingId, setExistingId] = useState<string | null>(null);

  // Load the suppliers to choose from.
  useEffect(() => {
    listSuppliers()
      .then(setSuppliers)
      .catch((err) => {
        setSuppliers([]);
        setError(errorMessage(err));
      });
  }, []);

  /** Send the typed invoice to the backend and open its review screen. */
  async function save(values: InvoiceFormValues) {
    if (!supplierId) {
      setError("Choose the supplier first.");
      return;
    }
    setSaving(true);
    setError("");
    setExistingId(null);
    try {
      const invoice = await createInvoice({ ...values.header, supplier_id: supplierId, lines: values.lines });
      router.push(`/invoice-manager/${invoice.id}`);
    } catch (err) {
      setError(errorMessage(err));
      if (err instanceof ApiError && err.invoiceId) setExistingId(err.invoiceId);
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <Link href="/invoice-manager" className="text-sm text-[var(--brand)] hover:underline">
          ← Invoice Manager
        </Link>
        <h1 className="mt-1 text-2xl font-semibold text-[var(--brand-deep)]">Type in an invoice</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">
          Type every value as printed, including the line totals and the invoice total. The backend checks the
          maths and you review it before verifying.
        </p>
      </div>

      <div className="bs-card max-w-xl">
        <label className="block">
          <span className="mb-1 block text-sm font-medium">Supplier</span>
          <select className="field" value={supplierId} onChange={(event) => setSupplierId(event.target.value)}>
            <option value="">{suppliers === null ? "Loading…" : "Choose a supplier"}</option>
            {(suppliers ?? []).map((supplier) => (
              <option key={supplier.id} value={supplier.id}>
                {supplier.name}
              </option>
            ))}
          </select>
        </label>
        <p className="mt-2 text-xs text-[var(--muted)]">
          Not in the list?{" "}
          <Link href="/supplier-profile" className="text-[var(--brand)] underline">
            Add the supplier on Supplier Profile
          </Link>
          .
        </p>
      </div>

      {error ? (
        <p className="banner-error">
          {error}{" "}
          {existingId ? (
            <Link href={`/invoice-manager/${existingId}`} className="font-medium underline">
              Open the saved invoice
            </Link>
          ) : null}
        </p>
      ) : null}

      <InvoiceForm submitLabel="Save as draft" saving={saving} onSubmit={save} />
    </div>
  );
}
