create function public.append_research_record(p_run uuid,p_worker text,p_generation bigint,p_record jsonb)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare incoming public.project_records; saved public.project_records; project uuid;
begin
  perform public.check_research_fence(p_run,p_worker,p_generation);
  select project_id into project from public.runs where id=p_run;
  incoming:=jsonb_populate_record(null::public.project_records,p_record);
  if incoming.run_id is distinct from p_run or incoming.project_id is distinct from project then
    raise exception 'RECORD_OWNERSHIP_MISMATCH'; end if;
  insert into public.project_records(id,project_id,run_id,kind,record_key,body,candidate_hash,
    source_ids,supersedes,component_id,requirement_id)
    values(incoming.id,project,p_run,incoming.kind,incoming.record_key,incoming.body,incoming.candidate_hash,
      incoming.source_ids,incoming.supersedes,incoming.component_id,incoming.requirement_id)
    on conflict(project_id,record_key) do nothing;
  select * into saved from public.project_records where project_id=project and record_key=incoming.record_key;
  -- Evidence for identical immutable candidate inputs may be shared by a later
  -- run in the same project. Its origin run and creation time remain unchanged.
  if (to_jsonb(saved)-'created_at'-'run_id') is distinct from
     (to_jsonb(incoming)-'created_at'-'run_id') then raise exception 'IMMUTABLE_PROJECT_RECORD'; end if;
  return to_jsonb(saved);
end $$;
revoke all on function public.append_research_record(uuid,text,bigint,jsonb) from public,anon,authenticated;
grant execute on function public.append_research_record(uuid,text,bigint,jsonb) to service_role;
