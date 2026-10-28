-- Run workers may append progress and stage files only while they own the lease.
create function public.append_research_event(p_run uuid,p_worker text,p_generation bigint,
  p_kind text,p_message text,p_stage text,p_attempt integer,p_elapsed_ms double precision)
returns bigint language plpgsql security invoker set search_path='' as $$
declare event_id bigint;
begin
  perform public.check_research_fence(p_run,p_worker,p_generation);
  insert into public.run_events(run_id,kind,message,stage,attempt,elapsed_ms)
    values(p_run,p_kind,p_message,p_stage,p_attempt,p_elapsed_ms)
    returning id into event_id;
  return event_id;
end $$;

create function public.stage_research_artifact(p_run uuid,p_worker text,p_generation bigint,
  p_project uuid,p_path text,p_bytes bigint)
returns boolean language plpgsql security invoker set search_path='' as $$
begin
  perform public.check_research_fence(p_run,p_worker,p_generation);
  if not exists(select 1 from public.runs where id=p_run and project_id=p_project) then
    raise exception 'ARTIFACT_PROJECT_MISMATCH';
  end if;
  insert into public.artifact_staging(storage_path,run_id,project_id,bytes)
    values(p_path,p_run,p_project,p_bytes)
    on conflict(storage_path) do nothing;
  if not exists(select 1 from public.artifact_staging
    where storage_path=p_path and run_id=p_run and project_id=p_project and bytes=p_bytes) then
    raise exception 'ARTIFACT_IDENTITY_MISMATCH';
  end if;
  return true;
end $$;

create function public.reserve_research_execution(p_run uuid,p_worker text,p_generation bigint,
  p_key text,p_seconds integer,p_budget integer)
returns boolean language plpgsql security invoker set search_path='' as $$
begin
  perform public.check_research_fence(p_run,p_worker,p_generation);
  return public.reserve_execution(p_run,p_key,p_seconds,p_budget);
end $$;

revoke all on function public.append_research_event(uuid,text,bigint,text,text,text,integer,double precision)
  from public,anon,authenticated;
revoke all on function public.stage_research_artifact(uuid,text,bigint,uuid,text,bigint)
  from public,anon,authenticated;
revoke all on function public.reserve_research_execution(uuid,text,bigint,text,integer,integer)
  from public,anon,authenticated;
grant execute on function public.append_research_event(uuid,text,bigint,text,text,text,integer,double precision)
  to service_role;
grant execute on function public.stage_research_artifact(uuid,text,bigint,uuid,text,bigint)
  to service_role;
grant execute on function public.reserve_research_execution(uuid,text,bigint,text,integer,integer)
  to service_role;
