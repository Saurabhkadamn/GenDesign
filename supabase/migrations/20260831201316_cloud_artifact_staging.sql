-- Record object paths before upload so interrupted publication can be cleaned safely.
create table public.artifact_staging (
  storage_path text primary key,
  run_id uuid not null references public.runs(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  bytes bigint not null check(bytes >= 0),
  created_at timestamptz not null default now()
);
create index artifact_staging_run on public.artifact_staging(run_id);
create index artifact_staging_project on public.artifact_staging(project_id);
alter table public.artifact_staging enable row level security;
revoke all on public.artifact_staging from public,anon,authenticated;
grant all on public.artifact_staging to service_role;
