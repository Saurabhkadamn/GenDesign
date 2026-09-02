-- Applied by a Supabase migration. No direct client writes to the CAD state.
create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null, display_name text not null default 'Engineer',
  role text not null default 'engineer' check (role in ('admin','engineer')),
  active boolean not null default true, must_change_password boolean not null default true,
  created_at timestamptz not null default now()
);
create table public.projects (
  id uuid primary key default gen_random_uuid(), owner_id uuid not null references public.profiles(id),
  name text not null check (char_length(name) between 1 and 100), current_revision_id uuid,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index projects_owner_updated on public.projects(owner_id, updated_at desc);
create table public.runs (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
  owner_id uuid not null references public.profiles(id), base_revision_id uuid,
  status text not null default 'queued' check (status in ('queued','running','waiting_input','paused','succeeded','failed','cancelled')),
  message text not null check (char_length(message) between 1 and 12000), selected_ids jsonb not null default '[]',
  idempotency_key uuid not null, workflow_id text, dispatch_at timestamptz,
  error text, model_calls integer not null default 0,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  unique(project_id, idempotency_key)
);
create index runs_project_created on public.runs(project_id, created_at desc);
create index runs_queue on public.runs(created_at) where status in ('queued','running');
create unique index one_running_globally on public.runs((true)) where status = 'running';
create table public.revisions (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
  run_id uuid not null unique references public.runs(id), ordinal integer not null,
  summary text not null, manifest jsonb not null, restored_from uuid references public.revisions(id),
  created_at timestamptz not null default now(), unique(project_id, ordinal)
);
alter table public.projects add constraint projects_current_revision foreign key(current_revision_id) references public.revisions(id);
alter table public.runs add constraint runs_base_revision foreign key(base_revision_id) references public.revisions(id);
create table public.source_snapshots (
  revision_id uuid primary key references public.revisions(id) on delete cascade, snapshot jsonb not null
);
create table public.run_private (
  run_id uuid primary key references public.runs(id) on delete cascade,
  checkpoint jsonb not null default '{}', lease_owner text, lease_until timestamptz
);
create table public.messages (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
  run_id uuid not null references public.runs(id), role text not null check (role in ('user','assistant')),
  content text not null, created_at timestamptz not null default now(), unique(run_id, role)
);
create index messages_project_created on public.messages(project_id, created_at);
create table public.run_events (
  id bigint generated always as identity primary key, run_id uuid not null references public.runs(id) on delete cascade,
  kind text not null check (kind in ('status','tool','validation','error')),
  message text not null, created_at timestamptz not null default now()
);
create index run_events_run on public.run_events(run_id, id);
create table public.artifacts (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
  revision_id uuid not null references public.revisions(id) on delete cascade,
  component_id text, name text not null, kind text not null check (kind in ('step','glb','plot')),
  bytes bigint not null check (bytes > 0 and bytes <= 41943040), storage_path text not null,
  unique(revision_id, name)
);
create index artifacts_project_revision on public.artifacts(project_id, revision_id);
create table public.calculations (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
  run_id uuid not null references public.runs(id), revision_id uuid references public.revisions(id),
  result jsonb not null, stale boolean not null default false, reproducible boolean not null default false,
  created_at timestamptz not null default now()
);
create index calculations_project on public.calculations(project_id, created_at desc);
create table public.calculation_sources (
  calculation_id uuid primary key references public.calculations(id) on delete cascade,
  source text not null, runtime_version text not null
);
create table public.feedback (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id),
  revision_id uuid references public.revisions(id), run_id uuid references public.runs(id), owner_id uuid not null references public.profiles(id),
  content text not null check(char_length(content) between 1 and 4000), created_at timestamptz not null default now()
);
create index feedback_owner on public.feedback(owner_id);
create table public.model_configs (
  role text primary key check(role in ('coordinator','cad','engineering')),
  model_id text not null, encrypted_key text not null, key_hint text not null,
  active boolean not null default false, version integer not null default 1,
  tested_at timestamptz, updated_at timestamptz not null default now()
);
create table public.app_settings (
  id boolean primary key default true check(id), settings jsonb not null
);
insert into public.app_settings(id,settings) values(true, '{"emergencyStop":false,"engineeringEnabled":true,"surfacingEnabled":true,"limits":{"maxModelCalls":12,"maxRepairs":2,"commandTimeoutSeconds":300,"maxArtifactBytes":41943040,"retainedExports":5,"monthlySandboxSeconds":7200,"storageBudgetBytes":800000000}}');
create table public.generations (
  id uuid primary key, run_id uuid not null references public.runs(id), ordinal integer not null,
  role text not null, model_id text not null, config_version integer not null, prompt_version text not null,
  status text not null default 'started', output jsonb,
  input_tokens integer, output_tokens integer, created_at timestamptz not null default now(),
  unique(run_id, ordinal)
);
create index generations_run on public.generations(run_id);
create table public.resource_usage (
  id uuid primary key default gen_random_uuid(), run_id uuid not null references public.runs(id),
  operation_key text not null unique, reserved_seconds integer not null,
  sandbox_id text, created_at timestamptz not null default now()
);
create index usage_created on public.resource_usage(created_at);

