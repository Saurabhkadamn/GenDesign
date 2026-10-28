-- Drawing jobs use existing project ownership, global run admission and leases.
alter table public.runs add column drawing_request jsonb;
alter table public.runs add constraint runs_drawing_request_shape check (
  drawing_request is null or (jsonb_typeof(drawing_request)='object'
    and case when jsonb_typeof(drawing_request->'sheets')='array'
      then jsonb_array_length(drawing_request->'sheets') between 1 and 50 else false end
    and octet_length(drawing_request::text)<=180000)
);

create function public.submit_drawing_run(
  p_project uuid,p_owner uuid,p_base uuid,p_key uuid,p_environment text,p_request jsonb
) returns uuid language plpgsql security invoker set search_path='' as $$
declare r uuid; prior public.runs;
begin
  if p_base is null or jsonb_typeof(p_request) is distinct from 'object'
    or jsonb_typeof(p_request->'sheets') is distinct from 'array' then
    raise exception 'INVALID_DRAWING_REQUEST';
  end if;
  if jsonb_array_length(p_request->'sheets') not between 1 and 50
    or octet_length(p_request::text)>180000 then
    raise exception 'INVALID_DRAWING_REQUEST';
  end if;
  -- Serialize with ordinary run submission before inspecting a reused key.
  -- A queued chat request must never be converted into a drawing request.
  perform 1 from public.projects where id=p_project and owner_id=p_owner for update;
  if not found then raise exception 'Project not found'; end if;
  select * into prior from public.runs where project_id=p_project and owner_id=p_owner
    and idempotency_key=p_key for update;
  if found and (prior.drawing_request is null or prior.drawing_request is distinct from p_request
    or prior.base_revision_id is distinct from p_base or prior.execution_environment is distinct from p_environment
    or prior.backend_version<>3) then
    raise exception 'IDEMPOTENCY_CONFLICT';
  end if;
  r:=public.submit_run_v3(p_project,p_owner,p_base,'Generate revision-linked engineering drawing drafts.','[]'::jsonb,p_key,p_environment);
  update public.runs set drawing_request=p_request where id=r and drawing_request is null;
  return r;
end $$;
revoke all on function public.submit_drawing_run(uuid,uuid,uuid,uuid,text,jsonb) from public,anon,authenticated;
grant execute on function public.submit_drawing_run(uuid,uuid,uuid,uuid,text,jsonb) to service_role;

alter table public.artifacts drop constraint artifacts_kind_check;
alter table public.artifacts add constraint artifacts_kind_check check(kind in ('step','glb','plot','bom','assembly','drawing'));
update storage.buckets set allowed_mime_types=array(
  select distinct mime from unnest(allowed_mime_types || array['image/svg+xml','application/pdf','image/vnd.dxf']) as mime
) where id='cad-private' and allowed_mime_types is not null;
