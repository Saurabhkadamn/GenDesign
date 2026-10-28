create table public.research_jobs (
  run_id uuid not null references public.runs(id), job_key text not null, kind text not null,
  input_hash text not null, input_state jsonb not null,
  status text not null default 'running' check(status in ('running','complete')),
  result jsonb, candidate_version bigint, created_at timestamptz not null default now(),
  completed_at timestamptz, primary key(run_id,job_key)
);
alter table public.research_jobs enable row level security;
revoke all on public.research_jobs from public,anon,authenticated;
grant select,insert,update on public.research_jobs to service_role;

create function public.begin_research_job(p_run uuid,p_worker text,p_generation bigint,
  p_key text,p_kind text,p_hash text,p_input jsonb) returns jsonb
language plpgsql security invoker set search_path='' as $$
declare j public.research_jobs;
begin
  perform public.check_research_fence(p_run,p_worker,p_generation);
  insert into public.research_jobs(run_id,job_key,kind,input_hash,input_state)
    values(p_run,p_key,p_kind,p_hash,p_input) on conflict(run_id,job_key) do nothing;
  select * into j from public.research_jobs where run_id=p_run and job_key=p_key;
  if j.input_hash<>p_hash or j.kind<>p_kind then raise exception 'JOB_INPUT_CONFLICT'; end if;
  return jsonb_build_object('status',j.status,'result',j.result,'candidateVersion',j.candidate_version);
end $$;

create function public.commit_research_job(p_run uuid,p_worker text,p_generation bigint,
  p_key text,p_hash text,p_result jsonb,p_expected_version bigint,p_candidate_hash text,p_snapshot jsonb)
returns bigint language plpgsql security invoker set search_path='' as $$
declare j public.research_jobs; version bigint;
begin
  perform public.check_research_fence(p_run,p_worker,p_generation);
  select * into j from public.research_jobs where run_id=p_run and job_key=p_key for update;
  if j.run_id is null or j.input_hash<>p_hash then raise exception 'JOB_INPUT_CONFLICT'; end if;
  if j.status='complete' then
    if j.result is distinct from p_result then raise exception 'IMMUTABLE_JOB_RECEIPT'; end if;
    return j.candidate_version;
  end if;
  version:=p_expected_version;
  if p_snapshot is not null then
    version:=public.commit_research_candidate(p_run,p_worker,p_generation,p_expected_version,p_candidate_hash,p_snapshot);
  elsif coalesce((select candidate_version from public.run_candidates where run_id=p_run),0)<>p_expected_version then
    raise exception 'STALE_CANDIDATE';
  end if;
  update public.research_jobs set status='complete',result=p_result,candidate_version=version,completed_at=now()
    where run_id=p_run and job_key=p_key;
  return version;
end $$;
revoke all on function public.begin_research_job(uuid,text,bigint,text,text,text,jsonb) from public,anon,authenticated;
revoke all on function public.commit_research_job(uuid,text,bigint,text,text,jsonb,bigint,text,jsonb) from public,anon,authenticated;
grant execute on function public.begin_research_job(uuid,text,bigint,text,text,text,jsonb) to service_role;
grant execute on function public.commit_research_job(uuid,text,bigint,text,text,jsonb,bigint,text,jsonb) to service_role;
