"use client";

import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { useRouter } from "next/navigation";
import { extractDocument, formatExtractError } from "@/lib/extract";
import { saveExtractDraft } from "@/lib/extractDraft";
import { CLAHE_PREVIEW_KEY, REGION_PREVIEW_KEY } from "@/lib/invoiceImage";
import { db, friendlyError, isSupabaseConfigured } from "@/lib/supabase";

const ACCEPT = "image/jpeg,image/png,image/webp,image/gif,.jpg,.jpeg,.png,.webp";
const PREVIEW_KEY = CLAHE_PREVIEW_KEY;
const REGION_ORDER = ["supplier_panel", "header_right", "body", "footer"] as const;
const REGION_LABELS: Record<string, string> = {
  supplier_panel: "Supplier panel",
  header_right: "Header right",
  body: "Body",
  footer: "Footer",
};

type Props = {
  className?: string;
};

function loadRegionPreviews(): Record<string, string> {
  if (typeof window === "undefined") return {};
  try {
    const raw = sessionStorage.getItem(REGION_PREVIEW_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, string>;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

export function DocumentUpload({ className = "" }: Props) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<{ kind: string; text: string } | null>(null);
  const [info, setInfo] = useState("");
  const [previewSrc, setPreviewSrc] = useState<string | null>(() => {
    // #region agent log
    const isServer = typeof window === "undefined";
    const value = isServer ? null : sessionStorage.getItem(PREVIEW_KEY);
    fetch("http://127.0.0.1:7245/ingest/613e9176-0dc9-4d8e-b25a-be23762adf05", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "96d718" },
      body: JSON.stringify({
        sessionId: "96d718",
        runId: "pre-fix",
        hypothesisId: "A",
        location: "DocumentUpload.tsx:previewInit",
        message: "previewSrc useState initializer",
        data: {
          phase: isServer ? "server" : "client",
          hasPreview: Boolean(value),
          previewLen: value?.length ?? 0,
        },
        timestamp: Date.now(),
      }),
    }).catch(() => {});
    // #endregion
    return value;
  });
  const [regionSrcs, setRegionSrcs] = useState<Record<string, string>>(() => {
    const value = loadRegionPreviews();
    // #region agent log
    fetch("http://127.0.0.1:7245/ingest/613e9176-0dc9-4d8e-b25a-be23762adf05", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "96d718" },
      body: JSON.stringify({
        sessionId: "96d718",
        runId: "pre-fix",
        hypothesisId: "B",
        location: "DocumentUpload.tsx:regionInit",
        message: "regionSrcs useState initializer",
        data: {
          phase: typeof window === "undefined" ? "server" : "client",
          regionCount: Object.keys(value).length,
          regionKeys: Object.keys(value),
        },
        timestamp: Date.now(),
      }),
    }).catch(() => {});
    // #endregion
    return value;
  });

  // #region agent log
  useEffect(() => {
    fetch("http://127.0.0.1:7245/ingest/613e9176-0dc9-4d8e-b25a-be23762adf05", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "96d718" },
      body: JSON.stringify({
        sessionId: "96d718",
        runId: "pre-fix",
        hypothesisId: "A",
        location: "DocumentUpload.tsx:mount",
        message: "DocumentUpload mounted after hydrate",
        data: {
          stateHasPreview: Boolean(previewSrc),
          stateRegionCount: Object.keys(regionSrcs).length,
          pathname: window.location.pathname,
        },
        timestamp: Date.now(),
      }),
    }).catch(() => {});
  }, []);
  // #endregion

  function showPreview(
    b64: string | null | undefined,
    regions?: Record<string, string> | null,
  ) {
    if (b64) {
      const src = `data:image/png;base64,${b64}`;
      setPreviewSrc(src);
      try {
        sessionStorage.setItem(PREVIEW_KEY, src);
      } catch {
        // quota — ignore
      }
    }
    if (regions && Object.keys(regions).length > 0) {
      const mapped: Record<string, string> = {};
      for (const [name, value] of Object.entries(regions)) {
        mapped[name] = `data:image/png;base64,${value}`;
      }
      setRegionSrcs(mapped);
      try {
        sessionStorage.setItem(REGION_PREVIEW_KEY, JSON.stringify(mapped));
      } catch {
        // quota — ignore
      }
    }
  }

  const runFile = useCallback(
    async (file: File | null) => {
      if (!file) return;
      if (!file.type.startsWith("image/") && !/\.(jpe?g|png|webp|gif)$/i.test(file.name)) {
        setError({
          kind: "reading_unsuccessful",
          text: "Reading unsuccessful — please drop an image file (jpg, png, or webp).",
        });
        return;
      }
      if (!isSupabaseConfigured) {
        setError({
          kind: "api_error",
          text: "API error — Supabase is not configured. Set env keys or fill forms manually.",
        });
        return;
      }

      setLoading(true);
      setError(null);
      setInfo("");
      try {
        const outcome = await extractDocument(file);
        if (!outcome.ok) {
          showPreview(outcome.failure.preview_png_base64, outcome.failure.region_previews);
          setError({
            kind: outcome.failure.error_type,
            text: formatExtractError(outcome.failure),
          });
          return;
        }

        showPreview(outcome.preview_png_base64, outcome.region_previews);
        const document = outcome.document;
        saveExtractDraft(document, outcome.missing_fields);

        if (document.doc_type === "receipt") {
          setInfo(
            "This photo looks like a receipt. Receipt entry is not in Invoice Manager yet — use purchase-entry, or add an invoice manually.",
          );
          return;
        }

        const name = (document.supplier?.name || "").trim();
        let matchedId: string | null = null;
        if (name) {
          const { data, error: qError } = await db().from("suppliers").select("id, name");
          if (qError) {
            setError({
              kind: "api_error",
              text: `API error — could not look up suppliers: ${friendlyError(qError)}`,
            });
            return;
          }
          const lower = name.toLowerCase();
          const hit = (data ?? []).find(
            (row) => (row.name as string).trim().toLowerCase() === lower,
          );
          if (hit) matchedId = hit.id as string;
        }

        if (matchedId) {
          setInfo(
            outcome.partial
              ? "Matched supplier. Opening invoice form (some fields need completion)…"
              : "Matched existing supplier. Opening invoice form…",
          );
          router.push(`/invoice-manager/${matchedId}/new`);
          return;
        }

        setInfo("No matching supplier — verify supplier details, then Save to continue to the invoice.");
        router.push("/invoice-manager/suppliers/new?fromExtract=1");
      } finally {
        setLoading(false);
      }
    },
    [router],
  );

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    if (loading) return;
    void runFile(e.dataTransfer.files?.[0] ?? null);
  }

  return (
    <div className={className}>
      <div
        className={`dropzone ${dragging ? "dropzone-active" : ""} ${loading ? "dropzone-disabled" : ""}`}
        onDragEnter={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={(e) => {
          e.preventDefault();
          setDragging(false);
        }}
        onDrop={onDrop}
        onClick={() => {
          if (!loading) inputRef.current?.click();
        }}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === " ") && !loading) {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
        aria-disabled={loading}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          className="hidden"
          disabled={loading}
          onChange={(e) => {
            const file = e.target.files?.[0] ?? null;
            e.target.value = "";
            void runFile(file);
          }}
        />
        {loading ? (
          <div className="flex flex-col items-center gap-3 py-2">
            <span className="extract-spinner" aria-hidden />
            <p className="text-sm font-medium text-[var(--brand-deep)]">Reading document…</p>
            <p className="text-xs text-[var(--muted)]">CLAHE preprocess + waiting for the model</p>
          </div>
        ) : (
          <div className="text-center">
            <p className="text-sm font-semibold text-[var(--brand-deep)]">
              Drop invoice photo here
            </p>
            <p className="mt-1 text-xs text-[var(--muted)]">
              or click to browse — jpg, png, webp. After upload you can see the CLAHE-preprocessed image.
            </p>
          </div>
        )}
      </div>

      {previewSrc || Object.keys(regionSrcs).length > 0 ? (
        <div className="bs-card mt-3 !p-3">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm font-semibold text-[var(--brand-deep)]">
              Preprocessed image (CLAHE) + region crops
            </p>
            <div className="flex gap-2">
              {previewSrc ? (
                <a
                  className="btn btn-secondary"
                  href={previewSrc}
                  download="clahe-preview.png"
                  onClick={(e) => e.stopPropagation()}
                >
                  Download PNG
                </a>
              ) : null}
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => {
                  setPreviewSrc(null);
                  setRegionSrcs({});
                  sessionStorage.removeItem(PREVIEW_KEY);
                  sessionStorage.removeItem(REGION_PREVIEW_KEY);
                }}
              >
                Hide
              </button>
            </div>
          </div>
          <p className="mb-2 text-xs text-[var(--muted)]">
            Full page plus zoomed crops sent to the vision model. Also saved under{" "}
            <code className="rounded bg-[#eef3f0] px-1">BusinessSense/backend/debug_previews/</code>.
          </p>
          {previewSrc ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={previewSrc}
              alt="CLAHE preprocessed invoice"
              className="max-h-[420px] w-full rounded border border-[var(--line)] object-contain bg-[#fafcfb]"
            />
          ) : null}
          {Object.keys(regionSrcs).length > 0 ? (
            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
              {REGION_ORDER.filter((name) => regionSrcs[name]).map((name) => (
                <figure key={name} className="min-w-0">
                  <figcaption className="mb-1 text-[11px] font-medium text-[var(--muted)]">
                    {REGION_LABELS[name] ?? name}
                  </figcaption>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={regionSrcs[name]}
                    alt={`${name} crop`}
                    className="h-28 w-full rounded border border-[var(--line)] object-contain bg-[#fafcfb]"
                  />
                </figure>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      {error ? (
        <div className="banner-error mt-3 flex items-start justify-between gap-3">
          <div>
            <p className="text-xs font-bold uppercase tracking-wide">
              {error.kind === "api_error" ? "API error" : "Reading unsuccessful"}
            </p>
            <p className="mt-1 text-sm">{error.text}</p>
          </div>
          <button type="button" className="btn btn-secondary shrink-0" onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      ) : null}

      {info && !error ? <p className="mt-3 text-sm text-[var(--muted)]">{info}</p> : null}
    </div>
  );
}
