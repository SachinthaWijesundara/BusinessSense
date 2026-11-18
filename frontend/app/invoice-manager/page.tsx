/**
 * Invoice Manager screen (proposal): upload photos, search invoices and open one to review.
 */

import { Suspense } from "react";
import { InvoiceList } from "@/components/InvoiceList";
import { PageSkeleton } from "@/components/ui/Skeleton";

/** The invoice list; Suspense is needed because it reads ?status= from the address. */
export default function InvoiceManagerPage() {
  return (
    <Suspense fallback={<PageSkeleton stats={0} />}>
      <InvoiceList />
    </Suspense>
  );
}
