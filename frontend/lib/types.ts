/**
 * Shapes of the JSON the BusinessSense backend sends and receives.
 * They mirror the backend's API contract; the frontend only displays these values.
 */

export type InvoiceStatus = "draft" | "verified" | "rejected";
export type PaymentStatus = "unpaid" | "paid" | "unconfirmed";
export type SupplierPaymentStatus = "unconfirmed" | "confirmed" | "rejected";
export type PaymentMethod = "cash" | "cheque" | "bank_transfer";
export type MatchStatus = "unmatched" | "linked" | "pending" | "new_product";
export type PaymentType = "cash" | "credit";

/** A supplier the shop buys from. credit_days is set by the owner. */
export type Supplier = {
  id: string;
  business_id: string;
  name: string;
  credit_days: number | null;
  phone1: string | null;
  phone2: string | null;
  email: string | null;
  address: string | null;
  created_at: string | null;
  updated_at: string | null;
};

/** Fields the owner can send when adding or changing a supplier. */
export type SupplierInput = {
  name?: string;
  credit_days?: number | null;
  phone1?: string | null;
  phone2?: string | null;
  email?: string | null;
  address?: string | null;
};

/** A master product in the shop's catalogue. */
export type Product = {
  id: string;
  business_id: string;
  name: string;
  sku: string | null;
  unit: string | null;
  created_at: string | null;
};

/** Fields for a new catalogue product. */
export type NewProduct = {
  name: string;
  sku?: string | null;
  unit?: string | null;
};

/** One possible product for an invoice line, as found by the backend's product search. */
export type Candidate = {
  product_id: string;
  product_name: string;
  product_sku: string | null;
  alias_text: string;
  similarity: number;
};

/** One item line of an invoice, with its product-match state. */
export type InvoiceLine = {
  id: string;
  invoice_id: string;
  sort_order: number;
  sku: string | null;
  item_name: string;
  quantity: number | null;
  mro: number | null;
  discount: number;
  line_total: number | null;
  product_id: string | null;
  product_name: string | null;
  suggested_product_id: string | null;
  match_status: MatchStatus;
  match_similarity: number | null;
  match_candidates: Candidate[];
  match_reason: string | null;
};

/** A problem the backend's math audit or date checks found. */
export type AuditIssue = {
  code: string;
  message: string;
  line_index?: number;
  field?: string;
  expected?: number;
  actual?: number;
};

/** An invoice as it appears in lists (no lines). */
export type InvoiceSummary = {
  id: string;
  business_id: string;
  supplier_id: string | null;
  supplier_name: string | null;
  supplier_name_raw: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  delivery_date: string | null;
  due_date: string | null;
  payment_type: PaymentType | null;
  credit_days: number | null;
  special_discount: number;
  tax: number;
  total: number | null;
  status: InvoiceStatus;
  source: "manual" | "agent";
  math_ok: boolean | null;
  math_issues: AuditIssue[];
  field_issues: AuditIssue[];
  missing_fields: string[];
  low_confidence_fields: string[];
  payment_status: PaymentStatus;
  amount_paid: number;
  image_path: string | null;
  verified_at: string | null;
  created_at: string | null;
  updated_at: string | null;
};

/** A full invoice: the summary plus its photo link and lines. */
export type Invoice = InvoiceSummary & {
  image_url: string | null;
  lines: InvoiceLine[];
};

/** One entry of an invoice's audit log (extracted, corrected, confirmed, verified...). */
export type AuditEvent = {
  id: string;
  invoice_id: string | null;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string | null;
};

/** One line as the owner types or corrects it. */
export type LineInput = {
  id?: string;
  sku?: string | null;
  item_name: string;
  quantity: number | null;
  mro: number | null;
  discount?: number | null;
  line_total: number | null;
};

/** Invoice header fields the owner types or corrects. */
export type InvoiceHeaderInput = {
  supplier_id?: string | null;
  invoice_number?: string | null;
  invoice_date?: string | null;
  delivery_date?: string | null;
  payment_type?: PaymentType | null;
  credit_days?: number | null;
  special_discount?: number | null;
  tax?: number | null;
  total?: number | null;
};

/** Body of POST /invoices (an invoice typed in by hand). */
export type InvoiceCreate = InvoiceHeaderInput & {
  supplier_id: string;
  lines: LineInput[];
};

/** Body of POST /invoices/{id}/fix (only the fields sent are changed). */
export type InvoiceFix = InvoiceHeaderInput & {
  lines?: LineInput[];
};

/** Body of POST /invoices/{id}/confirm-sku: link a line to an existing or a new product. */
export type ConfirmSku =
  | { line_id: string; product_id: string }
  | { line_id: string; new_product: NewProduct };

