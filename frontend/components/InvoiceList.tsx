"use client";

/**
 * Invoice Manager list: the photo upload, search filters, and the matching invoices from the
 * backend (GET /invoices). Each row opens that invoice's review screen.
 * A ?status=draft (or verified/rejected) in the address pre-selects the status filter.
 */

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { DocumentUpload } from "@/components/DocumentUpload";
import { errorMessage, listInvoices, listSuppliers } from "@/lib/api";
import { money } from "@/lib/money";
import type { InvoiceQuery, InvoiceStatus, InvoiceSummary, PaymentStatus, Supplier } from "@/lib/types";

const STATUSES: InvoiceStatus[] = ["draft", "verified", "rejected"];
const PAYMENT_STATUSES: PaymentStatus[] = ["unpaid", "paid", "unconfirmed"];

/** The status filter given in the address, if it is a valid one. */
function statusFrom(value: string | null): InvoiceStatus | undefined {
  return STATUSES.find((status) => status === value);
}

/** A coloured label for an invoice's review status. */
export function StatusBadge({ status }: { status: InvoiceStatus }) {
  const tone = status === "verified" ? "badge-good" : status === "rejected" ? "badge-bad" : "badge-warn";
  return <span className={`badge ${tone}`}>{status}</span>;
}

/** The upload box, filters and invoice table. */
export function InvoiceList() {
  const searchParams = useSearchParams();
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [filters, setFilters] = useState({
    supplier_id: "",
    status: statusFrom(searchParams.get("status")) ?? "",
    payment_status: "",
    invoice_number: "",
    date_from: "",
    date_to: "",
  });
  const [query, setQuery] = useState<InvoiceQuery>(() => ({ status: statusFrom(searchParams.get("status")) }));
  const [result, setResult] = useState<{ query: InvoiceQuery; rows: InvoiceSummary[]; error: string } | null>(
    null,
  );

  // Load the supplier names for the filter once.
  useEffect(() => {
    listSuppliers()
      .then(setSuppliers)
      .catch(() => setSuppliers([]));
  }, []);

  // Ask the backend for the invoices whenever the search changes.
  useEffect(() => {
    let cancelled = false;
    listInvoices(query)
      .then((rows) => {
        if (!cancelled) setResult({ query, rows, error: "" });
      })
      .catch((error) => {
        if (!cancelled) setResult({ query, rows: [], error: errorMessage(error) });
      });
    return () => {
      cancelled = true;
    };
  }, [query]);

  const loading = result?.query !== query;
  const rows = result?.rows ?? [];

  /** Turn the filter boxes into a search. */
  function search(event: FormEvent) {
    event.preventDefault();
    setQuery({
      supplier_id: filters.supplier_id || undefined,
      status: (filters.status || undefined) as InvoiceStatus | undefined,
      payment_status: (filters.payment_status || undefined) as PaymentStatus | undefined,
      invoice_number: filters.invoice_number.trim() || undefined,
      date_from: filters.date_from || undefined,
      date_to: filters.date_to || undefined,
    });
  }

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-[var(--brand-deep)]">Invoice Manager</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Upload a photo or type an invoice in. Every invoice stays a draft until you verify it.
          </p>
        </div>
        <Link href="/invoice-manager/new" className="btn btn-primary">
          Type in an invoice
        </Link>
      </div>

      <DocumentUpload className="mb-5" />

      <form onSubmit={search} className="bs-card mb-4 grid gap-3 !p-4 sm:grid-cols-3 lg:grid-cols-7">
        <select
          className="field"
          value={filters.supplier_id}
          onChange={(event) => setFilters({ ...filters, supplier_id: event.target.value })}
          aria-label="Supplier"
        >
          <option value="">All suppliers</option>
          {suppliers.map((supplier) => (
            <option key={supplier.id} value={supplier.id}>
              {supplier.name}
            </option>
          ))}
        </select>
        <input
          className="field"
          placeholder="Invoice number"
          value={filters.invoice_number}
          onChange={(event) => setFilters({ ...filters, invoice_number: event.target.value })}
        />
        <select
          className="field"
          value={filters.status}
          onChange={(event) => setFilters({ ...filters, status: event.target.value as InvoiceStatus | "" })}
          aria-label="Review status"
        >
          <option value="">Any status</option>
          {STATUSES.map((status) => (
            <option key={status} value={status}>
              {status}
            </option>
          ))}
        </select>
        <select
          className="field"
          value={filters.payment_status}
          onChange={(event) => setFilters({ ...filters, payment_status: event.target.value })}
          aria-label="Payment status"
        >
          <option value="">Any payment</option>
          {PAYMENT_STATUSES.map((status) => (
            <option key={status} value={status}>
              {status}
            </option>
          ))}
        </select>
        <input
          className="field"
          type="date"
          aria-label="From date"
          value={filters.date_from}
          onChange={(event) => setFilters({ ...filters, date_from: event.target.value })}
        />
        <input
          className="field"
          type="date"
          aria-label="To date"
          value={filters.date_to}
          onChange={(event) => setFilters({ ...filters, date_to: event.target.value })}
        />
        <button className="btn btn-secondary" type="submit">
          Search
        </button>
      </form>

      {result?.error ? <p className="banner-error mb-4">{result.error}</p> : null}

      <div className="bs-card overflow-x-auto !p-0">
        <table className="entry-table">
          <thead>
            <tr>
              <th>Supplier</th>
              <th>Invoice #</th>
              <th>Date</th>
              <th className="text-right">Total</th>
              <th>Status</th>
              <th>Math</th>
              <th>Payment</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={8} className="px-3 py-6 text-[var(--muted)]">
                  Loading…
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-3 py-6 text-[var(--muted)]">
                  No invoices match. Upload a photo or type one in.
                </td>
              </tr>
            ) : (
              rows.map((invoice) => (
                <tr key={invoice.id}>
                  <td className="px-2 py-2">
                    {invoice.supplier_name ?? invoice.supplier_name_raw ?? "—"}
                    {invoice.supplier_id ? null : <span className="badge badge-warn ml-2">not matched</span>}
                  </td>
                  <td className="px-2 py-2">{invoice.invoice_number ?? "—"}</td>
                  <td className="px-2 py-2">{invoice.invoice_date ?? "—"}</td>
                  <td className="px-2 py-2 text-right">{money(invoice.total)}</td>
                  <td className="px-2 py-2">
                    <StatusBadge status={invoice.status} />
                  </td>
                  <td className="px-2 py-2">
                    {invoice.math_ok === null ? (
                      "—"
                    ) : invoice.math_ok ? (
                      <span className="badge badge-good">adds up</span>
                    ) : (
                      <span className="badge badge-bad">check</span>
                    )}
                  </td>
                  <td className="px-2 py-2">{invoice.payment_status}</td>
                  <td className="px-2 py-2 text-right">
                    <Link
                      href={`/invoice-manager/${invoice.id}`}
                      className="text-sm font-medium text-[var(--brand)] hover:underline"
                    >
                      Open
                    </Link>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
