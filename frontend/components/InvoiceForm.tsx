"use client";

/**
 * Editable invoice header and item lines, shared by "type in an invoice" (create) and the review
 * screen (corrections). The owner types every value as printed on the paper, including line totals
 * and the invoice total; nothing is calculated here. The backend's math audit checks the numbers.
 * When an invoice is given, fields the backend flagged are highlighted.
 */

import { useState, type FormEvent } from "react";
import { numberText, parseNumber, textOrNull } from "@/lib/money";
import type { Invoice, InvoiceHeaderInput, LineInput, PaymentType } from "@/lib/types";

/** One line as typed in the table (text boxes hold strings until the form is sent). */
type LineRow = {
  key: string;
  id?: string;
  sku: string;
  item_name: string;
  quantity: string;
  mro: string;
  discount: string;
  line_total: string;
};

/** The header and lines the form hands to its owner screen when saved. */
export type InvoiceFormValues = {
  header: Omit<InvoiceHeaderInput, "supplier_id">;
  lines: LineInput[];
};

type Props = {
  invoice?: Invoice;
  submitLabel: string;
  saving: boolean;
  onSubmit: (values: InvoiceFormValues) => void;
};

let keyCounter = 0;

/** A unique React key for a new table row. */
function newKey(): string {
  keyCounter += 1;
  return `row-${keyCounter}`;
}

/** An empty line row. */
function blankRow(): LineRow {
  return { key: newKey(), sku: "", item_name: "", quantity: "", mro: "", discount: "", line_total: "" };
}

/** Line rows filled from a saved invoice (or one empty row). */
function rowsFrom(invoice?: Invoice): LineRow[] {
  if (!invoice || invoice.lines.length === 0) return [blankRow()];
  return invoice.lines.map((line) => ({
    key: line.id,
    id: line.id,
    sku: line.sku ?? "",
    item_name: line.item_name,
    quantity: numberText(line.quantity),
    mro: numberText(line.mro),
    discount: line.discount ? String(line.discount) : "",
    line_total: numberText(line.line_total),
  }));
}

/** CSS class for a header field: red when missing or wrong, amber when the agent was unsure. */
function fieldClass(invoice: Invoice | undefined, field: string): string {
  if (!invoice) return "field";
  const wrong =
    invoice.missing_fields.includes(field) ||
    invoice.field_issues.some((issue) => issue.field === field) ||
    invoice.math_issues.some((issue) => issue.field === field);
  if (wrong) return "field field-missing";
  if (invoice.low_confidence_fields.includes(`invoice.${field}`)) return "field field-unsure";
  return "field";
}

/** CSS class for a line cell: amber when the agent was unsure of that value. */
function cellClass(invoice: Invoice | undefined, index: number, field: string): string {
  const path = `invoice.lines[${index}].${field}`;
  return invoice?.low_confidence_fields.includes(path) ? "cell-input field-unsure" : "cell-input";
}

