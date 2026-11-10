-- 03_row_level_security.sql: row-level security (proposal section 10).
--
-- Turns on RLS for every BusinessSense table. The FastAPI backend connects as the database owner
-- (DATABASE_URL), which bypasses RLS, so the app keeps working. A logged-in owner (Supabase Auth)
-- may read only their own business's rows, and the public anon key can read nothing.
--
-- WHEN TO APPLY: once nothing writes to Supabase with the public anon key. The frontend no longer
-- does (it only calls the backend), but the team's separate data-entry app may still write with
-- the anon key; apply this after that app goes through the backend or signs in.

create or replace function public.current_business_ids()
returns setof uuid
language sql
stable
security definer
set search_path = public
as $$
  select business_id from business_users where user_id = auth.uid()
  union
  select id from businesses where owner_user_id = auth.uid();
$$;

alter table businesses enable row level security;
alter table business_users enable row level security;
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
  for select to authenticated using (id in (select public.current_business_ids()));

drop policy if exists business_users_self_read on business_users;
create policy business_users_self_read on business_users
  for select to authenticated using (user_id = auth.uid());

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
