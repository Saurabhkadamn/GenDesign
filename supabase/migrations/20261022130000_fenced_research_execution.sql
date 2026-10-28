alter table public.run_private add column lease_generation bigint not null default 0;
alter table public.run_candidates add column candidate_version bigint not null default 0;
alter table public.run_operations add column input_hash text;
drop index public.one_running_globally;
-- Legacy runs retain their original single-run admission rule while v4 runs
-- use per-project fencing and independent scheduling.
create unique index one_running_v3_globally on public.runs((true))
  where status = 'running' and harness_version = 3;
create unique index one_legacy_running_globally on public.runs((true))
  where status='running' and harness_version<4;
create unique index one_research_writer_per_project on public.runs(project_id)
  where status='running' and harness_version>=4;
create table public.candidate_versions (
  run_id uuid not null references public.runs(id), version bigint not null,
  snapshot jsonb not null, candidate_hash text not null, parent_hash text,
  created_at timestamptz not null default now(), primary key(run_id,version)
);
alter table public.candidate_versions enable row level security;
revoke all on public.candidate_versions from public,anon,authenticated;
grant select,insert on public.candidate_versions to service_role;

create table public.execution_outbox (
  id uuid primary key default gen_random_uuid(), run_id uuid not null references public.runs(id),
  operation_key text not null, payload jsonb not null,
  status text not null default 'pending' check(status in ('pending','dispatched','complete')),
  created_at timestamptz not null default now(), unique(run_id,operation_key)
);
alter table public.execution_outbox enable row level security;
revoke all on public.execution_outbox from public,anon,authenticated;
grant select,insert,update on public.execution_outbox to service_role;

create function public.claim_research_run(p_run uuid,p_worker text,p_environment text,p_concurrency integer)
returns bigint language plpgsql security invoker set search_path='' as $$
declare r public.runs; token bigint;
begin
  perform pg_advisory_xact_lock(862451);
  select * into r from public.runs where id=p_run for update;
  if r.id is null or r.harness_version<>4 or r.execution_environment<>p_environment
      or r.status not in ('queued','running') then return null; end if;
  if exists(select 1 from public.app_settings where (settings->>'emergencyStop')::boolean) then return null; end if;
  if exists(select 1 from public.run_private where run_id=p_run and lease_owner<>p_worker and lease_until>now()) then return null; end if;
  -- Legacy execution keeps its original global isolation until it finishes.
  if exists(select 1 from public.runs x join public.run_private p on p.run_id=x.id
    where x.id<>p_run and x.status='running' and p.lease_until>now()
      and x.harness_version<4) then return null; end if;
  if exists(select 1 from public.runs where project_id=r.project_id and status='running' and id<>p_run) then return null; end if;
  if (select count(*) from public.runs x join public.run_private p on p.run_id=x.id
    where x.id<>p_run and x.status='running' and p.lease_until>now()) >= greatest(1,p_concurrency) then return null; end if;
  update public.run_private set lease_generation=lease_generation+1,
    lease_owner=p_worker,lease_until=now()+interval '6 minutes'
    where run_id=p_run returning lease_generation into token;
  if token is null then return null; end if;
  update public.runs set status='running',updated_at=now() where id=p_run;
  return token;
end $$;

create function public.commit_research_candidate(p_run uuid,p_worker text,p_generation bigint,
  p_expected_version bigint,p_hash text,p_snapshot jsonb) returns bigint
language plpgsql security invoker set search_path='' as $$
declare r public.runs; current_version bigint; previous_hash text; next_version bigint;
begin
  select * into r from public.runs where id=p_run for update;
  if r.harness_version<>4 or r.status<>'running' then raise exception 'RUN_NOT_ACTIVE'; end if;
  if not exists(select 1 from public.run_private where run_id=p_run and lease_owner=p_worker
    and lease_generation=p_generation and lease_until>now()) then raise exception 'LEASE_LOST'; end if;
  select candidate_version,candidate_hash into current_version,previous_hash
    from public.run_candidates where run_id=p_run for update;
  current_version:=coalesce(current_version,0);
  if current_version<>p_expected_version then raise exception 'STALE_CANDIDATE'; end if;
  if previous_hash=p_hash then return current_version; end if;
  next_version:=current_version+1;
  insert into public.candidate_versions(run_id,version,snapshot,candidate_hash,parent_hash)
    values(p_run,next_version,p_snapshot,p_hash,previous_hash);
  insert into public.run_candidates(run_id,snapshot,candidate_hash,candidate_version)
    values(p_run,p_snapshot,p_hash,next_version)
    on conflict(run_id) do update set snapshot=excluded.snapshot,candidate_hash=excluded.candidate_hash,
      candidate_version=excluded.candidate_version,updated_at=now();
  insert into public.project_records(project_id,run_id,kind,record_key,body,candidate_hash)
    values(r.project_id,p_run,'event','candidate:'||p_run||':'||next_version,
      jsonb_build_object('candidateVersion',next_version,'parentHash',previous_hash),p_hash);
  return next_version;