/** The form: invoice number, dates, payment type, discounts, total and the line table. */
export function InvoiceForm({ invoice, submitLabel, saving, onSubmit }: Props) {
  const [invoiceNumber, setInvoiceNumber] = useState(invoice?.invoice_number ?? "");
  const [invoiceDate, setInvoiceDate] = useState(invoice?.invoice_date ?? "");
  const [deliveryDate, setDeliveryDate] = useState(invoice?.delivery_date ?? "");
  const [paymentType, setPaymentType] = useState<string>(invoice?.payment_type ?? "");
  const [creditDays, setCreditDays] = useState(numberText(invoice?.credit_days));
  const [specialDiscount, setSpecialDiscount] = useState(
    invoice?.special_discount ? String(invoice.special_discount) : "",
  );
  const [tax, setTax] = useState(invoice?.tax ? String(invoice.tax) : "");
  const [total, setTotal] = useState(numberText(invoice?.total));
  const [rows, setRows] = useState<LineRow[]>(() => rowsFrom(invoice));
  const [formError, setFormError] = useState("");

  const flaggedLines = new Set(
    (invoice?.math_issues ?? [])
      .map((issue) => issue.line_index)
      .filter((index): index is number => index !== undefined),
  );

  /** Change one cell of one row. */
  function updateRow(key: string, patch: Partial<LineRow>) {
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  }

  /** Check the typed text is readable, then hand the values to the screen that saves them. */
  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const filled = rows.filter((row) => row.item_name.trim());
    if (filled.length === 0) {
      setFormError("Add at least one item line.");
      return;
    }
    const numbers: Record<string, number | null> = {
      "credit days": parseNumber(creditDays),
      "special discount": parseNumber(specialDiscount),
      tax: parseNumber(tax),
      "invoice total": parseNumber(total),
    };
    const lines: LineInput[] = [];
    for (const [index, row] of filled.entries()) {
      const line: LineInput = {
        ...(row.id ? { id: row.id } : {}),
        sku: textOrNull(row.sku),
        item_name: row.item_name.trim(),
        quantity: parseNumber(row.quantity),
        mro: parseNumber(row.mro),
        discount: parseNumber(row.discount),
        line_total: parseNumber(row.line_total),
      };
      for (const field of ["quantity", "mro", "discount", "line_total"] as const) {
        numbers[`line ${index + 1} ${field.replace("_", " ")}`] = line[field] ?? null;
      }
      lines.push(line);
    }
    const unreadable = Object.entries(numbers)
      .filter(([, value]) => value !== null && Number.isNaN(value))
      .map(([name]) => name);
    if (unreadable.length > 0) {
      setFormError(`These are not numbers: ${unreadable.join(", ")}.`);
      return;
    }
    setFormError("");
    onSubmit({
      header: {
        invoice_number: textOrNull(invoiceNumber),
        invoice_date: textOrNull(invoiceDate),
        delivery_date: textOrNull(deliveryDate),
        payment_type: (paymentType || null) as PaymentType | null,
        credit_days: numbers["credit days"],
        special_discount: numbers["special discount"],
        tax: numbers.tax,
        total: numbers["invoice total"],
      },
      lines,
    });
  }

  return (
    <form onSubmit={handleSubmit} className="bs-card overflow-hidden !p-0">
      <div className="grid gap-4 px-5 py-4 sm:grid-cols-4">
        <label className="block">
          <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Invoice number
          </span>
          <input
            className={fieldClass(invoice, "invoice_number")}
            value={invoiceNumber}
            onChange={(event) => setInvoiceNumber(event.target.value)}
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Invoice date
          </span>
          <input
            className={fieldClass(invoice, "invoice_date")}
            type="date"
            value={invoiceDate}
            onChange={(event) => setInvoiceDate(event.target.value)}
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Delivery date
          </span>
          <input
            className={fieldClass(invoice, "delivery_date")}
            type="date"
            value={deliveryDate}
            onChange={(event) => setDeliveryDate(event.target.value)}
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Payment
          </span>
          <select
            className={fieldClass(invoice, "payment_type")}
            value={paymentType}
            onChange={(event) => setPaymentType(event.target.value)}
          >
            <option value="">Not printed</option>
            <option value="cash">Cash</option>
            <option value="credit">Credit</option>
          </select>
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Credit days (if printed)
          </span>
          <input
            className={fieldClass(invoice, "credit_days")}
            inputMode="numeric"
            value={creditDays}
            onChange={(event) => setCreditDays(event.target.value)}
          />
        </label>
      </div>

      <div className="overflow-x-auto px-5 pb-2">
        <table className="entry-table">
          <thead>
            <tr>
              <th className="w-28">Item code</th>
              <th>Description (as printed)</th>
              <th className="w-24">Qty</th>
              <th className="w-28">Rate</th>
              <th className="w-28">Discount</th>
              <th className="w-32">Line total</th>
              <th className="w-10"></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={row.key} className={flaggedLines.has(index) ? "row-issue" : undefined}>
                <td>
                  <input
                    className={cellClass(invoice, index, "sku")}
                    value={row.sku}
                    onChange={(event) => updateRow(row.key, { sku: event.target.value })}
                  />
                </td>
                <td>
                  <input
                    className={cellClass(invoice, index, "item_name")}
                    value={row.item_name}
                    onChange={(event) => updateRow(row.key, { item_name: event.target.value })}
                  />
                </td>
                <td>
                  <input
                    className={`${cellClass(invoice, index, "quantity")} text-right`}
                    inputMode="decimal"
                    value={row.quantity}
                    onChange={(event) => updateRow(row.key, { quantity: event.target.value })}
                  />
                </td>
                <td>
                  <input
                    className={`${cellClass(invoice, index, "mro")} text-right`}
                    inputMode="decimal"
                    value={row.mro}
                    onChange={(event) => updateRow(row.key, { mro: event.target.value })}
                  />
                </td>
                <td>
                  <input
                    className={`${cellClass(invoice, index, "discount")} text-right`}
                    inputMode="decimal"
                    placeholder="0"
                    value={row.discount}
                    onChange={(event) => updateRow(row.key, { discount: event.target.value })}
                  />
                </td>
                <td>
                  <input
                    className={`${cellClass(invoice, index, "line_total")} text-right`}
                    inputMode="decimal"
                    value={row.line_total}
                    onChange={(event) => updateRow(row.key, { line_total: event.target.value })}
                  />
                </td>
                <td className="text-center">
                  <button
                    type="button"
                    className="px-2 text-[var(--muted)] hover:text-red-700"
                    aria-label="Remove line"
                    onClick={() =>
                      setRows((current) =>
                        current.length === 1 ? [blankRow()] : current.filter((item) => item.key !== row.key),
                      )
                    }
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
                Special discount
              </td>
              <td>
                <input
                  className={`${fieldClass(invoice, "special_discount").replace("field", "cell-input")} text-right`}
                  inputMode="decimal"
                  placeholder="0"
                  value={specialDiscount}
                  onChange={(event) => setSpecialDiscount(event.target.value)}
                />
              </td>
              <td></td>
            </tr>
            <tr>
              <td colSpan={5} className="px-3 py-2 text-right font-medium">
                Tax
              </td>
              <td>
                <input
                  className={`${fieldClass(invoice, "tax").replace("field", "cell-input")} text-right`}
                  inputMode="decimal"
                  placeholder="0"
                  value={tax}
                  onChange={(event) => setTax(event.target.value)}
                />
              </td>
              <td></td>
            </tr>
            <tr>
              <td colSpan={5} className="px-3 py-2 text-right">
                Invoice total (as printed)
              </td>
              <td>
                <input
                  className={`${fieldClass(invoice, "total").replace("field", "cell-input")} text-right`}
                  inputMode="decimal"
                  value={total}
                  onChange={(event) => setTotal(event.target.value)}
                />
              </td>
              <td></td>
            </tr>
          </tfoot>
        </table>
        <button
          type="button"
          className="btn btn-secondary mt-3"
          onClick={() => setRows((current) => [...current, blankRow()])}
        >
          Add item line
        </button>
      </div>

      {formError ? <p className="banner-error mx-5 mb-2">{formError}</p> : null}

      <div className="flex flex-wrap gap-3 border-t border-[var(--line)] px-5 py-4">
        <button className="btn btn-primary" type="submit" disabled={saving}>
          {saving ? "Saving…" : submitLabel}
        </button>
      </div>
    </form>
  );
}
