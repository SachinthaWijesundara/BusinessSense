"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { DocumentUpload } from "@/components/DocumentUpload";
import { SetupNotice } from "@/components/SetupNotice";
import { db, friendlyError, isSupabaseConfigured } from "@/lib/supabase";
import type { Supplier } from "@/lib/types";

export function PickSupplier() {
  const router = useRouter();
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [supplierId, setSupplierId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!isSupabaseConfigured) {
      setLoading(false);
      return;
    }
    db()
      .from("suppliers")
      .select("*")
      .order("name")
      .then(({ data, error: qError }) => {
        if (qError) setError(friendlyError(qError));
        else setSuppliers((data ?? []) as Supplier[]);
        setLoading(false);
      });
  }, []);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!supplierId) {
      setError("Choose a supplier.");
      return;
    }
    router.push(`/invoice-manager/${supplierId}/new`);
  }

  if (!isSupabaseConfigured) return <SetupNotice />;

  return (
    <div className="space-y-4">
      <DocumentUpload />
      <form onSubmit={onSubmit} className="bs-card max-w-xl">
        <Link href="/invoice-manager" className="text-sm text-[var(--brand)] hover:underline">
          ← Invoice Manager
        </Link>
        <h1 className="mt-2 text-xl font-semibold text-[var(--brand-deep)]">Add invoice</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">
          Select an existing supplier, or{" "}
          <Link href="/invoice-manager/suppliers/new" className="text-[var(--brand)] underline">
            add a new supplier
          </Link>{" "}
          first.
        </p>

        {error ? <p className="banner-error mt-4">{error}</p> : null}

        {loading ? (
          <p className="mt-4 text-[var(--muted)]">Loading suppliers…</p>
        ) : suppliers.length === 0 ? (
          <p className="banner-partial mt-4 text-sm">
            No suppliers in the shared database yet. Add a supplier first (or use purchase-entry).
          </p>
        ) : (
          <label className="mt-5 block">
            <span className="mb-1 block text-sm font-medium">Supplier</span>
            <select
              className="field"
              value={supplierId}
              onChange={(e) => setSupplierId(e.target.value)}
              required
            >
              <option value="">Select…</option>
              {suppliers.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
        )}

        <div className="mt-6 flex gap-3">
          <button
            className="btn btn-primary"
            type="submit"
            disabled={loading || suppliers.length === 0}
          >
            Continue
          </button>
          <Link href="/invoice-manager" className="btn btn-secondary">
            Cancel
          </Link>
        </div>
      </form>
    </div>
  );
}
