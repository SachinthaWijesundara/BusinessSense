"use client";

import { useParams } from "next/navigation";
import { InvoiceForm } from "@/components/InvoiceForm";

export default function EditInvoicePage() {
  const params = useParams<{ supplierId: string; invoiceId: string }>();
  return <InvoiceForm supplierId={params.supplierId} invoiceId={params.invoiceId} />;
}
