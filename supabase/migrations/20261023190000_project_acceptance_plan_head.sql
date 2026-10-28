create table public.project_acceptance_plans (
  project_id uuid primary key references public.projects(id),
  version text not null, record_id uuid not null references public.project_records(id),
  updated_at timestamptz not null default now()
);
alter table public.project_acceptance_plans enable row level security;
revoke all on public.project_acceptance_plans from public,anon,authenticated;
grant select,insert,update on public.project_acceptance_plans to service_role;

create function public.commit_acceptance_plan(p_run uuid,p_worker text,p_generation bigint,
  p_expected text,p_version text,p_record uuid)
returns void language plpgsql security invoker set search_path='' as $$
declare project uuid; current_version text; body jsonb;
begin
  perform public.check_research_fence(p_run,p_worker,p_generation);
  select project_id into project from public.runs where id=p_run;
  perform 1 from public.projects where id=project for update;
  select version into current_version from public.project_acceptance_plans where project_id=project;
  if current_version=p_version then return; end if;
  if current_version is distinct from p_expected then raise exception 'STALE_ACCEPTANCE_PLAN'; end if;
  select r.body into body from public.project_records r where id=p_record and project_id=project and kind='task';
  if body is null or body->>'version'<>p_version or body->>'baseVersion' is distinct from p_expected then
    raise exception 'ACCEPTANCE_PLAN_RECORD_MISMATCH';
  end if;
  insert into public.project_acceptance_plans(project_id,version,record_id) values(project,p_version,p_record)
    on conflict(project_id) do update set version=excluded.version,record_id=excluded.record_id,updated_at=now();
end $$;
revoke all on function public.commit_acceptance_plan(uuid,text,bigint,text,text,uuid) from public,anon,authenticated;
grant execute on function public.commit_acceptance_plan(uuid,text,bigint,text,text,uuid) to service_role;
