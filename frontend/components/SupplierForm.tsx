"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { DocumentUpload } from "@/components/DocumentUpload";
import { SetupNotice } from "@/components/SetupNotice";
import { peekExtractDraft, peekMissingFields } from "@/lib/extractDraft";
import { emptyToNull, toNumber } from "@/lib/money";
import { db, friendlyError, isSupabaseConfigured } from "@/lib/supabase";

export function SupplierForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [name, setName] = useState("");
  const [creditDays, setCreditDays] = useState("");
  const [phone1, setPhone1] = useState("");
  const [phone2, setPhone2] = useState("");
  const [email, setEmail] = useState("");
  const [address, setAddress] = useState("");
  const [related1, setRelated1] = useState("");
  const [related2, setRelated2] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [prefillNote, setPrefillNote] = useState("");
  const [missing, setMissing] = useState<Set<string>>(new Set());

  useEffect(() => {
    const draft = peekExtractDraft();
    const fromExtract = searchParams.get("fromExtract") === "1";
    if (!draft?.supplier && !fromExtract) return;
    if (!draft?.supplier) return;
    if (draft.supplier.name) setName(draft.supplier.name);
    if (draft.supplier.phone1) setPhone1(draft.supplier.phone1);
    if (draft.supplier.address) setAddress(draft.supplier.address);
    const miss = new Set(peekMissingFields());
    if (!draft.supplier.name?.trim()) miss.add("supplier.name");
    setMissing(miss);
    setPrefillNote(
      miss.size > 0
        ? "Partial read — complete highlighted supplier fields, then Save to continue to the invoice."
        : "Prefilling from photo. Verify and Save supplier — then the invoice form opens. Nothing is saved until you press Save.",
    );
  }, [searchParams]);

  function clearMissing(key: string) {
    setMissing((current) => {
      if (!current.has(key)) return current;
      const next = new Set(current);
      next.delete(key);
      return next;
    });
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) {
      setError("Supplier name is required.");
      setMissing((m) => new Set(m).add("supplier.name"));
      return;
    }
    setSaving(true);
    setError("");
    const payload = {
      name: name.trim(),
      credit_days: creditDays.trim() === "" ? null : Math.round(toNumber(creditDays)),
      phone1: emptyToNull(phone1),
      phone2: emptyToNull(phone2),
      email: emptyToNull(email),
      address: emptyToNull(address),
      related_company_1: emptyToNull(related1),
      related_company_2: emptyToNull(related2),
      updated_at: new Date().toISOString(),
    };
    try {
      const { data, error: insertError } = await db()
        .from("suppliers")
        .insert(payload)
        .select("id")
        .single();
      if (insertError) throw insertError;
      const draft = peekExtractDraft();
      if (draft?.doc_type === "invoice") {
        router.push(`/invoice-manager/${data.id}/new`);
      } else {
        router.push("/invoice-manager");
      }
    } catch (err) {
      setError(friendlyError(err));
      setSaving(false);
    }
  }

  if (!isSupabaseConfigured) return <SetupNotice />;

  const nameMissing = missing.has("supplier.name") && !name.trim();

  return (
    <div className="space-y-4">
      <DocumentUpload />
      <form onSubmit={onSubmit} className="bs-card">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <Link href="/invoice-manager" className="text-sm text-[var(--brand)] hover:underline">
              ← Invoice Manager
            </Link>
            <h1 className="mt-1 text-xl font-semibold text-[var(--brand-deep)]">New supplier</h1>
            <p className="mt-1 text-sm text-[var(--muted)]">
              Verify details, then Save before the invoice form.
            </p>
          </div>
        </div>

        {prefillNote ? (
          <p className={`mt-4 text-sm ${missing.size > 0 ? "banner-partial" : "banner-ok"}`}>
            {prefillNote}
          </p>
        ) : null}
        {error ? <p className="banner-error mt-4">{error}</p> : null}

        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          <label className="block sm:col-span-2">
            <span
              className={`mb-1 block text-sm font-medium ${nameMissing ? "label-missing" : ""}`}
            >
              Supplier name
            </span>
            <input
              className={`field ${nameMissing ? "field-missing" : ""}`}
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                clearMissing("supplier.name");
              }}
              required
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-sm font-medium">Credit days</span>
            <input
              className="field"
              inputMode="numeric"
              value={creditDays}
              onChange={(e) => setCreditDays(e.target.value)}
              placeholder="e.g. 15"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-sm font-medium">Email</span>
            <input
              className="field"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-sm font-medium">Phone number 1</span>
            <input
              className="field"
              value={phone1}
              onChange={(e) => setPhone1(e.target.value)}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-sm font-medium">Phone number 2</span>
            <input
              className="field"
              value={phone2}
              onChange={(e) => setPhone2(e.target.value)}
            />
          </label>
          <label className="block sm:col-span-2">
            <span className="mb-1 block text-sm font-medium">Address</span>
            <textarea
              className="field min-h-20"
              value={address}
              onChange={(e) => setAddress(e.target.value)}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-sm font-medium">Related company 1</span>
            <input
              className="field"
              value={related1}
              onChange={(e) => setRelated1(e.target.value)}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-sm font-medium">Related company 2</span>
            <input
              className="field"
              value={related2}
              onChange={(e) => setRelated2(e.target.value)}
            />
          </label>
        </div>

        <div className="mt-6 flex gap-3">
          <button className="btn btn-primary" type="submit" disabled={saving}>
            {saving ? "Saving…" : "Save supplier"}
          </button>
          <Link href="/invoice-manager" className="btn btn-secondary">
            Cancel
          </Link>
        </div>
      </form>
    </div>
  );
}
