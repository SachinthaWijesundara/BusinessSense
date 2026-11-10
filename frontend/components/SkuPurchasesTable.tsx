"use client";

/**
 * Purchases grouped by SKU for a period: quantity, average unit price, and spend.
 */

import { money } from "@/lib/money";
import type { SkuPurchase } from "@/lib/supplierMetrics";

type Props = {
  rows: SkuPurchase[];
  emptyText?: string;
};

/** Period-scoped SKU rollup table. */
export function SkuPurchasesTable({
  rows,
  emptyText = "No purchases in this period.",
}: Props) {
  if (rows.length === 0) {
    return <p className="text-sm text-[var(--muted)]">{emptyText}</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="entry-table">
        <thead>
          <tr>
            <th>SKU / item</th>
            <th className="text-right">Qty</th>
            <th className="text-right">Avg price</th>
            <th className="text-right">Spend</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <td className="px-2 py-2">
                <div className="font-medium">{row.name}</div>
                {row.product_id ? (
                  <div className="text-xs text-[var(--muted)]">{row.product_id.slice(0, 8)}…</div>
                ) : null}
              </td>
              <td className="px-2 py-2 text-right">{row.quantity || "—"}</td>
              <td className="px-2 py-2 text-right">{money(row.avgPrice)}</td>
              <td className="px-2 py-2 text-right">{money(row.spend)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
