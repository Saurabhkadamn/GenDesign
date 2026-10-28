-- Cancellation invalidates the worker lease and pending dispatch in one commit.
create function public.cancel_research_run(p_run uuid,p_owner uuid)
returns boolean language plpgsql security invoker set search_path='' as $$
declare r public.runs;
begin
  select * into r from public.runs
    where id=p_run and owner_id=p_owner and harness_version=4 for update;
  if r.id is null then raise exception 'RUN_NOT_FOUND'; end if;
  if r.status not in ('queued','running','paused','waiting_input') then return false; end if;
  update public.run_private set lease_generation=lease_generation+1,
    lease_owner=null,lease_until=null where run_id=p_run;
  update public.execution_outbox set status='cancelled',updated_at=now()
    where run_id=p_run and status in ('pending','creating','prepared','dispatched','ambiguous');
  update public.runs set status='cancelled',task_outcome='incomplete',
    error='Work stopped by the project owner.',updated_at=now() where id=p_run;
  insert into public.messages(project_id,run_id,role,content)
    values(r.project_id,p_run,'assistant','Work stopped. Published revisions are preserved.');
  insert into public.run_events(run_id,kind,stage,message)
    values(p_run,'status','cancellation','Work stopped by the project owner.');
  return true;
end $$;
revoke all on function public.cancel_research_run(uuid,uuid) from public,anon,authenticated;
grant execute on function public.cancel_research_run(uuid,uuid) to service_role;
