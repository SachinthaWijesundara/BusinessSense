import { Suspense } from "react";
import { SupplierForm } from "@/components/SupplierForm";

export default function NewSupplierPage() {
  return (
    <Suspense fallback={<p className="text-[var(--muted)]">Loading…</p>}>
      <SupplierForm />
    </Suspense>
  );
}
