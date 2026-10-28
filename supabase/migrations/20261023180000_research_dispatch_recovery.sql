-- Dispatch intent and user continuation payload must survive the HTTP worker.
alter table public.execution_outbox add column updated_at timestamptz not null default now();
alter table public.execution_outbox drop constraint execution_outbox_status_check;
alter table public.execution_outbox add constraint execution_outbox_status_check
  check(status in ('pending','creating','prepared','dispatched','ambiguous','complete','cancelled'));

create function public.enqueue_research_dispatch() returns trigger
language plpgsql security invoker set search_path='' as $$
declare generation bigint;
begin
  if new.harness_version<>4 or new.status<>'queued' then return new; end if;
  select lease_generation into generation from public.run_private where run_id=new.id;
  if generation is null then return new; end if;
  if not exists(select 1 from public.execution_outbox where run_id=new.id
    and operation_key in ('dispatch:'||generation,'continuation:'||generation)) then
    insert into public.execution_outbox(run_id,operation_key,payload)
      values(new.id,'dispatch:'||generation,'{}') on conflict do nothing;
  end if;
  return new;
end $$;
create trigger research_dispatch_intent after insert or update of status,harness_version on public.runs
  for each row execute function public.enqueue_research_dispatch();

create function public.resume_research_run(p_run uuid,p_owner uuid,p_environment text,p_resume jsonb)
returns void language plpgsql security invoker set search_path='' as $$
declare generation bigint;
begin
  perform 1 from public.runs where id=p_run and owner_id=p_owner and harness_version=4
    and execution_environment=p_environment and status in ('paused','waiting_input') for update;
  if not found then raise exception 'RUN_NOT_RESUMABLE'; end if;
  update public.run_private set lease_generation=lease_generation+1,lease_owner=null,lease_until=null
    where run_id=p_run returning lease_generation into generation;
  insert into public.execution_outbox(run_id,operation_key,payload)
    values(p_run,'dispatch:'||generation,jsonb_build_object('resume',p_resume));
  update public.runs set status='queued',workflow_id=null,error=null,task_outcome='in_progress',updated_at=now()
    where id=p_run;
end $$;

create or replace function public.rollover_research_run(p_run uuid,p_worker text,p_generation bigint,p_payload jsonb)
returns uuid language plpgsql security invoker set search_path='' as $$
declare r public.runs; item public.execution_outbox; value jsonb;
begin
  select * into r from public.runs where id=p_run and harness_version=4 for update;
  if r.id is null then raise exception 'RUN_NOT_ACTIVE'; end if;
  value:=p_payload||jsonb_build_object('originWorker',p_worker,'originGeneration',p_generation);
  select * into item from public.execution_outbox where run_id=p_run and operation_key='continuation:'||p_generation;
  if item.id is not null then
    if item.payload<>value then raise exception 'CONTINUATION_INPUT_MISMATCH'; end if;
    return item.id; -- replay cannot reset a running successor's lease or status
  end if;
  perform public.check_research_fence(p_run,p_worker,p_generation);
  insert into public.execution_outbox(run_id,operation_key,payload)
    values(p_run,'continuation:'||p_generation,value) returning * into item;
  update public.run_private set lease_owner=null,lease_until=null where run_id=p_run;
  update public.runs set status='queued',workflow_id=null,updated_at=now() where id=p_run;
  return item.id;
end $$;

create or replace function public.reserve_research_dispatch(p_run uuid,p_worker text,p_resume jsonb)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare r public.runs; item public.execution_outbox; generation bigint;
begin
  select * into r from public.runs where id=p_run and harness_version=4 for update;
  if r.id is null or r.status<>'queued' then return null; end if;
  if exists(select 1 from public.app_settings where (settings->>'emergencyStop')::boolean) then return null; end if;
  select lease_generation into generation from public.run_private where run_id=p_run;
  select * into item from public.execution_outbox where run_id=p_run
    and operation_key in ('dispatch:'||generation,'continuation:'||generation)
    order by operation_key limit 1 for update;
  if item.id is null then raise exception 'DISPATCH_INTENT_MISSING'; end if;
  if p_resume is not null and item.payload->'resume' is distinct from p_resume then
    raise exception 'DISPATCH_INPUT_MISMATCH';
  end if;
  if item.status='pending' then
    update public.execution_outbox set status='creating',worker_id=p_worker,updated_at=now()
      where id=item.id returning * into item;
  end if;
  return to_jsonb(item);
