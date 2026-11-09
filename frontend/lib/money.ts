/**
 * Display helpers for numbers in forms and tables.
 * No business maths here: totals, balances and audits all come from the backend.
 */

/** Show a rupee amount like "5,850.00", or "—" when there is none. */
export function money(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-LK", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** Turn a number into text for an input box ("" when empty). */
export function numberText(value: number | null | undefined): string {
  return value === null || value === undefined ? "" : String(value);
}

/**
 * Read a number the owner typed ("1,250.50" -> 1250.5).
 * Returns null for an empty box and NaN for text that is not a number, so forms can complain.
 */
export function parseNumber(input: string): number | null {
  const cleaned = input.replace(/,/g, "").trim();
  if (cleaned === "") return null;
  const value = Number(cleaned);
  return Number.isFinite(value) ? value : Number.NaN;
}

/** Turn a typed text into a value to send: trimmed text, or null when empty. */
export function textOrNull(input: string): string | null {
  const trimmed = input.trim();
  return trimmed === "" ? null : trimmed;
}
