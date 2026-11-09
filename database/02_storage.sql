-- 02_storage.sql: the private Supabase Storage bucket for invoice photos.
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
