update public.runs set status='cancelled',updated_at=now()
where backend_version=2 and status in ('queued','running','paused','waiting_input');

create index runs_graph_environment_queue on public.runs(execution_environment, created_at)
  where backend_version=3 and status in ('queued','running');

create function public.submit_run_v3(p_project uuid,p_owner uuid,p_base uuid,p_message text,p_selected jsonb,p_key uuid,p_environment text)
returns uuid language plpgsql security invoker set search_path='' as $$
declare r uuid;
begin
  r := public.submit_run(p_project,p_owner,p_base,p_message,p_selected,p_key);
  if exists(select 1 from public.runs where id=r and backend_version<>3 and workflow_id is not null) then
    raise exception 'RUNTIME_MISMATCH';
  end if;
  update public.runs set backend_version=3,execution_environment=p_environment where id=r and workflow_id is null;
  return r;
end $$;

create function public.claim_run_v3(p_run uuid,p_worker text,p_environment text) returns boolean
language plpgsql security invoker set search_path='' as $$
declare r public.runs; locked_by text; locked_until timestamptz;
begin
  perform pg_advisory_xact_lock(862451);
  select * into r from public.runs where id=p_run for update;
  if r.id is null or r.backend_version<>3 or r.execution_environment<>p_environment or r.status not in ('queued','running') then return false; end if;
  select lease_owner,lease_until into locked_by,locked_until from public.run_private where run_id=p_run for update;
  if locked_by is not null and locked_by<>p_worker and locked_until>now() then return false; end if;
  if exists(select 1 from public.runs where status='running' and id<>p_run) then return false; end if;
  update public.runs set status='running',updated_at=now() where id=p_run;
  update public.run_private set lease_owner=p_worker,lease_until=now()+interval '6 minutes' where run_id=p_run;
  return true;
end $$;

create or replace function public.finish_graph_run_v3(p_run uuid,p_worker text,p_status text,p_message text)
returns void language plpgsql security invoker set search_path='' as $$
declare r public.runs;
begin
  select * into r from public.runs where id=p_run and backend_version=3 for update;
  if r.id is null then raise exception 'RUN_NOT_FOUND'; end if;
  if not exists(select 1 from public.run_private where run_id=p_run and lease_owner=p_worker) then raise exception 'LEASE_LOST'; end if;
  if r.status='cancelled' then p_status:='cancelled'; p_message:='Work stopped. Published revisions are preserved.'; end if;
  if p_status not in ('paused','waiting_input','succeeded','failed','cancelled') then raise exception 'INVALID_STATUS'; end if;
  insert into public.messages(project_id,run_id,role,content) values(r.project_id,p_run,'assistant',p_message)
    on conflict(run_id,role) do update set content=excluded.content;
  update public.runs set status=p_status,workflow_id=null,
    error=case when p_status='failed' then p_message else null end,updated_at=now() where id=p_run;
  update public.run_private set lease_owner=null,lease_until=null where run_id=p_run;
end $$;

create or replace function public.resume_graph_run_v3(p_run uuid,p_owner uuid,p_environment text)
returns void language plpgsql security invoker set search_path='' as $$
begin
  update public.runs set status='queued',workflow_id=null,error=null,updated_at=now()
    where id=p_run and owner_id=p_owner and backend_version=3 and execution_environment=p_environment
      and status in ('paused','waiting_input');
  if not found then raise exception 'RUN_NOT_RESUMABLE'; end if;
  update public.run_private set lease_owner=null,lease_until=null where run_id=p_run;
end $$;

revoke all on function public.submit_run_v3(uuid,uuid,uuid,text,jsonb,uuid,text) from public,anon,authenticated;
revoke all on function public.claim_run_v3(uuid,text,text) from public,anon,authenticated;
grant execute on function public.submit_run_v3(uuid,uuid,uuid,text,jsonb,uuid,text) to service_role;
grant execute on function public.claim_run_v3(uuid,text,text) to service_role;
