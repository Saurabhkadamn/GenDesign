-- Preserve every user clarification and assistant reply in a project chat.
-- Older runs already have one row per role and remain readable unchanged.
alter table public.messages drop constraint if exists messages_run_id_role_key;
create index if not exists messages_run_created_id
  on public.messages(run_id, created_at, id);

create or replace function public.finish_graph_run_v3(p_run uuid,p_worker text,p_status text,p_message text)
returns void language plpgsql security invoker set search_path='' as $$
declare r public.runs;
begin
  select * into r from public.runs where id=p_run and backend_version=3 for update;
  if r.id is null then raise exception 'RUN_NOT_FOUND'; end if;
  if not exists(select 1 from public.run_private where run_id=p_run and lease_owner=p_worker) then raise exception 'LEASE_LOST'; end if;
  if r.status='cancelled' then p_status:='cancelled'; p_message:='Work stopped. Published revisions are preserved.'; end if;
  if p_status not in ('paused','waiting_input','succeeded','failed','cancelled') then raise exception 'INVALID_STATUS'; end if;
  -- The lease and row lock fence this transition. A resumed clarification
  -- appends a new answer instead of replacing a prior question.
  insert into public.messages(project_id,run_id,role,content)
    values(r.project_id,p_run,'assistant',p_message);
  update public.runs set status=p_status,workflow_id=null,
    error=case when p_status='failed' then p_message else null end,updated_at=now() where id=p_run;
  update public.run_private set lease_owner=null,lease_until=null where run_id=p_run;
end $$;

revoke all on function public.finish_graph_run_v3(uuid,text,text,text) from public,anon,authenticated;
grant execute on function public.finish_graph_run_v3(uuid,text,text,text) to service_role;
