"use client";

/**
 * Review screen for one invoice, at /invoice-manager/<invoice id>.
 */

import { useParams } from "next/navigation";
import { InvoiceReview } from "@/components/InvoiceReview";

/** Read the invoice id from the address and show its review screen. */
export default function InvoiceReviewPage() {
  const params = useParams<{ invoiceId: string }>();
  return <InvoiceReview key={params.invoiceId} invoiceId={params.invoiceId} />;
}
