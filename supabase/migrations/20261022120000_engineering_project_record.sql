-- Additive v4 state. Existing graph checkpoints and accepted artifacts are untouched.
alter table public.runs add column task_outcome text not null default 'in_progress'
  check(task_outcome in ('in_progress','satisfied','incomplete','blocked'));
alter table public.runs add column research_mode boolean not null default false;
alter table public.runs add column harness_version integer not null default 3;
alter table public.runs add column predecessor_run_id uuid references public.runs(id);
alter table public.revisions add column acceptance_state text not null default 'draft'
  check(acceptance_state in ('draft','ready_for_review','accepted'));
alter table public.generations add column provenance jsonb not null default '{}';

create table public.project_records (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id),
  run_id uuid references public.runs(id),
  kind text not null check(kind in ('source','requirement','decision','task','experiment','evidence','event')),
  record_key text not null,
  body jsonb not null,
  source_ids uuid[] not null default '{}',
  supersedes uuid unique references public.project_records(id),
  candidate_hash text,
  component_id text,
  requirement_id text,
  created_at timestamptz not null default now(),
  unique(project_id,record_key)
);
create index project_records_page on public.project_records(project_id,created_at,id);
create index project_records_candidate on public.project_records(project_id,candidate_hash);
create index project_records_requirement on public.project_records(project_id,requirement_id);
alter table public.project_records enable row level security;
revoke all on public.project_records from public,anon,authenticated;
grant select,insert on public.project_records to service_role;

create function public.capture_project_message() returns trigger
language plpgsql security definer set search_path='' as $$
begin
  insert into public.project_records(id,project_id,run_id,kind,record_key,body)
  values(new.id,new.project_id,new.run_id,'source','message:'||new.id,
    jsonb_build_object('type','message','role',new.role,'content',new.content,'messageId',new.id))
  on conflict(project_id,record_key) do nothing;
  return new;
end $$;
revoke all on function public.capture_project_message() from public,anon,authenticated;
create trigger capture_project_message after insert on public.messages
  for each row execute function public.capture_project_message();
insert into public.project_records(id,project_id,run_id,kind,record_key,body,created_at)
select id,project_id,run_id,'source','message:'||id,
  jsonb_build_object('type','message','role',role,'content',content,'messageId',id),created_at
from public.messages on conflict(project_id,record_key) do nothing;

-- A draft connection never replaces the working connection before qualification.
create table public.model_config_candidates (like public.model_configs including defaults including constraints);
alter table public.model_config_candidates add primary key(role);
alter table public.model_config_candidates enable row level security;
revoke all on public.model_config_candidates from public,anon,authenticated;
grant select,insert,update,delete on public.model_config_candidates to service_role;
create function public.activate_model_candidate(p_role text,p_version integer) returns boolean
language plpgsql security invoker set search_path='' as $$
declare c public.model_config_candidates;
begin
  select * into c from public.model_config_candidates where role=p_role for update;
  if c.role is null or c.version<>p_version or c.tested_at is null then return false; end if;
  insert into public.model_configs(role,provider,base_url,model_id,max_output_tokens,
    encrypted_key,key_hint,active,tested_at,version,updated_at)
  values(c.role,c.provider,c.base_url,c.model_id,c.max_output_tokens,
    c.encrypted_key,c.key_hint,true,c.tested_at,c.version,now())
  on conflict(role) do update set provider=excluded.provider,base_url=excluded.base_url,
    model_id=excluded.model_id,max_output_tokens=excluded.max_output_tokens,
    encrypted_key=excluded.encrypted_key,key_hint=excluded.key_hint,active=true,
    tested_at=excluded.tested_at,version=excluded.version,updated_at=excluded.updated_at;
  delete from public.model_config_candidates where role=p_role and version=p_version;
  return true;
end $$;
revoke all on function public.activate_model_candidate(text,integer) from public,anon,authenticated;
grant execute on function public.activate_model_candidate(text,integer) to service_role;
