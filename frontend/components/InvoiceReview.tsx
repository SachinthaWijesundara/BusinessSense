"use client";

/**
 * Review screen for one invoice (the proposal's Invoice Manager review): the invoice photo beside
 * the fields, the backend's math-audit result, the supplier, "Is this the same as [product]?" for
 * every unlinked line, and the owner's actions (save corrections, verify, reject, mark paid).
 * Every action is one request to the backend; the screen then shows the invoice the backend returns.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { InvoiceForm, type InvoiceFormValues } from "@/components/InvoiceForm";
import { StatusBadge } from "@/components/InvoiceList";
import { ItemAutocomplete } from "@/components/ItemAutocomplete";
import { SupplierForm } from "@/components/SupplierForm";
import {
  ApiError,
  confirmSku,
  errorMessage,
  fixInvoice,
  getInvoice,
  getInvoiceEvents,
  listProducts,
  listSuppliers,
  rejectInvoice,
  setPaymentStatus,
  verifyInvoice,
} from "@/lib/api";
import { money } from "@/lib/money";
import type { AuditEvent, Invoice, InvoiceLine, Product, Supplier } from "@/lib/types";

/** Everything the screen loads from the backend. */
type Loaded = {
  invoice: Invoice;
  products: Product[];
  suppliers: Supplier[];
  events: AuditEvent[];
};

/** Plain names for the backend's missing-field codes. */
const MISSING_LABELS: Record<string, string> = {
  supplier_id: "supplier",
  invoice_number: "invoice number",
  invoice_date: "invoice date",
  lines: "item lines",
  total: "invoice total",
};

/** A coloured label for a line's product-match state. */
function MatchBadge({ line }: { line: InvoiceLine }) {
  if (line.match_status === "linked") return <span className="badge badge-good">linked</span>;
  if (line.match_status === "new_product") return <span className="badge badge-warn">new product?</span>;
  if (line.match_status === "pending") return <span className="badge badge-warn">confirm</span>;
  return <span className="badge">not matched yet</span>;
}

