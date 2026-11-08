-- Private bucket for original invoice photos and their CLAHE copies.
-- The backend uploads with the service-role key and hands out short-lived signed URLs, so the
-- photos (supplier names, balances) are never publicly readable.
-- If the first prototype already created a public "invoice-images" bucket, this leaves it as is;
-- switch it to private in Storage settings once the frontend reads images through the API.

insert into storage.buckets (id, name, public)
values ('invoice-images', 'invoice-images', false)
on conflict (id) do nothing;