end $$;

create or replace function public.prepare_research_dispatch(p_id uuid,p_worker text,p_workflow text,p_metadata jsonb)
returns boolean language plpgsql security invoker set search_path='' as $$
declare item public.execution_outbox; r public.runs;
begin
  select * into item from public.execution_outbox where id=p_id;
  select * into r from public.runs where id=item.run_id for update;
  select * into item from public.execution_outbox where id=p_id for update;
  if item.status not in ('creating','ambiguous') or item.worker_id<>p_worker then
    raise exception 'DISPATCH_OWNERSHIP_LOST';
  end if;
  if r.status<>'queued' or r.workflow_id is not null then
    update public.execution_outbox set status='cancelled',workflow_id=p_workflow,
      dispatch_metadata=p_metadata,updated_at=now() where id=p_id;
    return false;
  end if;
  update public.execution_outbox set status='prepared',workflow_id=p_workflow,
    dispatch_metadata=p_metadata,updated_at=now() where id=p_id;
  update public.runs set workflow_id=p_workflow,dispatch_at=now(),task_outcome='in_progress',error=null where id=r.id;
  return true;
end $$;

create function public.fail_research_dispatch(p_id uuid,p_worker text,p_uncertain boolean)
returns void language plpgsql security invoker set search_path='' as $$
declare item public.execution_outbox;
begin
  select * into item from public.execution_outbox where id=p_id;
  perform 1 from public.runs where id=item.run_id for update;
  update public.execution_outbox set status=case when p_uncertain then 'ambiguous' else 'pending' end,updated_at=now()
    where id=p_id and worker_id=p_worker and status='creating';
  if found and p_uncertain then
    update public.runs set task_outcome='blocked',error='Scheduler creation outcome is uncertain. Saved work is preserved; reconcile the existing dispatch before creating another scheduler.'
      where id=item.run_id and status='queued';
  end if;
end $$;

create function public.complete_research_dispatch(p_id uuid,p_workflow text)
returns boolean language plpgsql security invoker set search_path='' as $$
declare item public.execution_outbox; r public.runs;
begin
  select * into item from public.execution_outbox where id=p_id;
  select * into r from public.runs where id=item.run_id for update;
  update public.execution_outbox set status='dispatched',updated_at=now()
    where id=p_id and status in ('prepared','dispatched') and workflow_id=p_workflow
      and r.workflow_id=p_workflow and r.status in ('queued','running');
  return found;
end $$;

create function public.expire_research_dispatches(p_environment text)
returns integer language plpgsql security invoker set search_path='' as $$
declare item record; count integer:=0;
begin
  for item in select o.id,o.worker_id from public.execution_outbox o join public.runs r on r.id=o.run_id
    where o.status='creating' and o.updated_at<now()-interval '10 minutes'
      and r.execution_environment=p_environment
  loop
    perform public.fail_research_dispatch(item.id,item.worker_id,true);
    count:=count+1;
  end loop;
  return count;
end $$;

create function public.record_research_progress(p_run uuid,p_worker text,p_generation bigint,p_model_calls integer)
returns void language plpgsql security invoker set search_path='' as $$
begin
  perform public.check_research_fence(p_run,p_worker,p_generation);
  update public.runs set model_calls=greatest(model_calls,p_model_calls),updated_at=now() where id=p_run;
end $$;

revoke all on function public.enqueue_research_dispatch() from public,anon,authenticated;
revoke all on function public.resume_research_run(uuid,uuid,text,jsonb) from public,anon,authenticated;
revoke all on function public.fail_research_dispatch(uuid,text,boolean) from public,anon,authenticated;
revoke all on function public.complete_research_dispatch(uuid,text) from public,anon,authenticated;
revoke all on function public.expire_research_dispatches(text) from public,anon,authenticated;
revoke all on function public.record_research_progress(uuid,text,bigint,integer) from public,anon,authenticated;
grant execute on function public.enqueue_research_dispatch() to service_role;
grant execute on function public.resume_research_run(uuid,uuid,text,jsonb) to service_role;
grant execute on function public.fail_research_dispatch(uuid,text,boolean) to service_role;
grant execute on function public.complete_research_dispatch(uuid,text) to service_role;
grant execute on function public.expire_research_dispatches(text) to service_role;
grant execute on function public.record_research_progress(uuid,text,bigint,integer) to service_role;
