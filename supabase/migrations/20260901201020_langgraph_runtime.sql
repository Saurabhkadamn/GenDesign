-- Private candidate workspace. LangGraph checkpoints store only graph state and references.
create table public.run_candidates (
  run_id uuid primary key references public.runs(id) on delete cascade,
  snapshot jsonb not null,
  candidate_hash text not null,
  updated_at timestamptz not null default now()
);
alter table public.run_candidates enable row level security;
revoke all on public.run_candidates from public,anon,authenticated;
grant all on public.run_candidates to service_role;

create function public.finish_graph_run_v3(p_run uuid,p_worker text,p_status text,p_message text)
returns void language plpgsql security invoker set search_path='' as $$
declare r public.runs;
begin
  select * into r from public.runs where id=p_run and backend_version=2 for update;
  if r.id is null then raise exception 'RUN_NOT_FOUND'; end if;
  if not exists(select 1 from public.run_private where run_id=p_run and lease_owner=p_worker) then
    raise exception 'LEASE_LOST';
  end if;
  if r.status='cancelled' then p_status:='cancelled'; p_message:='Work stopped. Published revisions are preserved.'; end if;
  if p_status not in ('paused','waiting_input','succeeded','failed','cancelled') then raise exception 'INVALID_STATUS'; end if;
  insert into public.messages(project_id,run_id,role,content) values(r.project_id,p_run,'assistant',p_message)
    on conflict(run_id,role) do update set content=excluded.content;
  update public.runs set status=p_status,workflow_id=null,
    error=case when p_status='failed' then p_message else null end,updated_at=now() where id=p_run;
  update public.run_private set lease_owner=null,lease_until=null where run_id=p_run;
end $$;

create function public.resume_graph_run_v3(p_run uuid,p_owner uuid,p_environment text)
returns void language plpgsql security invoker set search_path='' as $$
begin
  update public.runs set status='queued',workflow_id=null,error=null,updated_at=now()
    where id=p_run and owner_id=p_owner and backend_version=2 and execution_environment=p_environment
      and status in ('paused','waiting_input');
  if not found then raise exception 'RUN_NOT_RESUMABLE'; end if;
  update public.run_private set lease_owner=null,lease_until=null where run_id=p_run;
end $$;

revoke all on function public.finish_graph_run_v3(uuid,text,text,text) from public,anon,authenticated;
revoke all on function public.resume_graph_run_v3(uuid,uuid,text) from public,anon,authenticated;
grant execute on function public.finish_graph_run_v3(uuid,text,text,text) to service_role;
grant execute on function public.resume_graph_run_v3(uuid,uuid,text) to service_role;
