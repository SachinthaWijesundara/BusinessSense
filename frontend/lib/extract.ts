import { extractApiBase } from "./supabase";
import type {
  ExtractedDocument,
  ExtractFailure,
  ExtractOutcome,
} from "./extractTypes";

export async function extractDocument(file: File): Promise<ExtractOutcome> {
  const url = `${extractApiBase()}/extract`;
  let response: Response;
  try {
    const body = new FormData();
    body.append("file", file);
    response = await fetch(url, { method: "POST", body });
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    return {
      ok: false,
      failure: {
        error_type: "api_error",
        message: `API error — could not reach the extract service. Check connection or try again. (${detail})`,
      },
    };
  }

  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    return { ok: false, failure: normalizeFailure(payload, response.status) };
  }

  return normalizeSuccess(payload);
}

function previewFrom(payload: Record<string, unknown>): string | null {
  const value = payload.preview_png_base64;
  return typeof value === "string" && value.length > 0 ? value : null;
}

function regionPreviewsFrom(payload: Record<string, unknown>): Record<string, string> | undefined {
  const raw = payload.region_previews;
  if (!raw || typeof raw !== "object") return undefined;
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof value === "string" && value.length > 0) out[key] = value;
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

function normalizeSuccess(payload: unknown): ExtractOutcome {
  if (!payload || typeof payload !== "object") {
    return {
      ok: false,
      failure: {
        error_type: "reading_unsuccessful",
        message: "Reading unsuccessful — empty extract response.",
      },
    };
  }
  const obj = payload as Record<string, unknown>;
  const missing = Array.isArray(obj.missing_fields)
    ? (obj.missing_fields as string[])
    : [];
  const partial = Boolean(obj.partial) || missing.length > 0;
  const preview = previewFrom(obj);
  const region_previews = regionPreviewsFrom(obj);

  let document: ExtractedDocument | null = null;
  if (obj.document && typeof obj.document === "object") {
    document = obj.document as ExtractedDocument;
  } else if (obj.doc_type === "invoice" || obj.doc_type === "receipt") {
    document = obj as unknown as ExtractedDocument;
  }

  if (!document) {
    return {
      ok: false,
      failure: {
        error_type: "reading_unsuccessful",
        message: "Reading unsuccessful — could not read this document.",
        missing_fields: missing,
        preview_png_base64: preview,
        region_previews,
      },
    };
  }

  return {
    ok: true,
    document,
    missing_fields: missing,
    partial,
    preview_png_base64: preview,
    region_previews,
  };
}

function normalizeFailure(payload: unknown, status: number): ExtractFailure {
  if (payload && typeof payload === "object") {
    const obj = payload as Record<string, unknown>;
    const errorType = obj.error_type;
    const message = typeof obj.message === "string" ? obj.message : "";
    const missing = Array.isArray(obj.missing_fields)
      ? (obj.missing_fields as string[])
      : [];
    const preview = previewFrom(obj);
    const region_previews = regionPreviewsFrom(obj);
    if (errorType === "reading_unsuccessful" || status === 422) {
      return {
        error_type: "reading_unsuccessful",
        message:
          message ||
          "Reading unsuccessful — could not read this document. Retake the photo or fill the form manually.",
        missing_fields: missing,
        preview_png_base64: preview,
        region_previews,
      };
    }
    if (errorType === "api_error" || status >= 500) {
      return {
        error_type: "api_error",
        message:
          message ||
          `API error — extract service failed (HTTP ${status}). Check connection or try again.`,
        missing_fields: missing,
        preview_png_base64: preview,
        region_previews,
      };
    }
  }
  return {
    error_type: "api_error",
    message: `API error — extract service failed (HTTP ${status}). Check connection or try again.`,
  };
}

export function formatExtractError(failure: ExtractFailure): string {
  const missing =
    failure.missing_fields && failure.missing_fields.length > 0
      ? ` Missing: ${failure.missing_fields.join(", ")}.`
      : "";
  return `${failure.message}${missing}`;
}
