"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { DocumentUpload } from "@/components/DocumentUpload";
import { ItemAutocomplete } from "@/components/ItemAutocomplete";
import { SetupNotice } from "@/components/SetupNotice";
import { clearExtractDraft, peekExtractDraft, peekMissingFields } from "@/lib/extractDraft";
import {
  clearInvoiceImageSession,
  uploadPendingClaheImage,
} from "@/lib/invoiceImage";
import {
  emptyToNull,
  lineTotal,
  money,
  newKey,
  round2,
  round3,
  toNumber,
} from "@/lib/money";
import { db, friendlyError, isSupabaseConfigured } from "@/lib/supabase";
import type { InvoiceLineDraft, Item, Supplier } from "@/lib/types";
import { useItems } from "@/lib/useItems";

function blankLine(): InvoiceLineDraft {
  const key = newKey();
  // #region agent log
  fetch("http://127.0.0.1:7245/ingest/613e9176-0dc9-4d8e-b25a-be23762adf05", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "96d718" },
    body: JSON.stringify({
      sessionId: "96d718",
      runId: "pre-fix",
      hypothesisId: "C",
      location: "InvoiceForm.tsx:blankLine",
      message: "blankLine generated unstable key",
      data: {
        phase: typeof window === "undefined" ? "server" : "client",
        key,
      },
      timestamp: Date.now(),
    }),
  }).catch(() => {});
  // #endregion
  return {
    key,
    sku: "",
    item_name: "",
    quantity: "",
    mro: "",
    discount: "",
    line_total: "",
  };
}

type Props = {
  supplierId: string;
  invoiceId?: string;
};

