-- Python service state is isolated from legacy TypeScript workflow checkpoints.
alter table public.runs add column backend_version integer not null default 1;
alter table public.runs add column execution_environment text not null default 'legacy';
alter table public.run_private add column checkpoint_version integer not null default 0;
alter table public.run_events add column stage text;
alter table public.run_events add column attempt integer;
alter table public.run_events add column elapsed_ms double precision;
alter table public.revisions add column validation jsonb;
create index runs_environment_queue on public.runs(execution_environment, created_at)
  where backend_version=2 and status in ('queued','running');

create table public.run_operations (
  run_id uuid not null references public.runs(id) on delete cascade,
  operation_key text not null,
  kind text not null,
  status text not null check(status in ('started','complete','failed','ambiguous')),
  result jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key(run_id, operation_key)
);
create table public.trace_outbox (
  id uuid primary key default gen_random_uuid(),
  run_id uuid references public.runs(id) on delete cascade,
  span_key text not null unique,
  payload jsonb not null,
  status text not null default 'pending' check(status in ('pending','sent','failed')),
  attempts integer not null default 0,
  next_attempt_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  sent_at timestamptz,
  execution_environment text not null
);
create index trace_outbox_run on public.trace_outbox(run_id);
create index trace_outbox_pending on public.trace_outbox(execution_environment,next_attempt_at)
  where status='pending';
alter table public.run_operations enable row level security;
alter table public.trace_outbox enable row level security;
revoke all on public.run_operations,public.trace_outbox from public,anon,authenticated;
grant all on public.run_operations,public.trace_outbox to service_role;

create function public.submit_run_v2(p_project uuid,p_owner uuid,p_base uuid,p_message text,p_selected jsonb,p_key uuid,p_environment text)
returns uuid language plpgsql security invoker set search_path='' as $$
declare r uuid;
begin
  r := public.submit_run(p_project,p_owner,p_base,p_message,p_selected,p_key);
  if exists(select 1 from public.runs where id=r and backend_version=2 and execution_environment<>p_environment) then
    raise exception 'ENVIRONMENT_MISMATCH';
  end if;
  update public.runs set backend_version=2,execution_environment=p_environment where id=r and workflow_id is null;
  return r;
end $$;

create function public.claim_run_v2(p_run uuid,p_worker text,p_environment text) returns boolean
language plpgsql security invoker set search_path='' as $$
declare r public.runs; locked_by text; locked_until timestamptz;
begin
  perform pg_advisory_xact_lock(862451);
  select * into r from public.runs where id=p_run for update;
  if r.id is null or r.backend_version<>2 or r.execution_environment<>p_environment or r.status not in ('queued','running') then return false; end if;
  select lease_owner,lease_until into locked_by,locked_until from public.run_private where run_id=p_run for update;
  if locked_by is not null and locked_by<>p_worker and locked_until>now() then return false; end if;
  -- Preserve the initial single execution slot across environments and the legacy service.
  if exists(select 1 from public.runs where status='running' and id<>p_run) then return false; end if;
  update public.runs set status='running',updated_at=now() where id=p_run;
  update public.run_private set lease_owner=p_worker,lease_until=now()+interval '6 minutes' where run_id=p_run;
  return true;
end $$;

create function public.checkpoint_run_v2(p_run uuid,p_worker text,p_version integer,p_checkpoint jsonb,p_model_calls integer)
returns integer language plpgsql security invoker set search_path='' as $$
declare v integer;
begin
  update public.run_private set checkpoint=p_checkpoint,checkpoint_version=checkpoint_version+1,
    lease_until=now()+interval '6 minutes'
    where run_id=p_run and lease_owner=p_worker and checkpoint_version=p_version
      and exists(select 1 from public.runs where id=p_run and backend_version=2 and status='running')
    returning checkpoint_version into v;
  if v is null then raise exception 'CHECKPOINT_CONFLICT'; end if;
  update public.runs set model_calls=p_model_calls,updated_at=now() where id=p_run;
  return v;
end $$;

