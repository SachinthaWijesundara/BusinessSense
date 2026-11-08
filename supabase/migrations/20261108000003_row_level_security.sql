-- Row-level security (proposal section 10: "row-level security in Supabase").
--
-- The FastAPI backend connects with the service role / database owner, which bypasses RLS.
-- These policies let a logged-in owner (Supabase Auth) read only their own business's rows,
-- and block the anonymous key entirely.
--
-- IMPORTANT: the first prototype's frontend writes to suppliers/invoices/invoice_items directly
-- with the anon key. Apply this migration only after the frontend reads and writes through the
-- FastAPI backend, or those screens will stop saving.

create or replace function public.current_business_ids()
returns setof uuid
language sql
stable
security definer
set search_path = public
as $$
  select id from businesses where owner_user_id = auth.uid();
$$;

alter table businesses enable row level security;
alter table suppliers enable row level security;
alter table products enable row level security;
alter table product_aliases enable row level security;
alter table invoices enable row level security;
alter table invoice_items enable row level security;
alter table supplier_payments enable row level security;
alter table expenses enable row level security;
alter table cash_records enable row level security;
alter table model_predictions enable row level security;
alter table extraction_runs enable row level security;
alter table audit_log enable row level security;

drop policy if exists businesses_owner_read on businesses;
create policy businesses_owner_read on businesses
  for select to authenticated using (owner_user_id = auth.uid());

do $$
declare
  t text;
begin
  foreach t in array array[
    'suppliers', 'products', 'product_aliases', 'invoices', 'supplier_payments',
    'expenses', 'cash_records', 'model_predictions', 'extraction_runs', 'audit_log'
  ]
  loop
    execute format('drop policy if exists %I on %I', t || '_owner_read', t);
    execute format(
      'create policy %I on %I for select to authenticated '
      'using (business_id in (select public.current_business_ids()))',
      t || '_owner_read', t
    );
  end loop;
end $$;

drop policy if exists invoice_items_owner_read on invoice_items;
create policy invoice_items_owner_read on invoice_items
  for select to authenticated
  using (
    invoice_id in (
      select id from invoices where business_id in (select public.current_business_ids())
    )
  );