export function InvoiceForm({ supplierId, invoiceId }: Props) {
  const router = useRouter();
  const { items, remember } = useItems();
  const [supplier, setSupplier] = useState<Supplier | null>(null);
  const [invoiceNumber, setInvoiceNumber] = useState("");
  const [invoiceDate, setInvoiceDate] = useState("");
  const [deliveryDate, setDeliveryDate] = useState("");
  const [specialDiscount, setSpecialDiscount] = useState("");
  const [lines, setLines] = useState<InvoiceLineDraft[]>([blankLine()]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [prefillNote, setPrefillNote] = useState("");
  const [missing, setMissing] = useState<Set<string>>(new Set());
  const [imageUrl, setImageUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!isSupabaseConfigured || !supplierId) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    async function load() {
      const { data: sup, error: supError } = await db()
        .from("suppliers")
        .select("*")
        .eq("id", supplierId)
        .single();
      if (cancelled) return;
      if (supError) {
        setError(friendlyError(supError));
        setLoading(false);
        return;
      }
      setSupplier(sup as Supplier);

      if (!invoiceId) {
        const draft = peekExtractDraft();
        const miss = new Set(peekMissingFields());
        if (draft?.doc_type === "invoice" && draft.invoice) {
          const inv = draft.invoice;
          setInvoiceNumber(inv.invoice_number?.trim() || "");
          setInvoiceDate(inv.invoice_date?.trim() || "");
          setDeliveryDate(inv.delivery_date?.trim() || "");
          setSpecialDiscount(
            inv.special_discount == null || Number(inv.special_discount) === 0
              ? ""
              : String(inv.special_discount),
          );
          const mapped =
            (inv.lines ?? [])
              .filter((line) => line.item_name && String(line.item_name).trim())
              .map((line) => ({
                key: newKey(),
                sku: line.sku?.trim() || "",
                item_name: line.item_name?.trim() || "",
                quantity: line.quantity == null ? "" : String(line.quantity),
                mro: line.mro == null ? "" : String(line.mro),
                discount: line.discount == null ? "" : String(line.discount),
                line_total: line.line_total == null ? "" : String(line.line_total),
              })) ?? [];
          setLines(mapped.length > 0 ? mapped : [blankLine()]);
          if (!inv.invoice_number?.trim()) miss.add("invoice.invoice_number");
          if (!inv.invoice_date?.trim()) miss.add("invoice.invoice_date");
          if (mapped.length === 0) miss.add("invoice.lines");
          setMissing(miss);
          setPrefillNote(
            miss.size > 0
              ? "Partial read — complete the highlighted fields, then Save invoice."
              : "Prefilling from photo. Review — nothing is saved until you press Save invoice.",
          );
          clearExtractDraft();
        } else if (draft?.doc_type === "receipt") {
          setPrefillNote(
            "This photo was read as a receipt. Receipt UI is not here yet — fill the invoice manually or use purchase-entry.",
          );
        }
        setLoading(false);
        return;
      }

      const [{ data: inv, error: invError }, { data: itemRows, error: itemError }] =
        await Promise.all([
          db().from("invoices").select("*").eq("id", invoiceId).single(),
          db()
            .from("invoice_items")
            .select("*")
            .eq("invoice_id", invoiceId)
            .order("sort_order"),
        ]);
      if (cancelled) return;
      if (invError) {
        setError(friendlyError(invError));
        setLoading(false);
        return;
      }
      if (itemError) setError(friendlyError(itemError));
      setInvoiceNumber(inv.invoice_number ?? "");
      setInvoiceDate(inv.invoice_date ?? "");
      setDeliveryDate(inv.delivery_date ?? "");
      setImageUrl((inv.image_url as string | null) ?? null);
      setSpecialDiscount(
        inv.special_discount == null || Number(inv.special_discount) === 0
          ? ""
          : String(inv.special_discount),
      );
      const loaded = (itemRows ?? []).map((row) => ({
        key: row.id as string,
        sku: (row.sku as string) ?? "",
        item_name: (row.item_name as string) ?? "",
        quantity: row.quantity == null ? "" : String(row.quantity),
        mro: row.mro == null ? "" : String(row.mro),
        discount: row.discount == null ? "" : String(row.discount),
        line_total: row.line_total == null ? "" : String(row.line_total),
      }));
      setLines(loaded.length > 0 ? loaded : [blankLine()]);
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
  }, [invoiceId, supplierId]);

  const itemsSubtotal = useMemo(
    () => round2(lines.reduce((sum, line) => sum + toNumber(line.line_total), 0)),
    [lines],
  );
  const invoiceTotal = round2(itemsSubtotal - toNumber(specialDiscount));

  function clearMissing(key: string) {
    setMissing((current) => {
      if (!current.has(key)) return current;
      const next = new Set(current);
      next.delete(key);
      return next;
    });
  }

  function updateLine(key: string, patch: Partial<InvoiceLineDraft>, recalc = false) {
    setLines((current) =>
      current.map((line) => {
        if (line.key !== key) return line;
        const next = { ...line, ...patch };
        if (recalc) {
          next.line_total = String(
            lineTotal(toNumber(next.quantity), toNumber(next.mro), toNumber(next.discount)),
          );
        }
        return next;
      }),
    );
    if (patch.item_name !== undefined) clearMissing("invoice.lines");
  }

  function pickItem(key: string, item: Item) {
    updateLine(key, { item_name: item.name, sku: item.sku ?? "" }, false);
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!supplier) return;
    if (!invoiceNumber.trim()) {
      setError("Invoice number is required.");
      setMissing((m) => new Set(m).add("invoice.invoice_number"));
      return;
    }
    const filled = lines.filter((line) => line.item_name.trim());
    if (filled.length === 0) {
      setError("Add at least one item with a name.");
      setMissing((m) => new Set(m).add("invoice.lines"));
      return;
    }
    setSaving(true);
    setError("");
    try {
      const header = {
        supplier_id: supplier.id,
        invoice_number: invoiceNumber.trim(),
        invoice_date: emptyToNull(invoiceDate),
        delivery_date: emptyToNull(deliveryDate),
        special_discount: round2(toNumber(specialDiscount)),
        total: invoiceTotal,
        updated_at: new Date().toISOString(),
      };
      let savedId = invoiceId;
      if (invoiceId) {
        const { error: updateError } = await db()
          .from("invoices")
          .update(header)
          .eq("id", invoiceId);
        if (updateError) throw updateError;
        const { error: deleteError } = await db()
          .from("invoice_items")
          .delete()
          .eq("invoice_id", invoiceId);
        if (deleteError) throw deleteError;
      } else {
        const { data, error: insertError } = await db()
          .from("invoices")
          .insert(header)
          .select("id")
          .single();
        if (insertError) throw insertError;
        savedId = data.id as string;
      }
      const rows = filled.map((line, index) => ({
        invoice_id: savedId,
        sku: emptyToNull(line.sku),
        item_name: line.item_name.trim(),
        quantity: round3(toNumber(line.quantity)),
        mro: round2(toNumber(line.mro)),
        discount: round2(toNumber(line.discount)),
        line_total: round2(toNumber(line.line_total)),
        sort_order: index,
      }));
      const { error: itemsError } = await db().from("invoice_items").insert(rows);
      if (itemsError) throw itemsError;

      if (savedId) {
        const publicUrl = await uploadPendingClaheImage(supplier.id, savedId);
        if (publicUrl) {
          const { error: imageError } = await db()
            .from("invoices")
            .update({ image_url: publicUrl, updated_at: new Date().toISOString() })
            .eq("id", savedId);
          if (imageError) throw imageError;
          setImageUrl(publicUrl);
        }
      }

      for (const line of filled) {
        await remember(line.item_name, line.sku);
      }
      clearExtractDraft();
      clearInvoiceImageSession();
      router.push("/invoice-manager");
    } catch (err) {
      setError(friendlyError(err));
      setSaving(false);
    }
  }

  async function removeInvoice() {
    if (!invoiceId) return;
    if (!confirm("Delete this invoice and its item lines?")) return;
    setSaving(true);
    const { error: delError } = await db().from("invoices").delete().eq("id", invoiceId);
    if (delError) {
      setError(friendlyError(delError));
      setSaving(false);
      return;
    }
    router.push("/invoice-manager");
  }

  if (!isSupabaseConfigured) return <SetupNotice />;
  if (loading) return <p className="text-[var(--muted)]">Loading invoice…</p>;
  if (!supplier) return <p>Supplier not found.</p>;

  const numMissing = missing.has("invoice.invoice_number") && !invoiceNumber.trim();
  const dateMissing = missing.has("invoice.invoice_date") && !invoiceDate.trim();
  const linesMissing =
    missing.has("invoice.lines") && !lines.some((line) => line.item_name.trim());

  return (
    <div className="space-y-4">
      {!invoiceId ? <DocumentUpload /> : null}
      {imageUrl ? (
        <div className="bs-card !p-3">
          <p className="mb-2 text-sm font-semibold text-[var(--brand-deep)]">Saved invoice image</p>
          <a href={imageUrl} target="_blank" rel="noreferrer" className="block">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={imageUrl}
              alt="Saved CLAHE invoice"
              className="max-h-48 w-full rounded border border-[var(--line)] object-contain bg-[#fafcfb]"
            />
          </a>
        </div>
      ) : null}
      <form onSubmit={onSubmit} className="bs-card overflow-hidden !p-0">
        <div className="border-b-2 border-[var(--brand-deep)] bg-[var(--brand-deep)] px-5 py-3 text-white">
          <Link href="/invoice-manager" className="text-xs text-[#c5e0d2] hover:underline">
            ← Invoice Manager · {supplier.name}
          </Link>
          <h1 className="text-lg font-semibold tracking-wide">PURCHASE INVOICE</h1>
        </div>

        <div className="grid gap-4 px-5 py-4 sm:grid-cols-3">
          <label className="block">
            <span
              className={`mb-1 block text-xs font-semibold uppercase tracking-wide ${
                numMissing ? "label-missing" : "text-[var(--muted)]"
              }`}
            >
              Invoice number
            </span>
            <input
              className={`field ${numMissing ? "field-missing" : ""}`}
              value={invoiceNumber}
              onChange={(e) => {
                setInvoiceNumber(e.target.value);
                clearMissing("invoice.invoice_number");
              }}
              required
            />
          </label>
          <label className="block">
            <span
              className={`mb-1 block text-xs font-semibold uppercase tracking-wide ${
                dateMissing ? "label-missing" : "text-[var(--muted)]"
              }`}
            >
              Invoice date
            </span>
            <input
              className={`field ${dateMissing ? "field-missing" : ""}`}
              type="date"
              value={invoiceDate}
              onChange={(e) => {
                setInvoiceDate(e.target.value);
                clearMissing("invoice.invoice_date");
              }}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
              Delivery date
            </span>
            <input
              className="field"
              type="date"
              value={deliveryDate}
              onChange={(e) => setDeliveryDate(e.target.value)}
            />
          </label>
        </div>

        {prefillNote ? (
          <p className={`mx-5 mb-4 text-sm ${missing.size > 0 ? "banner-partial" : "banner-ok"}`}>
            {prefillNote}
          </p>
        ) : null}
        {error ? <p className="banner-error mx-5 mb-4">{error}</p> : null}

        <div className={`overflow-x-auto px-5 pb-2 ${linesMissing ? "rounded ring-2 ring-red-300" : ""}`}>
          <table className="entry-table">
            <thead>
              <tr>
                <th className="w-28">Item SKU</th>
                <th>Item name</th>
                <th className="w-24">Qty</th>
                <th className="w-28">MRO</th>
                <th className="w-28">Discount</th>
                <th className="w-32">Total</th>
                <th className="w-10"></th>
              </tr>
            </thead>
            <tbody>
              {lines.map((line) => (
                <tr key={line.key}>
                  <td>
                    <ItemAutocomplete
                      kind="sku"
                      value={line.sku}
                      items={items}
                      placeholder="SKU"
                      onChange={(value) => updateLine(line.key, { sku: value })}
                      onPick={(item) => pickItem(line.key, item)}
                    />
                  </td>
                  <td>
                    <ItemAutocomplete
                      kind="name"
                      value={line.item_name}
                      items={items}
                      placeholder="Item name"
                      className={
                        linesMissing && !line.item_name.trim()
                          ? "cell-input field-missing"
                          : "cell-input"
                      }
                      onChange={(value) => updateLine(line.key, { item_name: value })}
                      onPick={(item) => pickItem(line.key, item)}
                    />
                  </td>
                  <td>
                    <input
                      className="cell-input text-right"
                      inputMode="decimal"
                      value={line.quantity}
                      onChange={(e) =>
                        updateLine(line.key, { quantity: e.target.value }, true)
                      }
                    />
                  </td>
                  <td>
                    <input
                      className="cell-input text-right"
                      inputMode="decimal"
                      value={line.mro}
                      onChange={(e) => updateLine(line.key, { mro: e.target.value }, true)}
                    />
                  </td>
                  <td>
                    <input
                      className="cell-input text-right"
                      inputMode="decimal"
                      value={line.discount}
                      onChange={(e) =>
                        updateLine(line.key, { discount: e.target.value }, true)
                      }
                    />
                  </td>
                  <td>
                    <input
                      className="cell-input field-readonly text-right"
                      readOnly
                      value={line.line_total === "" ? "" : money(toNumber(line.line_total))}
                      tabIndex={-1}
                    />
                  </td>
                  <td className="text-center">
                    <button
                      type="button"
                      className="px-2 text-[var(--muted)] hover:text-red-700"
                      onClick={() =>
                        setLines((current) =>
                          current.length === 1
                            ? [blankLine()]
                            : current.filter((row) => row.key !== line.key),
                        )
                      }
                      aria-label="Remove row"
                    >
                      ×
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td colSpan={5} className="px-3 py-2 text-right font-medium">
                  Items subtotal
                </td>
                <td className="px-3 py-2 text-right">{money(itemsSubtotal)}</td>
                <td></td>
              </tr>
              <tr>
                <td colSpan={5} className="px-3 py-2 text-right font-medium">
                  Special / bulk discount
                </td>
                <td>
                  <input
                    className="cell-input text-right"
                    inputMode="decimal"
                    placeholder="0.00"
                    value={specialDiscount}
                    onChange={(e) => setSpecialDiscount(e.target.value)}
                  />
                </td>
                <td></td>
              </tr>
              <tr>
                <td colSpan={5} className="px-3 py-2 text-right">
                  Invoice total
                </td>
                <td className="px-3 py-2 text-right">{money(invoiceTotal)}</td>
                <td></td>
              </tr>
            </tfoot>
          </table>
          <button
            type="button"
            className="btn btn-secondary mt-3"
            onClick={() => setLines((current) => [...current, blankLine()])}
          >
            Add item row
          </button>
        </div>

        <div className="flex flex-wrap gap-3 border-t border-[var(--line)] px-5 py-4">
          <button className="btn btn-primary" type="submit" disabled={saving}>
            {saving ? "Saving…" : "Save invoice"}
          </button>
          <Link href="/invoice-manager" className="btn btn-secondary">
            Cancel
          </Link>
          {invoiceId ? (
            <button
              type="button"
              className="btn btn-danger ml-auto"
              disabled={saving}
              onClick={removeInvoice}
            >
              Delete invoice
            </button>
          ) : null}
        </div>
      </form>
    </div>
  );
}
