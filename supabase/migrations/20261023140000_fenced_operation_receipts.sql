-- One input identity owns an operation key. Completed receipts are immutable.
create function public.record_research_operation(p_run uuid,p_worker text,p_generation bigint,
  p_key text,p_kind text,p_hash text,p_status text,p_result jsonb)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare previous public.run_operations;
begin
  perform public.check_research_fence(p_run,p_worker,p_generation);
  if p_hash is null or p_status not in ('started','complete','failed','ambiguous') then
    raise exception 'INVALID_OPERATION_RECEIPT'; end if;
  select * into previous from public.run_operations where run_id=p_run and operation_key=p_key for update;
  if previous.run_id is not null then
    if previous.input_hash is distinct from p_hash or previous.kind<>p_kind then raise exception 'OPERATION_INPUT_CONFLICT'; end if;
    if previous.status='complete' then
      if p_status<>'complete' or previous.result is distinct from p_result then raise exception 'IMMUTABLE_OPERATION_RECEIPT'; end if;
      return previous.result;
    end if;
    update public.run_operations set status=p_status,result=p_result,updated_at=now()
      where run_id=p_run and operation_key=p_key;
  else
    if p_status<>'started' then raise exception 'OPERATION_NOT_STARTED'; end if;
    insert into public.run_operations(run_id,operation_key,kind,input_hash,status,result)
      values(p_run,p_key,p_kind,p_hash,p_status,p_result);
  end if;
  return p_result;
end $$;

create or replace function public.finish_research_run(p_run uuid,p_worker text,p_generation bigint,p_status text,p_message text)
returns void language plpgsql security invoker set search_path='' as $$
declare r public.runs;
begin
  select * into r from public.runs where id=p_run and harness_version=4 for update;
  if r.id is null then raise exception 'RUN_NOT_FOUND'; end if;
  if not exists(select 1 from public.run_private where run_id=p_run and lease_owner=p_worker
    and lease_generation=p_generation and lease_until>now()) then raise exception 'LEASE_LOST'; end if;
  -- Finishing may release a cancelled/emergency-stopped worker. It may never
  -- turn that cleanup into publication or a successful engineering outcome.
  if exists(select 1 from public.app_settings where (settings->>'emergencyStop')::boolean) then
    p_status:='paused'; p_message:='Work paused by the emergency stop. Saved work and evidence are preserved.';
  end if;
  perform public.finish_graph_run_v3(p_run,p_worker,p_status,p_message);
  update public.runs set task_outcome=case when r.status='cancelled' then 'incomplete'
    when p_status in ('paused','waiting_input') then 'blocked'
    when task_outcome='in_progress' then 'incomplete' else task_outcome end where id=p_run;
end $$;
revoke all on function public.record_research_operation(uuid,text,bigint,text,text,text,text,jsonb) from public,anon,authenticated;
grant execute on function public.record_research_operation(uuid,text,bigint,text,text,text,text,jsonb) to service_role;
