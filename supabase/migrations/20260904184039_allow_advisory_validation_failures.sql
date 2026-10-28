-- A built CAD draft is publishable for human review even when an advisory
-- requirement measurement is marked failed. The API and graph already treat
-- these measurements as evidence rather than a publication gate. Keep the
-- report shape guard, but do not reject failed advisory checks here.
create or replace function public.publish_revision_v2(
  p_run uuid,
  p_worker text,
  p_revision uuid,
  p_summary text,
  p_manifest jsonb,
  p_snapshot jsonb,
  p_artifacts jsonb,
  p_report jsonb,
  p_restored uuid default null
)
returns uuid
language plpgsql
security invoker
set search_path=''
as $$
declare revision uuid;
begin
  perform 1 from public.runs where id=p_run and backend_version=3 for update;
  if not found then raise exception 'RUN_NOT_FOUND'; end if;
  if not exists(
    select 1 from public.run_private
    where run_id=p_run and lease_owner=p_worker and lease_until>now()
  ) then raise exception 'LEASE_LOST'; end if;

  -- Requirement failures remain visible in p_report for the human reviewer;
  -- only a missing validation identity or requirement list blocks publishing.
  if p_report->'identity' is null or p_report->'requirements' is null then
    raise exception 'VALIDATION_REQUIRED';
  end if;

  revision:=public.publish_revision(
    p_run,p_revision,p_summary,p_manifest,p_snapshot,p_artifacts,p_restored
  );
  update public.revisions set validation=p_report where id=revision;
  return revision;
end
$$;

revoke all on function public.publish_revision_v2(uuid,text,uuid,text,jsonb,jsonb,jsonb,jsonb,uuid)
  from public,anon,authenticated;
grant execute on function public.publish_revision_v2(uuid,text,uuid,text,jsonb,jsonb,jsonb,jsonb,uuid)
  to service_role;
