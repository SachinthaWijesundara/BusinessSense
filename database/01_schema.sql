-- 01_schema.sql: the BusinessSense tables in Supabase Postgres (proposal section 7.1).
--
-- Creates: businesses, suppliers, products (master catalogue), product_aliases (printed names
-- with pgvector embeddings), invoices, invoice_items, supplier_payments, expenses, cash_records,
-- model_predictions, extraction_runs (raw agent outputs), audit_log, and the
-- match_product_aliases() search used by SkuMatchAgent's find_sku_candidates tool.
--
-- Apply the three files in order (01, 02, 03) in the Supabase SQL editor.
-- Safe on a fresh project AND on the project the first prototype and the data-entry app already
-- use (suppliers, invoices, invoice_items, items): everything is "create ... if not exists" /
-- "add column if not exists", nothing is dropped, and re-running it changes nothing.

create schema if not exists extensions;
create extension if not exists vector with schema extensions;
set search_path = public, extensions;

-- ---------------------------------------------------------------------------------------------
-- businesses: one row per shop. The single-shop prototype uses the fixed id below until
-- owner login is wired in (owner_user_id will point at auth.users).
-- ---------------------------------------------------------------------------------------------
create table if not exists businesses (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  owner_user_id uuid,
  created_at timestamptz not null default now()
);

