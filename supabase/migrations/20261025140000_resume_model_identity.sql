-- Explicit human continuation starts fresh model requests after every prior
-- attempt, including a reply whose generation row/checkpoint was interrupted.
-- Prior receipts stay available; a changed provider or correction cannot collide
-- with the old request's immutable input identity.
create function public.next_research_model_ordinal(p_run uuid,p_worker text,p_generation bigint,p_minimum integer)
returns integer language plpgsql security invoker set search_path='' as $$
declare next_ordinal integer;
begin
  perform public.check_research_fence(p_run,p_worker,p_generation);
  select greatest(p_minimum,
    coalesce((select max(ordinal)+1 from public.generations where run_id=p_run),0),
    coalesce((select max(substring(operation_key from '^graph:[^:]+:([0-9]+)$')::integer)+1
      from public.run_operations where run_id=p_run and kind='model'),0)) into next_ordinal;
  return next_ordinal;
end $$;
revoke all on function public.next_research_model_ordinal(uuid,text,bigint,integer) from public,anon,authenticated;
grant execute on function public.next_research_model_ordinal(uuid,text,bigint,integer) to service_role;
