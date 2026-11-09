"use client";

/**
 * Photo drop zone for the Invoice Manager. Sends the photo to the backend (POST /invoices/upload),
 * which reads it with InvoiceVisionAgent, audits the math, matches products and saves a draft.
 * On success the owner is taken to that draft's review screen.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState, type DragEvent } from "react";
import { ApiError, errorMessage, uploadInvoice } from "@/lib/api";

const ACCEPT = "image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp";

/** What went wrong with an upload, plus an existing invoice to link to for duplicates. */
type UploadProblem = {
  title: string;
  text: string;
  missingFields?: string[];
  existingInvoiceId?: string;
};

/** Turn a failed upload into a message the owner can act on. */
function describeProblem(error: unknown): UploadProblem {
  if (!(error instanceof ApiError)) return { title: "Upload failed", text: errorMessage(error) };
  switch (error.errorType) {
    case "duplicate_invoice":
      return { title: "Already saved", text: error.message, existingInvoiceId: error.invoiceId };
    case "reading_unsuccessful":
      return {
        title: "Could not read this photo",
        text: "Retake the photo (flat, well lit, whole page), or type the invoice in by hand.",
        missingFields: error.missingFields,
      };
    case "network_error":
      return { title: "Backend not reachable", text: error.message };
    default:
      if (error.status === 502) return { title: "Reading service failed", text: error.message };
      return { title: "Upload refused", text: error.message };
  }
}

/** The drop zone, its progress spinner and any upload error. */
export function DocumentUpload({ className = "" }: { className?: string }) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [problem, setProblem] = useState<UploadProblem | null>(null);

  /** Send one photo to the backend and open the saved draft. */
  async function send(file: File | null) {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setProblem({ title: "Not a photo", text: "Choose a JPEG, PNG or WebP image of the invoice." });
      return;
    }
    setUploading(true);
    setProblem(null);
    try {
      const invoice = await uploadInvoice(file);
      router.push(`/invoice-manager/${invoice.id}`);
    } catch (error) {
      setProblem(describeProblem(error));
      setUploading(false);
    }
  }

  /** A photo dropped onto the zone. */
  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    if (!uploading) void send(event.dataTransfer.files?.[0] ?? null);
  }

  return (
    <div className={className}>
      <div
        className={`dropzone ${dragging ? "dropzone-active" : ""} ${uploading ? "dropzone-disabled" : ""}`}
        onDragEnter={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={(event) => {
          event.preventDefault();
          setDragging(false);
        }}
        onDrop={onDrop}
        onClick={() => {
          if (!uploading) inputRef.current?.click();
        }}
        onKeyDown={(event) => {
          if ((event.key === "Enter" || event.key === " ") && !uploading) {
            event.preventDefault();
            inputRef.current?.click();
          }
        }}
        role="button"
        tabIndex={0}
        aria-disabled={uploading}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          className="hidden"
          disabled={uploading}
          onChange={(event) => {
            const file = event.target.files?.[0] ?? null;
            event.target.value = "";
            void send(file);
          }}
        />
        {uploading ? (
          <div className="flex flex-col items-center gap-3 py-2">
            <span className="extract-spinner" aria-hidden />
            <p className="text-sm font-medium text-[var(--brand-deep)]">Reading the invoice…</p>
            <p className="text-xs text-[var(--muted)]">
              The backend is reading the photo, checking the math and matching products.
            </p>
          </div>
        ) : (
          <div className="text-center">
            <p className="text-sm font-semibold text-[var(--brand-deep)]">Drop an invoice photo here</p>
            <p className="mt-1 text-xs text-[var(--muted)]">
              or click to choose one (JPEG, PNG or WebP). It is saved as a draft for you to review.
            </p>
          </div>
        )}
      </div>

      {problem ? (
        <div className="banner-error mt-3 flex items-start justify-between gap-3">
          <div>
            <p className="text-xs font-bold uppercase tracking-wide">{problem.title}</p>
            <p className="mt-1 text-sm">{problem.text}</p>
            {problem.missingFields && problem.missingFields.length > 0 ? (
              <p className="mt-1 text-sm">Not found on the photo: {problem.missingFields.join(", ")}</p>
            ) : null}
            {problem.existingInvoiceId ? (
              <Link
                href={`/invoice-manager/${problem.existingInvoiceId}`}
                className="mt-1 inline-block text-sm font-medium underline"
              >
                Open the saved invoice
              </Link>
            ) : null}
          </div>
          <button type="button" className="btn btn-secondary shrink-0" onClick={() => setProblem(null)}>
            Dismiss
          </button>
        </div>
      ) : null}
    </div>
  );
}