insert into businesses (id, name)
values ('00000000-0000-0000-0000-000000000001', 'Hardware shop')
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------------------------
-- suppliers
-- ---------------------------------------------------------------------------------------------
create table if not exists suppliers (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table suppliers add column if not exists business_id uuid not null
  default '00000000-0000-0000-0000-000000000001' references businesses(id);
alter table suppliers add column if not exists credit_days integer check (credit_days >= 0);
alter table suppliers add column if not exists phone1 text;
alter table suppliers add column if not exists phone2 text;
alter table suppliers add column if not exists email text;
alter table suppliers add column if not exists address text;
alter table suppliers add column if not exists related_company_1 text;
alter table suppliers add column if not exists related_company_2 text;
create index if not exists suppliers_business_name_idx on suppliers (business_id, lower(name));

-- ---------------------------------------------------------------------------------------------
-- products: the master catalogue. product_aliases: printed descriptions mapped to a product,
-- with a text-embedding-3-small vector for similarity search (SkuMatchAgent).
-- ---------------------------------------------------------------------------------------------
create table if not exists products (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null default '00000000-0000-0000-0000-000000000001' references businesses(id),
  name text not null,
  sku text,
  unit text,
  created_at timestamptz not null default now()
);
create unique index if not exists products_business_name_key on products (business_id, lower(name));

create table if not exists product_aliases (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null default '00000000-0000-0000-0000-000000000001' references businesses(id),
  product_id uuid not null references products(id) on delete cascade,
  alias_text text not null,
  normalized_text text not null,
  embedding vector(1536),
  source text not null default 'owner_confirmed'
    check (source in ('catalogue', 'owner_confirmed', 'agent_linked', 'manual')),
  confirmed boolean not null default true,
  created_at timestamptz not null default now()
);
create unique index if not exists product_aliases_unique_key
  on product_aliases (business_id, product_id, normalized_text);
create index if not exists product_aliases_lookup_idx
  on product_aliases (business_id, normalized_text);
create index if not exists product_aliases_embedding_idx
  on product_aliases using hnsw (embedding vector_cosine_ops);

-- The data-entry app keeps its catalogue in an "items" table. Copy those items into products
-- once (names already in products are skipped, ignoring case), so SkuMatchAgent can match them.
do $$
begin
  if exists (
    select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relname = 'items' and c.relkind = 'r'
  ) then
    insert into products (name, sku)
    select distinct on (lower(trim(i.name))) trim(i.name), i.sku
    from items i
    where trim(coalesce(i.name, '')) <> ''
      and not exists (
        select 1 from products p
        where p.business_id = '00000000-0000-0000-0000-000000000001'
          and lower(p.name) = lower(trim(i.name))
      )
    order by lower(trim(i.name)), i.sku nulls last;
  end if;
end $$;

-- On a fresh project there is no "items" table: expose the catalogue under that name (an
-- updatable view) so the data-entry app can still read and add items.
do $$
begin
  if to_regclass('public.items') is null then
    execute 'create view items as select id, sku, name from products';
  end if;
end $$;

-- ---------------------------------------------------------------------------------------------
-- invoices and invoice_items
-- status: draft until the owner verifies it. Only verified, product-linked lines feed the
-- supplier/item histories and Model 1 / Model 2.
-- ---------------------------------------------------------------------------------------------
create table if not exists invoices (
  id uuid primary key default gen_random_uuid(),
  supplier_id uuid references suppliers(id) on delete restrict,
  invoice_number text,
  invoice_date date,
  delivery_date date,
  special_discount numeric(12, 2) not null default 0,
  total numeric(12, 2),
  image_url text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
-- An agent-read invoice can arrive before its supplier is confirmed.
alter table invoices alter column supplier_id drop not null;
alter table invoices alter column invoice_number drop not null;
alter table invoices alter column total drop not null;
alter table invoices add column if not exists business_id uuid not null
  default '00000000-0000-0000-0000-000000000001' references businesses(id);
alter table invoices add column if not exists supplier_name_raw text;
alter table invoices add column if not exists due_date date;
alter table invoices add column if not exists payment_type text check (payment_type in ('cash', 'credit'));
alter table invoices add column if not exists credit_days integer check (credit_days >= 0);
alter table invoices add column if not exists tax numeric(12, 2) not null default 0;
-- Rows that existed before this migration were typed in by hand and are left as drafts;
-- mark the ones checked against the paper as verified.
alter table invoices add column if not exists status text not null default 'draft'
  check (status in ('draft', 'verified', 'rejected'));
alter table invoices add column if not exists source text not null default 'manual'
  check (source in ('manual', 'agent'));
alter table invoices add column if not exists math_ok boolean;
alter table invoices add column if not exists math_issues jsonb not null default '[]'::jsonb;
alter table invoices add column if not exists field_issues jsonb not null default '[]'::jsonb;
alter table invoices add column if not exists missing_fields jsonb not null default '[]'::jsonb;
alter table invoices add column if not exists low_confidence_fields jsonb not null default '[]'::jsonb;
alter table invoices add column if not exists payment_status text not null default 'unpaid'
  check (payment_status in ('unpaid', 'paid', 'unconfirmed'));
alter table invoices add column if not exists image_path text;
alter table invoices add column if not exists verified_at timestamptz;
create unique index if not exists invoices_supplier_number_key
  on invoices (business_id, supplier_id, invoice_number)
  where supplier_id is not null and invoice_number is not null;
create index if not exists invoices_business_date_idx on invoices (business_id, invoice_date desc);
create index if not exists invoices_business_status_idx on invoices (business_id, status);

create table if not exists invoice_items (
  id uuid primary key default gen_random_uuid(),
  invoice_id uuid not null references invoices(id) on delete cascade,
  sku text,
  item_name text not null,
  quantity numeric(12, 3),
  mro numeric(12, 2),
  discount numeric(12, 2) not null default 0,
  line_total numeric(12, 2),
  sort_order integer not null default 0
);
-- Unreadable values stay null (and fail the math audit) instead of being typed in as 0.
alter table invoice_items alter column quantity drop not null;
alter table invoice_items alter column mro drop not null;
alter table invoice_items alter column line_total drop not null;
alter table invoice_items add column if not exists product_id uuid references products(id) on delete set null;
alter table invoice_items add column if not exists suggested_product_id uuid references products(id) on delete set null;
alter table invoice_items add column if not exists match_status text not null default 'unmatched'
  check (match_status in ('unmatched', 'linked', 'pending', 'new_product'));
alter table invoice_items add column if not exists match_similarity real;
alter table invoice_items add column if not exists match_candidates jsonb not null default '[]'::jsonb;
alter table invoice_items add column if not exists match_reason text;
create index if not exists invoice_items_invoice_idx on invoice_items (invoice_id, sort_order);
create index if not exists invoice_items_product_idx on invoice_items (product_id);

-- ---------------------------------------------------------------------------------------------
-- payments, cash records, predictions
-- ---------------------------------------------------------------------------------------------
create table if not exists supplier_payments (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null default '00000000-0000-0000-0000-000000000001' references businesses(id),
  supplier_id uuid not null references suppliers(id) on delete restrict,
  invoice_id uuid references invoices(id) on delete set null,
  amount numeric(12, 2) not null check (amount > 0),
  paid_on date,
  method text check (method in ('cash', 'cheque', 'bank_transfer')),
  receipt_number text,
  cheque_number text,
  bank_name text,
  cheque_date date,
  -- A payment stays unconfirmed until the owner confirms it.
  status text not null default 'unconfirmed' check (status in ('confirmed', 'unconfirmed')),
  reminder_sent_at timestamptz,
  created_at timestamptz not null default now()
);
create index if not exists supplier_payments_supplier_idx on supplier_payments (business_id, supplier_id);

create table if not exists expenses (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null default '00000000-0000-0000-0000-000000000001' references businesses(id),
  description text not null,
  amount numeric(12, 2) not null check (amount >= 0),
  spent_on date not null,
  category text,
  created_at timestamptz not null default now()
);

create table if not exists cash_records (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null default '00000000-0000-0000-0000-000000000001' references businesses(id),
  record_type text not null check (record_type in ('opening_cash', 'expected_inflow')),
  amount numeric(12, 2) not null,
  record_date date not null,
  note text,
  created_at timestamptz not null default now()
);

create table if not exists model_predictions (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null default '00000000-0000-0000-0000-000000000001' references businesses(id),
  model_name text not null check (model_name in ('item_price', 'purchase_spend')),
  model_version text not null,
  product_id uuid references products(id) on delete cascade,
  target_date date,
  horizon text,
  predicted_value numeric(14, 2),
  lower_bound numeric(14, 2),
  upper_bound numeric(14, 2),
  baseline_name text,
  baseline_value numeric(14, 2),
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists model_predictions_lookup_idx
  on model_predictions (business_id, model_name, product_id, target_date);

-- ---------------------------------------------------------------------------------------------
-- Evidence and audit: every raw agent output (so nothing is re-sent to OpenAI) and every
-- correction, confirmation and verification.
-- ---------------------------------------------------------------------------------------------
create table if not exists extraction_runs (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null default '00000000-0000-0000-0000-000000000001' references businesses(id),
  invoice_id uuid references invoices(id) on delete set null,
  agent text not null,
  model text,
  guardrail_passed boolean,
  raw_output jsonb,
  error text,
  duration_ms integer,
  created_at timestamptz not null default now()
);

create table if not exists audit_log (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null default '00000000-0000-0000-0000-000000000001' references businesses(id),
  invoice_id uuid references invoices(id) on delete set null,
  event_type text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists audit_log_invoice_idx on audit_log (invoice_id, created_at);

-- ---------------------------------------------------------------------------------------------
-- Vector search for find_sku_candidates. Returns aliases ordered by cosine similarity.
-- ---------------------------------------------------------------------------------------------
create or replace function match_product_aliases(
  p_business_id uuid,
  p_embedding vector(1536),
  p_match_count integer default 15
)
returns table (
  alias_id uuid,
  product_id uuid,
  product_name text,
  product_sku text,
  alias_text text,
  similarity double precision
)
language sql
stable
set search_path = public, extensions
as $$
  select a.id, p.id, p.name, p.sku, a.alias_text, 1 - (a.embedding <=> p_embedding)
  from product_aliases a
  join products p on p.id = a.product_id
  where a.business_id = p_business_id
    and a.confirmed
    and a.embedding is not null
  order by a.embedding <=> p_embedding
  limit p_match_count;
$$;
