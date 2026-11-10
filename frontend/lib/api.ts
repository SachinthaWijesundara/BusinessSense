/**
 * The ONLY place the frontend talks to anything outside the browser: one function per endpoint of
 * the BusinessSense backend (FastAPI; port 8765 locally, Render in production). The browser never
 * talks to the database, Supabase or OpenAI directly and holds no keys. Only GET and POST requests
 * are sent.
 *
 * Login: the owner's email and password go to the backend (POST /auth/login), which checks them
 * with Supabase Auth. The session it returns (a short-lived access token and a refresh token) is
 * kept in this browser and the access token is sent with every request. It is renewed shortly
 * before it expires; if the backend refuses it, the session is cleared and the login page opens.
 */

import type {
  AnalyticsOverview,
  AuditEvent,
  CashOverview,
  CashOverviewQuery,
  ConfirmSku,
  Dashboard,
  Health,
  Invoice,
  InvoiceCreate,
  InvoicePage,
  LoginReply,
  InvoiceFix,
  InvoiceQuery,
  InvoiceSummary,
  LineCheck,
  NewProduct,
  PaymentCreate,
  PaymentInput,
  PaymentQuery,
  Product,
  ProductHistory,
  ReceiptUpload,
  Session,
  Supplier,
  SupplierInput,
  SupplierPayment,
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
  paymentId?: string;
};

/** An error reply from the backend, or no reply at all (status 0). */
export class ApiError extends Error {
  status: number;
  errorType: string;
  reasons?: string[];
  missingFields?: string[];
  invoiceId?: string;
  paymentId?: string;

  /** Keep every detail the backend sent so screens can show reasons, missing fields or links. */
  constructor(details: ApiErrorDetails) {
    super(details.message);
    this.name = "ApiError";
    this.status = details.status;
    this.errorType = details.errorType;
    this.reasons = details.reasons;
    this.missingFields = details.missingFields;
    this.invoiceId = details.invoiceId;
    this.paymentId = details.paymentId;
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
      paymentId: typeof data.payment_id === "string" ? data.payment_id : undefined,
    });
  }
  return new ApiError({ status, errorType: "error", message: `Request failed (HTTP ${status}).` });
}

// Login session -------------------------------------------------------------------------------

const SESSION_KEY = "businesssense-session";
const SESSION_EVENT = "businesssense-session-change";

/** The stored session as text (null when logged out or storage is unavailable). */
export function readSessionText(): string | null {
  try {
    return window.localStorage.getItem(SESSION_KEY);
  } catch {
    return null;
  }
}

/** Read a stored session; anything malformed counts as logged out. */
export function parseSession(text: string | null): Session | null {
  if (!text) return null;
  try {
    const value = JSON.parse(text) as Session;
    return typeof value.access_token === "string" && typeof value.refresh_token === "string" ? value : null;
  } catch {
    return null;
  }
}

/** Store (or, with null, forget) the session and tell the screens that it changed. */
function saveSession(session: Session | null): void {
  try {
    if (session) window.localStorage.setItem(SESSION_KEY, JSON.stringify(session));
    else window.localStorage.removeItem(SESSION_KEY);
  } catch {
    // storage unavailable: the session lasts only until the page reloads
  }
  window.dispatchEvent(new Event(SESSION_EVENT));
}

/** Call ``onChange`` whenever the session changes (in this tab or another). Returns an unsubscribe. */
export function subscribeSession(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  window.addEventListener(SESSION_EVENT, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(SESSION_EVENT, onChange);
  };
}

/** Turn the backend's login reply into the session kept in the browser. */
function toSession(reply: LoginReply): Session {
  return {
    access_token: reply.access_token,
    refresh_token: reply.refresh_token,
    expires_at: Date.now() + reply.expires_in * 1000,
    email: reply.email,
    business_name: reply.business_name,
  };
}

let refreshing: Promise<Session | null> | null = null;

/** Renew the session once (requests that need it at the same time share one renewal). */
function refreshSession(current: Session): Promise<Session | null> {
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const reply = await send<LoginReply>(
          "/auth/refresh",
          { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: current.refresh_token }) },
          false,
        );
        const session = toSession(reply);
        saveSession(session);
        return session;
      } catch {
        saveSession(null);
        return null;
      } finally {
        refreshing = null;
      }
    })();
  }
  return refreshing;
}

/** A current access token, renewed when it expires within a minute; null when logged out. */
async function accessToken(): Promise<string | null> {
  let session = parseSession(readSessionText());
  if (session && session.expires_at - Date.now() < 60_000) session = await refreshSession(session);
  return session?.access_token ?? null;
}

/** POST /auth/login: check the email and password and keep the session in this browser. */
export async function login(email: string, password: string): Promise<Session> {
  const reply = await send<LoginReply>(
    "/auth/login",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }) },
    false,
  );
  const session = toSession(reply);
  saveSession(session);
  return session;
}

/** POST /auth/logout: forget the session here and end it on the server. */
export async function logout(): Promise<void> {
  const session = parseSession(readSessionText());
  saveSession(null);
  if (!session) return;
  try {
    await fetch(`${API_URL}/auth/logout`, { method: "POST", headers: { Authorization: `Bearer ${session.access_token}` } });
  } catch {
    // already logged out in this browser; the server session expires on its own
  }
}

