-- New artifacts use the same project/revision ownership and private bucket.
-- No authentication grants or storage access policies are expanded.
alter table public.artifacts drop constraint if exists artifacts_kind_check;
alter table public.artifacts add constraint artifacts_kind_check
  check (kind in ('step', 'glb', 'plot', 'bom', 'assembly'));

-- Preserve any existing restricted MIME set, adding only the new export types.
-- A NULL list already accepts supported types and does not need rewriting.
update storage.buckets
set allowed_mime_types = array(
  select distinct mime from unnest(allowed_mime_types || array['application/json', 'text/csv']) as mime
)
where id = 'cad-private' and allowed_mime_types is not null;
