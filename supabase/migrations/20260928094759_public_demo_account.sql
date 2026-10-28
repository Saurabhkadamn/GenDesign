-- A shared reviewer login is explicitly marked and capped before a run is
-- admitted. Keep the check inside the same transaction as run creation so
-- concurrent public visitors cannot race past the limit.
alter table public.profiles
  add column if not exists is_public_demo boolean not null default false;

create or replace function public.submit_run_v3(
  p_project uuid, p_owner uuid, p_base uuid, p_message text,
  p_selected jsonb, p_key uuid, p_environment text
) returns uuid language plpgsql security invoker set search_path='' as $$
declare r uuid; demo boolean;
begin
  select is_public_demo into demo from public.profiles
    where id=p_owner and active and not must_change_password;
  if not found then raise exception 'ACCOUNT_INACTIVE'; end if;

  if demo then
    perform pg_advisory_xact_lock(862453);
    -- Retries of the same submission must still return the existing run.
    select id into r from public.runs
      where project_id=p_project and owner_id=p_owner and idempotency_key=p_key;
    if r is null and (
      select count(*) from public.runs
      where owner_id=p_owner and created_at >= now() - interval '24 hours'
    ) >= 6 then
      raise exception 'PUBLIC_DEMO_DAILY_LIMIT';
    end if;
  end if;

  r := public.submit_run(p_project,p_owner,p_base,p_message,p_selected,p_key);
  if exists(select 1 from public.runs
            where id=r and backend_version<>3 and workflow_id is not null) then
    raise exception 'RUNTIME_MISMATCH';
  end if;
  update public.runs set backend_version=3,execution_environment=p_environment
    where id=r and workflow_id is null;
  return r;
end $$;

revoke all on function public.submit_run_v3(uuid,uuid,uuid,text,jsonb,uuid,text)
  from public,anon,authenticated;
grant execute on function public.submit_run_v3(uuid,uuid,uuid,text,jsonb,uuid,text)
  to service_role;
