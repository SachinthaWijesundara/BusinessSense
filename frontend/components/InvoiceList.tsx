"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { DocumentUpload } from "@/components/DocumentUpload";
import { SetupNotice } from "@/components/SetupNotice";
import { money } from "@/lib/money";
import { db, friendlyError, isSupabaseConfigured } from "@/lib/supabase";
import type { InvoiceWithSupplier } from "@/lib/types";

export function InvoiceList() {
  const [rows, setRows] = useState<InvoiceWithSupplier[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!isSupabaseConfigured) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    async function load() {
      const { data, error: qError } = await db()
        .from("invoices")
        .select("*, suppliers(name)")
        .order("invoice_date", { ascending: false });
      if (cancelled) return;
      if (qError) {
        setError(friendlyError(qError));
        setLoading(false);
        return;
      }
      const mapped = (data ?? []).map((row) => {
        const r = row as Record<string, unknown>;
        const suppliers = r.suppliers as { name?: string } | null;
        return {
          ...(r as unknown as InvoiceWithSupplier),
          supplier_name: suppliers?.name ?? "—",
        };
      });
      setRows(mapped);
      setLoading(false);
    }
    load().catch((err) => {
      if (!cancelled) {
        setError(friendlyError(err));
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!isSupabaseConfigured) return <SetupNotice />;

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-[var(--brand-deep)]">Invoice Manager</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Upload a photo or add invoices manually. Shared ledger with purchase-entry.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link href="/invoice-manager/suppliers/new" className="btn btn-secondary">
            Add supplier
          </Link>
          <Link href="/invoice-manager/new" className="btn btn-primary">
            Add invoice
          </Link>
        </div>
      </div>

      <DocumentUpload className="mb-5" />

      {error ? <p className="banner-error mb-4">{error}</p> : null}

      <div className="bs-card !p-0 overflow-x-auto">
        <table className="entry-table">
          <thead>
            <tr>
              <th>Photo</th>
              <th>Supplier</th>
              <th>Invoice #</th>
              <th>Date</th>
              <th className="text-right">Total</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-[var(--muted)]">
                  Loading…
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-[var(--muted)]">
                  No invoices yet. Add one or drop a photo above.
                </td>
              </tr>
            ) : (
              rows.map((inv) => (
                <tr key={inv.id}>
                  <td className="px-2 py-2">
                    {inv.image_url ? (
                      <a href={inv.image_url} target="_blank" rel="noreferrer">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={inv.image_url}
                          alt=""
                          className="h-10 w-10 rounded border border-[var(--line)] object-cover"
                        />
                      </a>
                    ) : (
                      <span className="text-xs text-[var(--muted)]">—</span>
                    )}
                  </td>
                  <td className="px-2 py-2">{inv.supplier_name}</td>
                  <td className="px-2 py-2">{inv.invoice_number}</td>
                  <td className="px-2 py-2">{inv.invoice_date || "—"}</td>
                  <td className="px-2 py-2 text-right">{money(Number(inv.total))}</td>
                  <td className="px-2 py-2 text-right">
                    <Link
                      href={`/invoice-manager/${inv.supplier_id}/${inv.id}`}
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