/** The whole review screen for one invoice. */
export function InvoiceReview({ invoiceId }: { invoiceId: string }) {
  const [data, setData] = useState<Loaded | null>(null);
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [reasons, setReasons] = useState<string[]>([]);
  const [rejectReason, setRejectReason] = useState("");
  const [addingSupplier, setAddingSupplier] = useState(false);

  // Load the invoice, the catalogue, the suppliers and the audit log.
  useEffect(() => {
    let cancelled = false;
    Promise.all([getInvoice(invoiceId), listProducts(), listSuppliers(), getInvoiceEvents(invoiceId)])
      .then(([invoice, products, suppliers, events]) => {
        if (!cancelled) setData({ invoice, products, suppliers, events });
      })
      .catch((error) => {
        if (!cancelled) setLoadError(errorMessage(error));
      });
    return () => {
      cancelled = true;
    };
  }, [invoiceId]);

  /** Run one owner action against the backend, then show the invoice and log it returns. */
  async function run(action: () => Promise<Invoice>, successText: string) {
    setBusy(true);
    setNotice(null);
    setReasons([]);
    try {
      const invoice = await action();
      const [events, products] = await Promise.all([getInvoiceEvents(invoiceId), listProducts()]);
      setData((current) => (current ? { ...current, invoice, events, products } : current));
      setNotice({ tone: "ok", text: successText });
    } catch (error) {
      if (error instanceof ApiError && error.reasons) setReasons(error.reasons);
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  /** Save the owner's corrections; the backend re-runs the math audit and product matching. */
  function saveCorrections(values: InvoiceFormValues) {
    void run(
      () => fixInvoice(invoiceId, { ...values.header, lines: values.lines }),
      "Corrections saved. The backend checked the maths again.",
    );
  }

  /** Set the invoice's supplier. */
  function chooseSupplier(supplierId: string) {
    if (supplierId) void run(() => fixInvoice(invoiceId, { supplier_id: supplierId }), "Supplier set.");
  }

  /** A supplier was just created from this invoice: add it to the list and use it. */
  function supplierCreated(supplier: Supplier) {
    setAddingSupplier(false);
    setData((current) => (current ? { ...current, suppliers: [...current.suppliers, supplier] } : current));
    chooseSupplier(supplier.id);
  }

  /** Confirm that a line is an existing catalogue product. */
  function linkProduct(line: InvoiceLine, productId: string) {
    void run(() => confirmSku(invoiceId, { line_id: line.id, product_id: productId }), `Linked "${line.item_name}".`);
  }

  /** Create a new catalogue product from a line and link the line to it. */
  function createProductFor(line: InvoiceLine) {
    void run(
      () => confirmSku(invoiceId, { line_id: line.id, new_product: { name: line.item_name, sku: line.sku } }),
      `Created the product "${line.item_name}" and linked the line.`,
    );
  }

  /** Ask the backend to verify; it lists what is still missing if it refuses. */
  function verify() {
    void run(() => verifyInvoice(invoiceId), "Verified. The invoice now counts in supplier and item history.");
  }

  /** Keep a draft out of the ledger, with the owner's reason. */
  function reject() {
    if (!rejectReason.trim()) {
      setNotice({ tone: "error", text: "Write why the invoice is rejected." });
      return;
    }
    void run(() => rejectInvoice(invoiceId, rejectReason.trim()), "Invoice rejected.");
  }

  /** Mark a verified invoice paid or unpaid. */
  function markPayment(status: "paid" | "unpaid") {
    void run(() => setPaymentStatus(invoiceId, status), `Marked ${status}.`);
  }

  if (loadError) {
    return (
      <div className="space-y-3">
        <Link href="/invoice-manager" className="text-sm text-[var(--brand)] hover:underline">
          ← Invoice Manager
        </Link>
        <p className="banner-error">{loadError}</p>
      </div>
    );
  }
  if (!data) return <p className="text-[var(--muted)]">Loading invoice…</p>;

  const { invoice, products, suppliers, events } = data;
  const supplierName = invoice.supplier_name ?? invoice.supplier_name_raw ?? "Unknown supplier";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link href="/invoice-manager" className="text-sm text-[var(--brand)] hover:underline">
            ← Invoice Manager
          </Link>
          <h1 className="mt-1 text-2xl font-semibold text-[var(--brand-deep)]">
            {supplierName} · {invoice.invoice_number ?? "no number"}
          </h1>
          <div className="mt-2 flex flex-wrap gap-2">
            <StatusBadge status={invoice.status} />
            <span className="badge">{invoice.source === "agent" ? "read from photo" : "typed in"}</span>
            {invoice.math_ok === null ? null : (
              <span className={`badge ${invoice.math_ok ? "badge-good" : "badge-bad"}`}>
                {invoice.math_ok ? "maths adds up" : "maths does not add up"}
              </span>
            )}
            <span className="badge">payment: {invoice.payment_status}</span>
            {invoice.amount_paid > 0 ? <span className="badge">paid so far {money(invoice.amount_paid)}</span> : null}
            {invoice.supplier_id && invoice.status === "verified" ? (
              <Link href={`/supplier-profile?supplier=${invoice.supplier_id}`} className="badge hover:underline">
                payments on Supplier Profile
              </Link>
            ) : null}
            {invoice.due_date ? <span className="badge">due {invoice.due_date}</span> : null}
          </div>
        </div>
      </div>

      {notice ? <p className={notice.tone === "ok" ? "banner-ok" : "banner-error"}>{notice.text}</p> : null}
      {reasons.length > 0 ? (
        <div className="banner-partial">
          <p className="font-semibold">Before this invoice can be verified:</p>
          <ul className="mt-1 list-disc pl-5 text-sm">
            {reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="bs-card !p-3">
          <p className="mb-2 text-sm font-semibold text-[var(--brand-deep)]">Invoice photo</p>
          {invoice.image_url ? (
            <a href={invoice.image_url} target="_blank" rel="noreferrer">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={invoice.image_url}
                alt="Invoice photo"
                className="max-h-[520px] w-full rounded border border-[var(--line)] bg-[#fafcfb] object-contain"
              />
            </a>
          ) : (
            <div className="bs-empty text-sm">
              {invoice.source === "manual" ? "Typed in by hand, no photo." : "No photo stored for this invoice."}
            </div>
          )}
        </div>

        <div className="space-y-4">
          <div className="bs-card">
            <p className="text-sm font-semibold text-[var(--brand-deep)]">Supplier</p>
            {invoice.supplier_id ? (
              <p className="mt-1 text-sm">
                <Link href={`/supplier-profile?supplier=${invoice.supplier_id}`} className="text-[var(--brand)] underline">
                  {invoice.supplier_name}
                </Link>
              </p>
            ) : (
              <p className="banner-partial mt-2 text-sm">
                Not matched to a saved supplier. Read from the photo as: <strong>{invoice.supplier_name_raw ?? "—"}</strong>
              </p>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <select
                className="field max-w-xs"
                value=""
                disabled={busy}
                onChange={(event) => chooseSupplier(event.target.value)}
                aria-label="Choose supplier"
              >
                <option value="">{invoice.supplier_id ? "Change supplier…" : "Pick a saved supplier…"}</option>
                {suppliers.map((supplier) => (
                  <option key={supplier.id} value={supplier.id}>
                    {supplier.name}
                  </option>
                ))}
              </select>
              {!invoice.supplier_id && !addingSupplier ? (
                <button type="button" className="btn btn-secondary" onClick={() => setAddingSupplier(true)}>
                  Create supplier
                </button>
              ) : null}
            </div>
            {addingSupplier ? (
              <div className="mt-3">
                <SupplierForm
                  initialName={invoice.supplier_name_raw ?? ""}
                  onCreated={supplierCreated}
                  onCancel={() => setAddingSupplier(false)}
                />
              </div>
            ) : null}
          </div>

          <div className="bs-card">
            <p className="text-sm font-semibold text-[var(--brand-deep)]">Checks from the backend</p>
            {invoice.math_ok === null ? (
              <p className="mt-1 text-sm text-[var(--muted)]">The maths has not been checked yet.</p>
            ) : invoice.math_ok ? (
              <p className="mt-1 text-sm">Every line and the total add up (within Rs 1).</p>
            ) : null}
            {[...invoice.math_issues, ...invoice.field_issues].length > 0 ? (
              <ul className="mt-2 list-disc pl-5 text-sm text-[#9b2c2c]">
                {[...invoice.math_issues, ...invoice.field_issues].map((issue, index) => (
                  <li key={`${issue.code}-${index}`}>{issue.message}</li>
                ))}
              </ul>
            ) : null}
            {invoice.missing_fields.length > 0 ? (
              <p className="mt-2 text-sm">
                Missing: {invoice.missing_fields.map((field) => MISSING_LABELS[field] ?? field).join(", ")}
              </p>
            ) : null}
            {invoice.low_confidence_fields.length > 0 ? (
              <p className="mt-2 text-sm text-[#9c4221]">
                The reading agent was unsure of {invoice.low_confidence_fields.length} value(s); they are marked in
                amber below.
              </p>
            ) : null}
          </div>
        </div>
      </div>

      <div>
        <p className="mb-2 text-sm font-semibold text-[var(--brand-deep)]">Invoice values</p>
        {invoice.status === "verified" ? (
          <p className="mb-2 text-xs text-[var(--muted)]">Saving corrections moves a verified invoice back to draft.</p>
        ) : null}
        <InvoiceForm
          key={invoice.updated_at ?? invoice.id}
          invoice={invoice}
          submitLabel="Save corrections"
          saving={busy}
          onSubmit={saveCorrections}
        />
      </div>

      <div className="bs-card overflow-x-auto">
        <p className="mb-2 text-sm font-semibold text-[var(--brand-deep)]">Products</p>
        {invoice.math_ok === false ? (
          <p className="mb-2 text-sm text-[var(--muted)]">
            Products are matched once the maths adds up. You can still link them by hand.
          </p>
        ) : null}
        <table className="entry-table">
          <thead>
            <tr>
              <th>Printed item</th>
              <th>Match</th>
              <th>Product</th>
              <th className="w-72">Link to a product</th>
            </tr>
          </thead>
          <tbody>
            {invoice.lines.map((line) => (
              <tr key={line.id}>
                <td className="px-2 py-2">
                  {line.item_name}
                  {line.sku ? <span className="ml-2 text-xs text-[var(--muted)]">{line.sku}</span> : null}
                </td>
                <td className="px-2 py-2">
                  <MatchBadge line={line} />
                </td>
                <td className="px-2 py-2 text-sm">
                  {line.match_status === "linked" ? (
                    <span>{line.product_name ?? "linked"}</span>
                  ) : line.match_candidates.length > 0 ? (
                    <div className="space-y-1">
                      <p>Is this the same as…</p>
                      {line.match_candidates.slice(0, 3).map((candidate) => (
                        <button
                          key={candidate.product_id}
                          type="button"
                          className="btn btn-secondary mr-1"
                          disabled={busy}
                          onClick={() => linkProduct(line, candidate.product_id)}
                        >
                          {candidate.product_name} ({Math.round(candidate.similarity * 100)}%)
                        </button>
                      ))}
                    </div>
                  ) : (
                    <span className="text-[var(--muted)]">
                      {line.match_status === "unmatched"
                        ? "Product matching runs once the math audit passes."
                        : "No similar product in the catalogue."}
                    </span>
                  )}
                  {line.match_reason ? (
                    <p className="mt-1 text-xs text-[var(--muted)]">{line.match_reason}</p>
                  ) : null}
                </td>
                <td className="px-2 py-2">
                  {/* Only a draft can change its product links; verified history stays as verified. */}
                  {invoice.status === "draft" ? (
                    <ItemAutocomplete products={products} onPick={(product) => linkProduct(line, product.id)} />
                  ) : null}
                  {invoice.status === "draft" && line.match_status !== "linked" ? (
                    <button
                      type="button"
                      className="btn btn-secondary mt-1"
                      disabled={busy}
                      onClick={() => createProductFor(line)}
                    >
                      Create new product
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="bs-card flex flex-wrap items-end gap-3">
        {invoice.status !== "verified" ? (
          <button type="button" className="btn btn-primary" disabled={busy} onClick={verify}>
            Verify invoice
          </button>
        ) : (
          <>
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy || invoice.payment_status === "paid"}
              onClick={() => markPayment("paid")}
            >
              Mark paid
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              disabled={busy || invoice.payment_status !== "paid"}
              onClick={() => markPayment("unpaid")}
            >
              Mark unpaid
            </button>
          </>
        )}
        {invoice.status === "draft" ? (
          <div className="ml-auto flex flex-wrap items-end gap-2">
            <input
              className="field w-64"
              placeholder="Why reject this invoice?"
              value={rejectReason}
              onChange={(event) => setRejectReason(event.target.value)}
            />
            <button type="button" className="btn btn-danger" disabled={busy} onClick={reject}>
              Reject
            </button>
          </div>
        ) : null}
      </div>

      <div className="bs-card">
        <p className="mb-2 text-sm font-semibold text-[var(--brand-deep)]">History</p>
        {events.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">Nothing recorded yet.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {events.map((event) => (
              <li key={event.id}>
                <span className="text-[var(--muted)]">
                  {event.created_at ? new Date(event.created_at).toLocaleString() : ""}
                </span>{" "}
                <span className="font-medium">{event.event_type.replace(/_/g, " ")}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