-- The API performs mutations on the server with current profile checks.
-- RLS is a second boundary, including on tables deliberately withheld from the Data API.
do $$ declare t text; begin
  foreach t in array array['profiles','projects','runs','revisions','source_snapshots','run_private','messages','run_events','artifacts','calculations','calculation_sources','feedback','model_configs','app_settings','generations','resource_usage'] loop
    execute format('alter table public.%I enable row level security',t);
    execute format('revoke all on public.%I from anon, authenticated',t);
    execute format('grant all on public.%I to service_role',t);
  end loop;
end $$;
grant usage,select on sequence public.run_events_id_seq to service_role;
grant select on public.profiles,public.projects,public.runs,public.revisions,public.messages,public.run_events,public.artifacts,public.calculations,public.feedback to authenticated;
create policy profile_self on public.profiles for select to authenticated using(id=(select auth.uid()));
create policy projects_owner on public.projects for select to authenticated using(owner_id=(select auth.uid()) and exists(select 1 from public.profiles p where p.id=(select auth.uid()) and p.active and not p.must_change_password));
do $$ declare t text; begin
  foreach t in array array['runs','revisions','messages','artifacts','calculations','feedback'] loop
    execute format('create policy owner_read on public.%I for select to authenticated using(exists(select 1 from public.projects p where p.id=project_id and p.owner_id=(select auth.uid())))',t);
  end loop;
end $$;
create policy events_owner on public.run_events for select to authenticated using(exists(select 1 from public.runs r where r.id=run_id and r.owner_id=(select auth.uid())));

insert into storage.buckets(id,name,public,file_size_limit) values('cad-private','cad-private',false,41943040) on conflict(id) do nothing;
-- Deliberately no browser Storage policy: downloads are authorized by the server, using artifact IDs.

-- Invoker functions are executable ONLY by service_role. They implement atomic state transitions.
create function public.submit_run(p_project uuid,p_owner uuid,p_base uuid,p_message text,p_selected jsonb,p_key uuid)
returns uuid language plpgsql security invoker set search_path='' as $$
declare r uuid; current_id uuid;
begin
  select id into r from public.runs where project_id=p_project and idempotency_key=p_key and owner_id=p_owner;
  if r is not null then return r; end if;
  select current_revision_id into current_id from public.projects where id=p_project and owner_id=p_owner for update;
  if not found then raise exception 'Project not found'; end if;
  if current_id is distinct from p_base then raise exception 'STALE_REVISION'; end if;
  insert into public.runs(project_id,owner_id,base_revision_id,message,selected_ids,idempotency_key)
    values(p_project,p_owner,p_base,p_message,p_selected,p_key) returning id into r;
  insert into public.run_private(run_id) values(r);
  insert into public.messages(project_id,run_id,role,content) values(p_project,r,'user',p_message);
  return r;
end $$;

create function public.resume_run(p_run uuid,p_owner uuid) returns void
language plpgsql security invoker set search_path='' as $$
begin
  perform 1 from public.runs where id=p_run and owner_id=p_owner and status='paused' for update;
  if not found then raise exception 'RUN_NOT_PAUSED'; end if;
  if not exists(select 1 from public.profiles where id=p_owner and active and not must_change_password) then raise exception 'ACCOUNT_INACTIVE'; end if;
  if (select (settings->>'emergencyStop')::boolean from public.app_settings where id=true) then raise exception 'EMERGENCY_STOP'; end if;
  update public.run_private set checkpoint=jsonb_set(jsonb_set(checkpoint,'{repairs}','0'::jsonb),'{protocolRepairs}','0'::jsonb),lease_owner=null,lease_until=null where run_id=p_run;
  update public.runs set status='queued',workflow_id=null,dispatch_at=null,error=null,model_calls=0,updated_at=now() where id=p_run;
end $$;

