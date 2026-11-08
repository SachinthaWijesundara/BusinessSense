"use client";

import { useParams } from "next/navigation";
import { InvoiceForm } from "@/components/InvoiceForm";

export default function NewInvoicePage() {
  const params = useParams<{ supplierId: string }>();
  return <InvoiceForm supplierId={params.supplierId} />;
}
