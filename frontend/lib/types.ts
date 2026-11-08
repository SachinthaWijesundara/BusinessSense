export type Supplier = {
  id: string;
  name: string;
  credit_days: number | null;
  phone1: string | null;
  phone2: string | null;
  email: string | null;
  address: string | null;
  related_company_1: string | null;
  related_company_2: string | null;
  created_at: string;
  updated_at: string;
};

export type Item = {
  id: string;
  sku: string | null;
  name: string;
};

export type Invoice = {
  id: string;
  supplier_id: string;
  invoice_number: string;
  invoice_date: string | null;
  delivery_date: string | null;
  special_discount: number;
  total: number;
  /** Public URL of CLAHE PNG in Storage; null for manual invoices. */
  image_url: string | null;
  created_at: string;
  updated_at: string;
};

export type InvoiceItem = {
  id: string;
  invoice_id: string;
  sku: string | null;
  item_name: string;
  quantity: number;
  mro: number;
  discount: number;
  line_total: number;
  sort_order: number;
};

export type InvoiceLineDraft = {
  key: string;
  sku: string;
  item_name: string;
  quantity: string;
  mro: string;
  discount: string;
  line_total: string;
};

export type InvoiceWithSupplier = Invoice & {
  supplier_name?: string;
};
