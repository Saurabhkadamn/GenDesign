alter table public.artifacts drop constraint artifacts_kind_check;
alter table public.artifacts add constraint artifacts_kind_check
  check(kind in ('step','glb','plot','bom','assembly','drawing','notebook','report'));
