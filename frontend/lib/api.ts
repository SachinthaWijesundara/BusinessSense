/**
 * The ONLY place the frontend talks to anything outside the browser: one function per endpoint of
 * the BusinessSense backend (FastAPI; port 8765 locally, Render in production). The browser never
 * talks to the database or OpenAI directly and holds no keys. Only GET and POST requests are sent.
 */

import type {
  AuditEvent,
  ConfirmSku,
  Dashboard,
  Health,
  Invoice,
  InvoiceCreate,
  InvoiceFix,
  InvoiceQuery,
  InvoiceSummary,
  NewProduct,
  Product,
  Supplier,
  SupplierInput,
  SupplierProfile,
} from "./types";

/** Address of the backend, from NEXT_PUBLIC_API_URL (set it to the Render URL on Vercel). */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8765").replace(/\/$/, "");

/** Details of a failed request, in a form the screens can show to the owner. */
type ApiErrorDetails = {
  status: number;
  errorType: string;
  message: string;
  reasons?: string[];
  missingFields?: string[];
  invoiceId?: string;
};

/** An error reply from the backend, or no reply at all (status 0). */
export class ApiError extends Error {
  status: number;
  errorType: string;
  reasons?: string[];
  missingFields?: string[];
  invoiceId?: string;

  /** Keep every detail the backend sent so screens can show reasons, missing fields or links. */
  constructor(details: ApiErrorDetails) {
    super(details.message);
    this.name = "ApiError";
    this.status = details.status;
    this.errorType = details.errorType;
    this.reasons = details.reasons;
    this.missingFields = details.missingFields;
    this.invoiceId = details.invoiceId;
  }
}

/** Text to show for any error thrown by a request. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

/** Read a list of strings from an error body field, if it is one. */
function stringList(value: unknown): string[] | undefined {
  return Array.isArray(value) ? value.map(String) : undefined;
}

/** Build an ApiError from a failed reply: business errors and FastAPI's validation errors. */
function toApiError(status: number, body: unknown): ApiError {
  if (body && typeof body === "object") {
    const data = body as Record<string, unknown>;
    if (Array.isArray(data.detail)) {
      const message = data.detail
        .map((item) => {
          const entry = item as { loc?: unknown[]; msg?: string };
          const where = (entry.loc ?? []).slice(1).join(".");
          return where ? `${where}: ${entry.msg}` : String(entry.msg);
        })
        .join("; ");
      return new ApiError({ status, errorType: "invalid_request", message });
    }
    return new ApiError({
      status,
      errorType: typeof data.error_type === "string" ? data.error_type : "error",
      message:
        typeof data.message === "string" && data.message
          ? data.message
          : typeof data.detail === "string"
            ? data.detail
            : `Request failed (HTTP ${status}).`,
      reasons: stringList(data.reasons),
      missingFields: stringList(data.missing_fields),
      invoiceId: typeof data.invoice_id === "string" ? data.invoice_id : undefined,
    });
  }
  return new ApiError({ status, errorType: "error", message: `Request failed (HTTP ${status}).` });
}

/** Send one request to the backend and return its JSON reply, or throw an ApiError. */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, init);
  } catch {
    throw new ApiError({
      status: 0,
      errorType: "network_error",
      message: `The backend is not reachable at ${API_URL}. Is it running?`,
    });
  }
  const text = await response.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!response.ok) throw toApiError(response.status, body);
  return body as T;
}