/** Send one request with the login token; a refused token is renewed once, then the owner is logged out. */
async function request<T>(path: string, init?: RequestInit, onHeaders?: (headers: Headers) => void): Promise<T> {
  try {
    return await send<T>(path, init, true, onHeaders);
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 401) throw error;
    const session = parseSession(readSessionText());
    const renewed = session ? await refreshSession(session) : null;
    if (!renewed) {
      saveSession(null);
      throw error;
    }
    return send<T>(path, init, true, onHeaders);
  }
}

/** Send one request to the backend and return its JSON reply, or throw an ApiError. */
async function send<T>(
  path: string,
  init: RequestInit | undefined,
  withLogin: boolean,
  onHeaders?: (headers: Headers) => void,
): Promise<T> {
  const headers = new Headers(init?.headers);
  if (withLogin) {
    const token = await accessToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
  }
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, { ...init, headers });
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
  onHeaders?.(response.headers);
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
  return send<Health>("/health", undefined, false);
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

/** GET /invoices with limit/offset: one page of the search and how many match in all (X-Total-Count). */
export async function listInvoicePage(query: InvoiceQuery): Promise<InvoicePage> {
  let total = 0;
  const rows = await request<InvoiceSummary[]>(`/invoices${queryString(query)}`, undefined, (headers) => {
    total = Number(headers.get("X-Total-Count") ?? 0);
  });
  return { rows, total: Number.isFinite(total) && total > 0 ? total : rows.length };
}

/**
 * POST /invoices/upload: send a photo of an invoice or a payment receipt. For an invoice the backend
 * reads, audits, matches and saves a draft; for a receipt it saves unconfirmed supplier payments.
 */
export function uploadInvoice(file: File, supplierId?: string): Promise<Invoice | ReceiptUpload> {
  const body = new FormData();
  body.append("file", file);
  if (supplierId) body.append("supplier_id", supplierId);
  return request<Invoice | ReceiptUpload>("/invoices/upload", { method: "POST", body });
}

/** True when an upload reply is a receipt's payments rather than an invoice. */
export function isReceipt(saved: Invoice | ReceiptUpload): saved is ReceiptUpload {
  return "document_type" in saved && saved.document_type === "receipt";
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

/** GET /suppliers/{id}/profile: supplier details, totals, invoices, item lines and payments. */
export function getSupplierProfile(supplierId: string): Promise<SupplierProfile> {
  return request<SupplierProfile>(`/suppliers/${supplierId}/profile`);
}

// Supplier payments ----------------------------------------------------------------------------

/** GET /payments: payments by supplier, invoice or status (e.g. those waiting for confirmation). */
export function listPayments(query: PaymentQuery = {}): Promise<SupplierPayment[]> {
  return request<SupplierPayment[]>(`/payments${queryString(query)}`);
}

/** POST /payments: record a payment typed in by hand (and confirm it at once if asked). */
export function createPayment(data: PaymentCreate): Promise<SupplierPayment> {
  return postJson<SupplierPayment>("/payments", data);
}

/** POST /payments/{id}/fix: correct a payment; it goes back to unconfirmed. */
export function fixPayment(paymentId: string, data: PaymentInput): Promise<SupplierPayment> {
  return postJson<SupplierPayment>(`/payments/${paymentId}/fix`, data);
}

/** POST /payments/{id}/confirm: the owner confirms a payment, or gets the reasons it cannot be (409). */
export function confirmPayment(paymentId: string): Promise<SupplierPayment> {
  return request<SupplierPayment>(`/payments/${paymentId}/confirm`, { method: "POST" });
}

/** POST /payments/{id}/reject: keep a wrong payment out of the ledger, with the owner's reason. */
export function rejectPayment(paymentId: string, reason: string): Promise<SupplierPayment> {
  return postJson<SupplierPayment>(`/payments/${paymentId}/reject`, { reason });
}

// Purchase Analytics (Model 1) -------------------------------------------------------------------

/** GET /analytics/overview: products with their expected next price, flagged prices, spend. */
export function getAnalyticsOverview(): Promise<AnalyticsOverview> {
  return request<AnalyticsOverview>("/analytics/overview");
}

/** GET /analytics/cash-overview: payable series, overdue, monthly purchases+paid, supplier/item pies. */
export function getCashOverview(query: CashOverviewQuery = {}): Promise<CashOverview> {
  return request<CashOverview>(`/analytics/cash-overview${queryString(query)}`);
}

/** GET /analytics/products/{id}: one product's prices over time with the expected range. */
export function getProductHistory(productId: string): Promise<ProductHistory> {
  return request<ProductHistory>(`/analytics/products/${productId}`);
}

/** GET /analytics/invoices/{id}/price-check: each line's unit price against its expected range. */
export function getInvoicePriceCheck(invoiceId: string): Promise<LineCheck[]> {
  return request<LineCheck[]>(`/analytics/invoices/${invoiceId}/price-check`);
}
