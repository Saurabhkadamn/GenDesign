-- Keep a v4 pause reason on the run as well as in immutable message history.
-- Legacy v3 run lifecycle and its error semantics remain unchanged.
create or replace function public.finish_research_run(
  p_run uuid,p_worker text,p_generation bigint,p_status text,p_message text
)
returns void language plpgsql security invoker set search_path='' as $$
declare r public.runs;
begin
  select * into r from public.runs where id=p_run and harness_version=4 for update;
  if r.id is null then raise exception 'RUN_NOT_FOUND'; end if;
  if not exists(select 1 from public.run_private where run_id=p_run and lease_owner=p_worker
    and lease_generation=p_generation and lease_until>now()) then raise exception 'LEASE_LOST'; end if;
  if exists(select 1 from public.app_settings where (settings->>'emergencyStop')::boolean) then
    p_status:='paused';
    p_message:='Work paused by the emergency stop. Saved work and evidence are preserved.';
  end if;
  perform public.finish_graph_run_v3(p_run,p_worker,p_status,p_message);
  update public.runs set
    task_outcome=case when r.status='cancelled' then 'incomplete'
      when p_status in ('paused','waiting_input') then 'blocked'
      when task_outcome='in_progress' then 'incomplete' else task_outcome end,
    error=case when r.status<>'cancelled' and p_status='paused' then p_message
      else error end
  where id=p_run;
end $$;
