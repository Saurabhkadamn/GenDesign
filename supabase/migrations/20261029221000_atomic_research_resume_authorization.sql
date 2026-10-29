-- A stale Continue must not change operation receipts before the run is resumed.
-- The run row lock makes retry authorization, the user message and dispatch
-- intent one transaction. An interrupted caller can inspect the committed
-- state instead of issuing a second external operation.
create or replace function public.resume_research_run(
  p_run uuid, p_owner uuid, p_environment text, p_resume jsonb
) returns void language plpgsql security invoker set search_path='' as $$
declare r public.runs; generation bigint; operation public.run_operations;
begin
  select * into r from public.runs where id=p_run and owner_id=p_owner
    and harness_version=4 and execution_environment=p_environment
    and status in ('paused','waiting_input') for update;
  if r.id is null then raise exception 'RUN_NOT_RESUMABLE'; end if;

  update public.run_private set lease_generation=lease_generation+1,
    lease_owner=null,lease_until=null where run_id=p_run
    returning lease_generation into generation;

  if p_resume->>'kind'='continue' then
    for operation in select * from public.run_operations where run_id=p_run
      and kind in ('model','calculate','drawing_build')
      and status in ('started','ambiguous','failed') for update
    loop
      insert into public.project_records(
        project_id,run_id,kind,record_key,body
      ) values (
        r.project_id,p_run,'event',
        'retry-authorization:'||generation||':'||operation.operation_key,
        jsonb_build_object(
          'type','operation_retry_authorization',
          'operationKey',operation.operation_key,
          'previousStatus',operation.status,
          'previousResult',operation.result,
          'resumeKind','continue',
          'generation',generation
        )
      );
      update public.run_operations set status='failed',
        result=jsonb_build_object(
          'category','user_retry_authorized',
          'diagnostic','The user explicitly continued after an uncertain model or deterministic engineering operation.',
          'previous_category',operation.result->>'category',
          'previous_diagnostic',operation.result->>'diagnostic'
        ),updated_at=now()
        where run_id=p_run and operation_key=operation.operation_key;
    end loop;
  end if;

  if nullif(btrim(p_resume->>'message'),'') is not null then
    insert into public.messages(project_id,run_id,role,content)
      values(r.project_id,p_run,'user',btrim(p_resume->>'message'));
  end if;

  insert into public.execution_outbox(run_id,operation_key,payload)
    values(p_run,'dispatch:'||generation,jsonb_build_object('resume',p_resume));
  update public.runs set status='queued',workflow_id=null,error=null,
    task_outcome='in_progress',updated_at=now() where id=p_run;
end $$;