create function public.claim_run(p_run uuid,p_worker text) returns boolean
language plpgsql security invoker set search_path='' as $$
declare r public.runs; locked_by text; locked_until timestamptz;
begin
  perform pg_advisory_xact_lock(862451);
  select * into r from public.runs where id=p_run for update;
  if r.status not in ('queued','running') then return false; end if;
  select lease_owner,lease_until into locked_by,locked_until from public.run_private where run_id=p_run for update;
  if locked_by is not null and locked_by<>p_worker and locked_until>now() then return false; end if;
  if exists(select 1 from public.runs where status='running' and id<>p_run) then return false; end if;
  if r.status='queued' and exists(select 1 from public.runs where status='queued' and created_at<r.created_at) then return false; end if;
  update public.runs set status='running',updated_at=now() where id=p_run;
  update public.run_private set lease_owner=p_worker,lease_until=now()+interval '10 minutes' where run_id=p_run;
  return true;
end $$;

create function public.reserve_execution(p_run uuid,p_key text,p_seconds integer,p_budget integer) returns boolean
language plpgsql security invoker set search_path='' as $$
begin
  perform pg_advisory_xact_lock(862452);
  if exists(select 1 from public.resource_usage where operation_key=p_key) then return true; end if;
  if (select coalesce(sum(reserved_seconds),0) from public.resource_usage where created_at>=date_trunc('month',now()))+p_seconds>p_budget then return false; end if;
  insert into public.resource_usage(run_id,operation_key,reserved_seconds) values(p_run,p_key,p_seconds);
  return true;
end $$;

create function public.publish_revision(p_run uuid,p_revision uuid,p_summary text,p_manifest jsonb,p_snapshot jsonb,p_artifacts jsonb,p_restored uuid default null)
returns uuid language plpgsql security invoker set search_path='' as $$
declare r public.runs; pr public.projects; existing uuid; item jsonb; next_ordinal integer;
begin
  select id into existing from public.revisions where run_id=p_run;
  if existing is not null then return existing; end if;
  select * into r from public.runs where id=p_run for update;
  if r.status<>'running' then raise exception 'RUN_NOT_ACTIVE'; end if;
  if not exists(select 1 from public.profiles where id=r.owner_id and active and not must_change_password) then raise exception 'ACCOUNT_INACTIVE'; end if;
  if (select (settings->>'emergencyStop')::boolean from public.app_settings where id=true) then raise exception 'EMERGENCY_STOP'; end if;
  select * into pr from public.projects where id=r.project_id for update;
  if pr.current_revision_id is distinct from r.base_revision_id then raise exception 'STALE_REVISION'; end if;
  select coalesce(max(ordinal),0)+1 into next_ordinal from public.revisions where project_id=r.project_id;
  insert into public.revisions(id,project_id,run_id,ordinal,summary,manifest,restored_from)
    values(p_revision,r.project_id,p_run,next_ordinal,p_summary,p_manifest,p_restored);
  insert into public.source_snapshots(revision_id,snapshot) values(p_revision,p_snapshot);
  for item in select * from jsonb_array_elements(p_artifacts) loop
    insert into public.artifacts(project_id,revision_id,component_id,name,kind,bytes,storage_path)
      values(r.project_id,p_revision,item->>'componentId',item->>'name',item->>'kind',(item->>'bytes')::bigint,item->>'storagePath');
  end loop;
  update public.projects set current_revision_id=p_revision,updated_at=now() where id=r.project_id;
  update public.calculations set stale=true where project_id=r.project_id and revision_id is distinct from p_revision;
  return p_revision;
end $$;

revoke all on function public.submit_run(uuid,uuid,uuid,text,jsonb,uuid) from public,anon,authenticated;
revoke all on function public.claim_run(uuid,text) from public,anon,authenticated;
revoke all on function public.resume_run(uuid,uuid) from public,anon,authenticated;
revoke all on function public.reserve_execution(uuid,text,integer,integer) from public,anon,authenticated;
revoke all on function public.publish_revision(uuid,uuid,text,jsonb,jsonb,jsonb,uuid) from public,anon,authenticated;
grant execute on function public.submit_run(uuid,uuid,uuid,text,jsonb,uuid) to service_role;
grant execute on function public.claim_run(uuid,text) to service_role;
grant execute on function public.resume_run(uuid,uuid) to service_role;
grant execute on function public.reserve_execution(uuid,text,integer,integer) to service_role;
grant execute on function public.publish_revision(uuid,uuid,text,jsonb,jsonb,jsonb,uuid) to service_role;
