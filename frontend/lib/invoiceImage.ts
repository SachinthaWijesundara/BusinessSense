import { db } from "./supabase";

export const CLAHE_PREVIEW_KEY = "businesssense-clahe-preview";
export const REGION_PREVIEW_KEY = "businesssense-region-previews";
export const INVOICE_IMAGES_BUCKET = "invoice-images";

export function peekClahePreviewDataUrl(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const value = sessionStorage.getItem(CLAHE_PREVIEW_KEY);
    return value && value.startsWith("data:") ? value : null;
  } catch {
    return null;
  }
}

export function clearInvoiceImageSession(): void {
  if (typeof window === "undefined") return;
  try {
    sessionStorage.removeItem(CLAHE_PREVIEW_KEY);
    sessionStorage.removeItem(REGION_PREVIEW_KEY);
  } catch {
    // ignore
  }
}

function dataUrlToBlob(dataUrl: string): Blob {
  const [header, payload] = dataUrl.split(",", 2);
  if (!payload) throw new Error("Invalid CLAHE preview data URL.");
  const mimeMatch = /data:([^;]+)/.exec(header ?? "");
  const mime = mimeMatch?.[1] || "image/png";
  const binary = atob(payload);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new Blob([bytes], { type: mime });
}

/** Upload pending CLAHE PNG; returns public URL or null if nothing to upload. */
export async function uploadPendingClaheImage(
  supplierId: string,
  invoiceId: string,
): Promise<string | null> {
  const dataUrl = peekClahePreviewDataUrl();
  if (!dataUrl) return null;

  const blob = dataUrlToBlob(dataUrl);
  const path = `${supplierId}/${invoiceId}.png`;
  const { error } = await db()
    .storage.from(INVOICE_IMAGES_BUCKET)
    .upload(path, blob, {
      contentType: "image/png",
      upsert: true,
    });
  if (error) throw error;

  const { data } = db().storage.from(INVOICE_IMAGES_BUCKET).getPublicUrl(path);
  return data.publicUrl;
}
