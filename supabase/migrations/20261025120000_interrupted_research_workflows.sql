-- A failed Workflow step may never return its current lease token to its
-- scheduler. Recover using the immutable dispatch owner, not a freshly read
-- token that could belong to a successor. This transition only stops work.
create function public.interrupt_research_workflow(p_run uuid,p_worker text,p_message text)
returns text language plpgsql security invoker set search_path='' as $$
declare r public.runs;
begin
  select * into r from public.runs where id=p_run and harness_version=4 for update;
  if r.id is null then raise exception 'RUN_NOT_FOUND'; end if;
  if r.status not in ('queued','running') then return r.status; end if;
  if not exists(select 1 from public.execution_outbox where run_id=p_run
      and worker_id=p_worker and workflow_id=r.workflow_id
      and status in ('prepared','dispatched')) then return 'superseded'; end if;
  if exists(select 1 from public.run_private where run_id=p_run
      and lease_owner is not null and lease_owner<>p_worker) then return 'superseded'; end if;
  update public.run_private set lease_generation=lease_generation+1,
    lease_owner=null,lease_until=null where run_id=p_run;
  update public.execution_outbox set status='complete',updated_at=now()
    where run_id=p_run and worker_id=p_worker and workflow_id=r.workflow_id;
  update public.runs set status='paused',task_outcome='blocked',workflow_id=null,
    error=p_message,updated_at=now() where id=p_run;
  insert into public.messages(project_id,run_id,role,content)
    values(r.project_id,p_run,'assistant',p_message);
  insert into public.run_events(run_id,kind,stage,message)
    values(p_run,'status','recovery',p_message);
  return 'paused';
end $$;
revoke all on function public.interrupt_research_workflow(uuid,text,text) from public,anon,authenticated;
grant execute on function public.interrupt_research_workflow(uuid,text,text) to service_role;
