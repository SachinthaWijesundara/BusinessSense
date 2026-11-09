"use client";

/**
 * Add-supplier form. Sends the details to the backend (POST /suppliers) and hands the saved
 * supplier back to the screen that opened it (Supplier Profile, or the review screen when a
 * photo's supplier is not recognised).
 */

import { useState, type FormEvent } from "react";
import { createSupplier, errorMessage } from "@/lib/api";
import { parseNumber, textOrNull } from "@/lib/money";
import type { Supplier } from "@/lib/types";

type Props = {
  initialName?: string;
  onCreated: (supplier: Supplier) => void;
  onCancel?: () => void;
};

/** The supplier fields and a Save button. */
export function SupplierForm({ initialName = "", onCreated, onCancel }: Props) {
  const [name, setName] = useState(initialName);
  const [creditDays, setCreditDays] = useState("");
  const [phone1, setPhone1] = useState("");
  const [phone2, setPhone2] = useState("");
  const [email, setEmail] = useState("");
  const [address, setAddress] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  /** Check the fields, save the supplier through the backend and report it to the parent. */
  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const days = parseNumber(creditDays);
    if (!name.trim()) {
      setError("Enter the supplier name.");
      return;
    }
    if (days !== null && (Number.isNaN(days) || days < 0 || !Number.isInteger(days))) {
      setError("Credit days must be a whole number (0 or more).");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const supplier = await createSupplier({
        name: name.trim(),
        credit_days: days,
        phone1: textOrNull(phone1),
        phone2: textOrNull(phone2),
        email: textOrNull(email),
        address: textOrNull(address),
      });
      onCreated(supplier);
    } catch (err) {
      setError(errorMessage(err));
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      {error ? <p className="banner-error">{error}</p> : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block sm:col-span-2">
          <span className="mb-1 block text-sm font-medium">Supplier name</span>
          <input className="field" value={name} onChange={(event) => setName(event.target.value)} />
        </label>
        <label className="block">
          <span className="mb-1 block text-sm font-medium">Usual credit days</span>
          <input
            className="field"
            inputMode="numeric"
            placeholder="e.g. 30"
            value={creditDays}
            onChange={(event) => setCreditDays(event.target.value)}
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-sm font-medium">Email</span>
          <input className="field" type="email" value={email} onChange={(event) => setEmail(event.target.value)} />
        </label>
        <label className="block">
          <span className="mb-1 block text-sm font-medium">Phone 1</span>
          <input className="field" value={phone1} onChange={(event) => setPhone1(event.target.value)} />
        </label>
        <label className="block">
          <span className="mb-1 block text-sm font-medium">Phone 2</span>
          <input className="field" value={phone2} onChange={(event) => setPhone2(event.target.value)} />
        </label>
        <label className="block sm:col-span-2">
          <span className="mb-1 block text-sm font-medium">Address</span>
          <textarea className="field min-h-16" value={address} onChange={(event) => setAddress(event.target.value)} />
        </label>
      </div>
      <div className="flex gap-3">
        <button className="btn btn-primary" type="submit" disabled={saving}>
          {saving ? "Saving…" : "Save supplier"}
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
