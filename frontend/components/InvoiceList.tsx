"use client";

/**
 * Invoice Manager list: the photo upload, search filters, and the matching invoices from the
 * backend (GET /invoices), 100 to a page with Previous / page numbers / Next below the table.
 * Each row opens that invoice's review screen.
 * A ?status=draft (or verified/rejected) in the address pre-selects the status filter.
 */

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { DocumentUpload } from "@/components/DocumentUpload";
import { errorMessage, listInvoicePage, listSuppliers } from "@/lib/api";
import { money } from "@/lib/money";
import type { InvoiceQuery, InvoiceStatus, InvoiceSummary, PaymentStatus, Supplier } from "@/lib/types";

const STATUSES: InvoiceStatus[] = ["draft", "verified", "rejected"];
const PAYMENT_STATUSES: PaymentStatus[] = ["unpaid", "paid", "unconfirmed"];
const PAGE_SIZE = 100;

/** The page numbers to show: the first, the last, and two either side of the current one. */
function pageNumbers(current: number, count: number): (number | "gap")[] {
  const wanted = new Set([0, count - 1, current - 2, current - 1, current, current + 1, current + 2]);
  const pages = [...wanted].filter((page) => page >= 0 && page < count).sort((a, b) => a - b);
  const out: (number | "gap")[] = [];
  pages.forEach((page, index) => {
    if (index > 0 && page - pages[index - 1] > 1) out.push("gap");
    out.push(page);
  });
  return out;
}

/** "Showing 101–200 of 880" with Previous, page numbers and Next. */
function Pager({ offset, total, onPage }: { offset: number; total: number; onPage: (offset: number) => void }) {
  const count = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const current = Math.floor(offset / PAGE_SIZE);
  const first = total === 0 ? 0 : offset + 1;
  const last = Math.min(total, offset + PAGE_SIZE);
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--line)] px-3 py-3 text-sm">
      <p className="text-[var(--muted)]">
        Showing {first}–{last} of {total} invoice{total === 1 ? "" : "s"}
      </p>
      {count > 1 ? (
        <nav className="flex flex-wrap items-center gap-1" aria-label="Invoice pages">
          <button type="button" className="btn btn-secondary" disabled={current === 0} onClick={() => onPage(offset - PAGE_SIZE)}>
            Previous
          </button>
          {pageNumbers(current, count).map((page, index) =>
            page === "gap" ? (
              <span key={`gap-${index}`} className="px-1 text-[var(--muted)]">
                …
              </span>
            ) : (
              <button
                key={page}
                type="button"
                className={`btn ${page === current ? "btn-primary" : "btn-secondary"}`}
                aria-current={page === current ? "page" : undefined}
                onClick={() => onPage(page * PAGE_SIZE)}
              >
                {page + 1}
              </button>
            ),
          )}
          <button
            type="button"
            className="btn btn-secondary"
            disabled={current >= count - 1}
            onClick={() => onPage(offset + PAGE_SIZE)}
          >
            Next
          </button>
        </nav>
      ) : null}
    </div>
  );
}

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
  const [query, setQuery] = useState<InvoiceQuery>(() => ({
    status: statusFrom(searchParams.get("status")),
    limit: PAGE_SIZE,
    offset: 0,
  }));
  const [result, setResult] = useState<{ query: InvoiceQuery; rows: InvoiceSummary[]; total: number; error: string } | null>(
    null,
  );
  const tableTop = useRef<HTMLDivElement>(null);

  // Load the supplier names for the filter once.
  useEffect(() => {
    listSuppliers()
      .then(setSuppliers)
      .catch(() => setSuppliers([]));
  }, []);

  // Ask the backend for this page of invoices whenever the search or the page changes.
  useEffect(() => {
    let cancelled = false;
    listInvoicePage(query)
      .then(({ rows, total }) => {
        if (!cancelled) setResult({ query, rows, total, error: "" });
      })
      .catch((error) => {
        if (!cancelled) setResult({ query, rows: [], total: 0, error: errorMessage(error) });
      });
    return () => {
      cancelled = true;
    };
  }, [query]);

  const loading = result?.query !== query;
  const rows = result?.rows ?? [];

  /** Open another page of the results and bring the table into view. */
  function goToPage(offset: number) {
    setQuery({ ...query, offset: Math.max(0, offset) });
    tableTop.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /** Turn the filter boxes into a search (starting again at the first page). */
  function search(event: FormEvent) {
    event.preventDefault();
    setQuery({
      limit: PAGE_SIZE,
      offset: 0,
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

      <div ref={tableTop} className="bs-card scroll-mt-4 !p-0">
        <div className="overflow-x-auto">
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
        {result && !result.error ? (
          <Pager offset={query.offset ?? 0} total={result.total} onPage={goToPage} />
        ) : null}
      </div>
    </div>
  );
}
