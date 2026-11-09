/**
 * Invoice Manager screen (proposal): upload photos, search invoices and open one to review.
 */

import { Suspense } from "react";
import { InvoiceList } from "@/components/InvoiceList";

/** The invoice list; Suspense is needed because it reads ?status= from the address. */
export default function InvoiceManagerPage() {
  return (
    <Suspense fallback={<p className="text-[var(--muted)]">Loading…</p>}>
      <InvoiceList />
    </Suspense>
  );
}
