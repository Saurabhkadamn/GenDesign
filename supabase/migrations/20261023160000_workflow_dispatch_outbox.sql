alter table public.execution_outbox drop constraint execution_outbox_status_check;
alter table public.execution_outbox add constraint execution_outbox_status_check
  check(status in ('pending','creating','prepared','dispatched','ambiguous','complete'));
alter table public.execution_outbox add column worker_id text,
  add column workflow_id text, add column dispatch_metadata jsonb not null default '{}';

create function public.reserve_research_dispatch(p_run uuid,p_worker text,p_resume jsonb)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare r public.runs; item public.execution_outbox; generation bigint;
begin
  select * into r from public.runs where id=p_run and harness_version=4 for update;
  if r.id is null or r.status<>'queued' then return null; end if;
  if exists(select 1 from public.app_settings where (settings->>'emergencyStop')::boolean) then return null; end if;
  select lease_generation into generation from public.run_private where run_id=p_run;
  select * into item from public.execution_outbox where run_id=p_run
    and operation_key in ('dispatch:'||generation,'continuation:'||generation) for update;
  if item.id is null then
    insert into public.execution_outbox(run_id,operation_key,payload)
      values(p_run,'dispatch:'||generation,jsonb_build_object('resume',p_resume)) returning * into item;
  end if;
  if item.status='pending' then
    update public.execution_outbox set status='creating',worker_id=p_worker where id=item.id returning * into item;
  end if;
  return to_jsonb(item);
end $$;

create function public.prepare_research_dispatch(p_id uuid,p_worker text,p_workflow text,p_metadata jsonb)
returns boolean language plpgsql security invoker set search_path='' as $$
declare item public.execution_outbox;
begin
  select * into item from public.execution_outbox where id=p_id for update;
  if item.status<>'creating' or item.worker_id<>p_worker then raise exception 'DISPATCH_OWNERSHIP_LOST'; end if;
  update public.execution_outbox set status='prepared',workflow_id=p_workflow,dispatch_metadata=p_metadata where id=p_id;
  update public.runs set workflow_id=p_workflow,dispatch_at=now() where id=item.run_id and status='queued' and workflow_id is null;
  return found;
end $$;
revoke all on function public.reserve_research_dispatch(uuid,text,jsonb) from public,anon,authenticated;
revoke all on function public.prepare_research_dispatch(uuid,text,text,jsonb) from public,anon,authenticated;
grant execute on function public.reserve_research_dispatch(uuid,text,jsonb) to service_role;
grant execute on function public.prepare_research_dispatch(uuid,text,text,jsonb) to service_role;
