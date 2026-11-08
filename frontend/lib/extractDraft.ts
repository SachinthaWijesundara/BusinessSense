import type { ExtractedDocument } from "./extractTypes";

const KEY = "businesssense-extract-draft";
const MISSING_KEY = "businesssense-extract-missing";

export type StoredExtractDraft = {
  document: ExtractedDocument;
  missing_fields: string[];
  savedAt: number;
};

export function saveExtractDraft(
  document: ExtractedDocument,
  missing_fields: string[] = [],
): void {
  if (typeof window === "undefined") return;
  const payload: StoredExtractDraft = {
    document,
    missing_fields,
    savedAt: Date.now(),
  };
  sessionStorage.setItem(KEY, JSON.stringify(payload));
  sessionStorage.setItem(MISSING_KEY, JSON.stringify(missing_fields));
}

export function peekExtractDraft(): ExtractedDocument | null {
  if (typeof window === "undefined") return null;
  const raw = sessionStorage.getItem(KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as StoredExtractDraft;
    return parsed.document ?? null;
  } catch {
    return null;
  }
}

export function peekMissingFields(): string[] {
  if (typeof window === "undefined") return [];
  const raw = sessionStorage.getItem(KEY);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as StoredExtractDraft;
    return parsed.missing_fields ?? [];
  } catch {
    return [];
  }
}

export function takeExtractDraft(): {
  document: ExtractedDocument;
  missing_fields: string[];
} | null {
  const doc = peekExtractDraft();
  const missing = peekMissingFields();
  if (!doc) return null;
  clearExtractDraft();
  return { document: doc, missing_fields: missing };
}

export function clearExtractDraft(): void {
  if (typeof window === "undefined") return;
  sessionStorage.removeItem(KEY);
  sessionStorage.removeItem(MISSING_KEY);
}
