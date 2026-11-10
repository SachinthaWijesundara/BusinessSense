-- 02_storage.sql: the private Supabase Storage buckets for invoice photos and trained models.
--
-- Creates the "invoice-images" bucket. The backend uploads each original photo and its cleaned
-- (CLAHE) copy with the service-role key and hands the browser short-lived signed links, so the
-- photos (supplier names, balances) are never publicly readable.
-- If an "invoice-images" bucket already exists (the first prototype made a public one), this
-- leaves it as is: switch it to private in Supabase > Storage, since the frontend now gets
-- photos only through the backend's signed links.

insert into storage.buckets (id, name, public)
values ('invoice-images', 'invoice-images', false)
on conflict (id) do nothing;

-- "models": the shop's trained predictive models (Model 1 item price, later Model 2), uploaded by
-- backend/ml/train.py and loaded by the backend. Private: they are built from the shop's invoices.
insert into storage.buckets (id, name, public)
values ('models', 'models', false)
on conflict (id) do nothing;