end $$;

create function public.rollover_research_run(p_run uuid,p_worker text,p_generation bigint,p_payload jsonb)
returns uuid language plpgsql security invoker set search_path='' as $$
declare outbox_id uuid;
begin
  perform 1 from public.runs where id=p_run and harness_version=4 and status='running' for update;
  if not found then raise exception 'RUN_NOT_ACTIVE'; end if;
  if not exists(select 1 from public.run_private where run_id=p_run and lease_owner=p_worker
    and lease_generation=p_generation and lease_until>now()) then raise exception 'LEASE_LOST'; end if;
  insert into public.execution_outbox(run_id,operation_key,payload)
    values(p_run,'continuation:'||p_generation,p_payload)
    on conflict(run_id,operation_key) do update set payload=excluded.payload returning id into outbox_id;
  update public.runs set status='queued',workflow_id=null,updated_at=now() where id=p_run;
  update public.run_private set lease_owner=null,lease_until=null where run_id=p_run;
  return outbox_id;
end $$;
revoke all on function public.claim_research_run(uuid,text,text,integer) from public,anon,authenticated;
revoke all on function public.commit_research_candidate(uuid,text,bigint,bigint,text,jsonb) from public,anon,authenticated;
revoke all on function public.rollover_research_run(uuid,text,bigint,jsonb) from public,anon,authenticated;
grant execute on function public.claim_research_run(uuid,text,text,integer) to service_role;
grant execute on function public.commit_research_candidate(uuid,text,bigint,bigint,text,jsonb) to service_role;
grant execute on function public.rollover_research_run(uuid,text,bigint,jsonb) to service_role;

create function public.check_research_fence(p_run uuid,p_worker text,p_generation bigint)
returns boolean language plpgsql security invoker set search_path='' as $$
begin
  perform 1 from public.runs where id=p_run and status='running' for update;
  if not found or exists(select 1 from public.app_settings where (settings->>'emergencyStop')::boolean)
    then raise exception 'RUN_NOT_ACTIVE'; end if;
  update public.run_private set lease_until=now()+interval '6 minutes'
    where run_id=p_run and lease_owner=p_worker and lease_generation=p_generation and lease_until>now();
  if not found then raise exception 'LEASE_LOST'; end if;
  return true;
end $$;

create function public.finish_research_run(p_run uuid,p_worker text,p_generation bigint,p_status text,p_message text)
returns void language plpgsql security invoker set search_path='' as $$
begin
  perform public.check_research_fence(p_run,p_worker,p_generation);
  perform public.finish_graph_run_v3(p_run,p_worker,p_status,p_message);
  update public.runs set task_outcome=case when p_status in ('paused','waiting_input') then 'blocked'
    when task_outcome='in_progress' then 'incomplete' else task_outcome end where id=p_run;
end $$;

create function public.publish_research_revision(p_run uuid,p_worker text,p_generation bigint,
  p_revision uuid,p_summary text,p_manifest jsonb,p_snapshot jsonb,p_artifacts jsonb,p_report jsonb,p_restored uuid default null)
returns uuid language plpgsql security invoker set search_path='' as $$
declare result uuid;
begin
  perform public.check_research_fence(p_run,p_worker,p_generation);
  result := public.publish_revision_v2(p_run,p_worker,p_revision,p_summary,p_manifest,p_snapshot,p_artifacts,p_report,p_restored);
  update public.revisions set acceptance_state=case
    when p_report#>>'{completion,acceptanceState}'='ready_for_review' then 'ready_for_review' else 'draft' end
    where id=result;
  update public.runs set task_outcome=case
    when p_report#>>'{completion,taskOutcome}'='satisfied' then 'satisfied' else 'incomplete' end where id=p_run;
  return result;
end $$;
revoke all on function public.check_research_fence(uuid,text,bigint) from public,anon,authenticated;
revoke all on function public.finish_research_run(uuid,text,bigint,text,text) from public,anon,authenticated;
revoke all on function public.publish_research_revision(uuid,text,bigint,uuid,text,jsonb,jsonb,jsonb,jsonb,uuid) from public,anon,authenticated;
grant execute on function public.check_research_fence(uuid,text,bigint) to service_role;
grant execute on function public.finish_research_run(uuid,text,bigint,text,text) to service_role;
grant execute on function public.publish_research_revision(uuid,text,bigint,uuid,text,jsonb,jsonb,jsonb,jsonb,uuid) to service_role;