create function public.finish_run_v2(p_run uuid,p_worker text,p_status text,p_message text,p_checkpoint jsonb)
returns void language plpgsql security invoker set search_path='' as $$
declare r public.runs;
begin
  select * into r from public.runs where id=p_run and backend_version=2 for update;
  if r.id is null then raise exception 'RUN_NOT_FOUND'; end if;
  if not exists(select 1 from public.run_private where run_id=p_run and lease_owner=p_worker) then raise exception 'LEASE_LOST'; end if;
  if r.status='cancelled' then p_status:='cancelled'; p_message:='Work stopped. Your last saved design is unchanged.'; end if;
  if p_status not in ('paused','waiting_input','succeeded','failed','cancelled') then raise exception 'INVALID_STATUS'; end if;
  insert into public.messages(project_id,run_id,role,content) values(r.project_id,p_run,'assistant',p_message)
    on conflict(run_id,role) do update set content=excluded.content;
  update public.runs set status=p_status,error=case when p_status='failed' then p_message else null end,updated_at=now() where id=p_run;
  update public.run_private set checkpoint=p_checkpoint,checkpoint_version=checkpoint_version+1,lease_owner=null,lease_until=null where run_id=p_run;
end $$;

create function public.resume_run_v2(p_run uuid,p_owner uuid,p_environment text) returns void
language plpgsql security invoker set search_path='' as $$
begin
  if not exists(select 1 from public.runs where id=p_run and owner_id=p_owner and backend_version=2 and execution_environment=p_environment) then raise exception 'ENVIRONMENT_MISMATCH'; end if;
  perform public.resume_run(p_run,p_owner);
  update public.run_private set checkpoint=jsonb_set(checkpoint,'{modelCalls}','0'::jsonb),checkpoint_version=checkpoint_version+1 where run_id=p_run;
end $$;

create function public.publish_revision_v2(p_run uuid,p_worker text,p_revision uuid,p_summary text,p_manifest jsonb,p_snapshot jsonb,p_artifacts jsonb,p_report jsonb,p_restored uuid default null)
returns uuid language plpgsql security invoker set search_path='' as $$
declare revision uuid;
begin
  perform 1 from public.runs where id=p_run and backend_version=2 for update;
  if not found then raise exception 'RUN_NOT_FOUND'; end if;
  if not exists(select 1 from public.run_private where run_id=p_run and lease_owner=p_worker and lease_until>now()) then raise exception 'LEASE_LOST'; end if;
  if p_report->'identity' is null or p_report->'requirements' is null or exists(
    select 1 from jsonb_array_elements(p_report->'requirements') x where x->>'status'='failed'
  ) then raise exception 'VALIDATION_REQUIRED'; end if;
  revision:=public.publish_revision(p_run,p_revision,p_summary,p_manifest,p_snapshot,p_artifacts,p_restored);
  update public.revisions set validation=p_report where id=revision;
  return revision;
end $$;
revoke all on function public.publish_revision_v2(uuid,text,uuid,text,jsonb,jsonb,jsonb,jsonb,uuid) from public,anon,authenticated;
grant execute on function public.publish_revision_v2(uuid,text,uuid,text,jsonb,jsonb,jsonb,jsonb,uuid) to service_role;

revoke all on function public.submit_run_v2(uuid,uuid,uuid,text,jsonb,uuid,text) from public,anon,authenticated;
revoke all on function public.claim_run_v2(uuid,text,text) from public,anon,authenticated;
revoke all on function public.checkpoint_run_v2(uuid,text,integer,jsonb,integer) from public,anon,authenticated;
revoke all on function public.finish_run_v2(uuid,text,text,text,jsonb) from public,anon,authenticated;
revoke all on function public.resume_run_v2(uuid,uuid,text) from public,anon,authenticated;
grant execute on function public.submit_run_v2(uuid,uuid,uuid,text,jsonb,uuid,text) to service_role;
grant execute on function public.claim_run_v2(uuid,text,text) to service_role;
grant execute on function public.checkpoint_run_v2(uuid,text,integer,jsonb,integer) to service_role;
grant execute on function public.finish_run_v2(uuid,text,text,text,jsonb) to service_role;
grant execute on function public.resume_run_v2(uuid,uuid,text) to service_role;