/** Send a POST with a JSON body. */
function postJson<T>(path: string, data: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

/** Turn filters into "?a=1&b=2", skipping empty ones. */
function queryString(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

// Health and dashboard -------------------------------------------------------------------------

/** GET /health: is the backend up, and is it connected to the database. */
export function getHealth(): Promise<Health> {
  return request<Health>("/health");
}

/** GET /dashboard: review queue, purchases by month, supplier spend and payments due. */
export function getDashboard(): Promise<Dashboard> {
  return request<Dashboard>("/dashboard");
}

// Invoices -------------------------------------------------------------------------------------

/** GET /invoices: search invoices by supplier, status, payment status, number or date range. */
export function listInvoices(query: InvoiceQuery = {}): Promise<InvoiceSummary[]> {
  return request<InvoiceSummary[]>(`/invoices${queryString(query)}`);
}

/** POST /invoices/upload: send a photo; the backend reads, audits, matches and saves a draft. */
export function uploadInvoice(file: File, supplierId?: string): Promise<Invoice> {
  const body = new FormData();
  body.append("file", file);
  if (supplierId) body.append("supplier_id", supplierId);
  return request<Invoice>("/invoices/upload", { method: "POST", body });
}

/** POST /invoices: save an invoice typed in by hand; the backend audits and matches it. */
export function createInvoice(data: InvoiceCreate): Promise<Invoice> {
  return postJson<Invoice>("/invoices", data);
}

/** GET /invoices/{id}: one invoice with its lines and a link to its photo. */
export function getInvoice(invoiceId: string): Promise<Invoice> {
  return request<Invoice>(`/invoices/${invoiceId}`);
}

/** GET /invoices/{id}/events: the invoice's audit log, oldest first. */
export function getInvoiceEvents(invoiceId: string): Promise<AuditEvent[]> {
  return request<AuditEvent[]>(`/invoices/${invoiceId}/events`);
}

/** POST /invoices/{id}/fix: send the owner's corrections; the backend re-runs the math audit. */
export function fixInvoice(invoiceId: string, data: InvoiceFix): Promise<Invoice> {
  return postJson<Invoice>(`/invoices/${invoiceId}/fix`, data);
}

/** POST /invoices/{id}/confirm-sku: link a line to an existing product or a new one. */
export function confirmSku(invoiceId: string, data: ConfirmSku): Promise<Invoice> {
  return postJson<Invoice>(`/invoices/${invoiceId}/confirm-sku`, data);
}

/** POST /invoices/{id}/verify: mark verified, or get the reasons it cannot be yet (409). */
export function verifyInvoice(invoiceId: string): Promise<Invoice> {
  return request<Invoice>(`/invoices/${invoiceId}/verify`, { method: "POST" });
}

/** POST /invoices/{id}/reject: keep a draft out of the ledger, with the owner's reason. */
export function rejectInvoice(invoiceId: string, reason: string): Promise<Invoice> {
  return postJson<Invoice>(`/invoices/${invoiceId}/reject`, { reason });
}

/** POST /invoices/{id}/payment: mark a verified invoice paid or unpaid. */
export function setPaymentStatus(invoiceId: string, paymentStatus: "paid" | "unpaid"): Promise<Invoice> {
  return postJson<Invoice>(`/invoices/${invoiceId}/payment`, { payment_status: paymentStatus });
}

// Products -------------------------------------------------------------------------------------

/** GET /products: the shop's master catalogue. */
export function listProducts(): Promise<Product[]> {
  return request<Product[]>("/products");
}

/** POST /products: add a product to the catalogue. */
export function createProduct(data: NewProduct): Promise<Product> {
  return postJson<Product>("/products", data);
}

// Suppliers ------------------------------------------------------------------------------------

/** GET /suppliers: every supplier, by name. */
export function listSuppliers(): Promise<Supplier[]> {
  return request<Supplier[]>("/suppliers");
}

/** POST /suppliers: add a supplier. */
export function createSupplier(data: SupplierInput & { name: string }): Promise<Supplier> {
  return postJson<Supplier>("/suppliers", data);
}

/** GET /suppliers/{id}: one supplier. */
export function getSupplier(supplierId: string): Promise<Supplier> {
  return request<Supplier>(`/suppliers/${supplierId}`);
}

/** POST /suppliers/{id}: change a supplier's details, e.g. its credit days. */
export function updateSupplier(supplierId: string, data: SupplierInput): Promise<Supplier> {
  return postJson<Supplier>(`/suppliers/${supplierId}`, data);
}

/** GET /suppliers/{id}/profile: supplier details, totals, invoices and item lines. */
export function getSupplierProfile(supplierId: string): Promise<SupplierProfile> {
  return request<SupplierProfile>(`/suppliers/${supplierId}/profile`);
}