/** Filters for GET /invoices. */
export type InvoiceQuery = {
  supplier_id?: string;
  status?: InvoiceStatus;
  payment_status?: PaymentStatus;
  invoice_number?: string;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
};

/** One page of the invoice search, with how many invoices match in all. */
export type InvoicePage = {
  rows: InvoiceSummary[];
  total: number;
};

/** A verified, unpaid invoice that falls due soon (or is overdue). */
export type PaymentDue = {
  invoice_id: string;
  supplier_id: string | null;
  supplier_name: string | null;
  invoice_number: string | null;
  due_date: string;
  total: number | null;
  amount_paid: number;
  still_owed: number | null;
  days_left: number;
};

/** Reply of GET /dashboard. */
export type Dashboard = {
  review_queue: number;
  verified_invoices: number;
  monthly_purchases: { month: string; total: number }[];
  supplier_spend: { supplier_id: string; supplier_name: string; total: number }[];
  payments_due: PaymentDue[];
  unconfirmed_payments: number;
  payment_reminders: SupplierPayment[];
  forecast: null;
  forecast_note: string;
};

/** One item line bought from a supplier (verified invoices only). */
export type SupplierLine = {
  invoice_id: string;
  invoice_number: string | null;
  invoice_date: string | null;
  item_name: string;
  product_id: string | null;
  product_name: string | null;
  quantity: number | null;
  mro: number | null;
  line_total: number | null;
};

/** Reply of GET /suppliers/{id}/profile. All totals are calculated by the backend. */
export type SupplierProfile = {
  supplier: Supplier;
  totals: {
    invoice_count: number;
    draft_count: number;
    verified_total: number;
    outstanding: number;
    next_due_date: string | null;
    paid_total: number;
    unconfirmed_total: number;
    unconfirmed_count: number;
  };
  invoices: InvoiceSummary[];
  lines: SupplierLine[];
  payments: SupplierPayment[];
};

/**
 * One payment to a supplier: typed in, or one invoice settled on a receipt photo. It stays
 * "unconfirmed" until the owner confirms it; reminder_due is set once it has waited two days.
 */
export type SupplierPayment = {
  id: string;
  business_id: string;
  supplier_id: string | null;
  supplier_name: string | null;
  supplier_name_raw: string | null;
  invoice_id: string | null;
  invoice_number: string | null;
  invoice_number_raw: string | null;
  amount: number | null;
  paid_on: string | null;
  method: PaymentMethod | null;
  receipt_number: string | null;
  cheque_number: string | null;
  bank_name: string | null;
  cheque_date: string | null;
  status: SupplierPaymentStatus;
  source: "manual" | "agent";
  image_path: string | null;
  image_url: string | null;
  low_confidence_fields: string[];
  confirmed_at: string | null;
  reminder_sent_at: string | null;
  reminder_due: boolean;
  created_at: string | null;
  updated_at: string | null;
};

/** Payment fields the owner types or corrects (POST /payments/{id}/fix sends only these). */
export type PaymentInput = {
  supplier_id?: string | null;
  invoice_id?: string | null;
  invoice_number_raw?: string | null;
  amount?: number | null;
  paid_on?: string | null;
  method?: PaymentMethod | null;
  receipt_number?: string | null;
  cheque_number?: string | null;
  bank_name?: string | null;
  cheque_date?: string | null;
};

/** Body of POST /payments: a payment typed in by hand; confirm saves and confirms it at once. */
export type PaymentCreate = PaymentInput & {
  supplier_id: string;
  amount: number;
  paid_on: string;
  confirm?: boolean;
};

/** Reply of POST /invoices/upload when the photo was a payment receipt. */
export type ReceiptUpload = {
  document_type: "receipt";
  receipt_number: string | null;
  supplier_id: string | null;
  supplier_name_raw: string | null;
  missing_fields: string[];
  image_url: string | null;
  payments: SupplierPayment[];
};

/** Filters for GET /payments. */
export type PaymentQuery = {
  supplier_id?: string;
  invoice_id?: string;
  status?: SupplierPaymentStatus;
};

/** Reply of GET /health. */
export type Health = {
  ok: boolean;
  database: string;
  openai_configured: boolean;
  image_storage: boolean;
  login_required: boolean;
};

/** Reply of POST /auth/login and /auth/refresh. */
export type LoginReply = {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  email: string | null;
  business_id: string;
  business_name: string;
};

/** The owner's login session as kept in this browser (expires_at in milliseconds). */
export type Session = {
  access_token: string;
  refresh_token: string;
  expires_at: number;
  email: string | null;
  business_name: string;
};
