export type ExtractSupplierDraft = {
  name?: string | null;
  address?: string | null;
  phone1?: string | null;
};

export type ExtractInvoiceLine = {
  sku?: string | null;
  item_name?: string | null;
  quantity?: number | null;
  mro?: number | null;
  discount?: number | null;
  line_total?: number | null;
};

export type ExtractInvoiceDraft = {
  invoice_number?: string | null;
  invoice_date?: string | null;
  delivery_date?: string | null;
  special_discount?: number | null;
  total?: number | null;
  lines?: ExtractInvoiceLine[];
};

export type ExtractReceiptPayment = {
  invoice_number?: string | null;
  amount?: number | null;
};

export type ExtractReceiptCheque = {
  cheque_number?: string | null;
  bank_name?: string | null;
  cheque_date?: string | null;
};

export type ExtractReceiptDraft = {
  receipt_number?: string | null;
  receipt_date?: string | null;
  payment_method?: "cash" | "cheque" | null;
  payments?: ExtractReceiptPayment[];
  cheques?: ExtractReceiptCheque[];
};

export type ExtractedDocument = {
  doc_type: "invoice" | "receipt";
  supplier: ExtractSupplierDraft;
  invoice?: ExtractInvoiceDraft | null;
  receipt?: ExtractReceiptDraft | null;
};

export type ExtractErrorType = "api_error" | "reading_unsuccessful";

export type RegionPreviews = Record<string, string>;

export type ExtractFailure = {
  error_type: ExtractErrorType;
  message: string;
  missing_fields?: string[];
  preview_png_base64?: string | null;
  region_previews?: RegionPreviews;
};

export type ExtractSuccess = {
  ok: true;
  document: ExtractedDocument;
  missing_fields: string[];
  partial: boolean;
  preview_png_base64?: string | null;
  region_previews?: RegionPreviews;
};

export type ExtractOutcome = ExtractSuccess | { ok: false; failure: ExtractFailure };
