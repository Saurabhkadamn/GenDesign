alter table public.calculations add column candidate_hash text;
alter table public.calculations add column runtime_version text;
alter table public.calculations add column source_path text;

create function public.record_research_generation(p_run uuid,p_worker text,p_generation bigint,p_record jsonb)
returns void language plpgsql security invoker set search_path='' as $$
declare existing public.generations; incoming public.generations;
begin
  perform public.check_research_fence(p_run,p_worker,p_generation);
  incoming:=jsonb_populate_record(null::public.generations,p_record);
  incoming.provenance:=coalesce(incoming.provenance,'{}');
  if incoming.run_id is distinct from p_run or incoming.status<>'complete' then
    raise exception 'INVALID_GENERATION_RECEIPT'; end if;
  select * into existing from public.generations where run_id=p_run and ordinal=incoming.ordinal;
  if found then
    if (to_jsonb(existing)-'created_at') is distinct from
       (to_jsonb(incoming)-'created_at') then raise exception 'IMMUTABLE_GENERATION_RECEIPT'; end if;
    return;
  end if;
  insert into public.generations(id,run_id,ordinal,role,model_id,config_version,prompt_version,status,
    output,provenance,input_tokens,output_tokens)
    values(incoming.id,p_run,incoming.ordinal,incoming.role,incoming.model_id,incoming.config_version,
      incoming.prompt_version,incoming.status,incoming.output,coalesce(incoming.provenance,'{}'),
      incoming.input_tokens,incoming.output_tokens);
end $$;

create function public.record_research_calculation(p_run uuid,p_worker text,p_generation bigint,
  p_record jsonb,p_source text,p_path text,p_candidate text,p_runtime text)
returns void language plpgsql security invoker set search_path='' as $$
declare r public.runs; existing public.calculations; item_id uuid;
begin
  perform public.check_research_fence(p_run,p_worker,p_generation);
  select * into r from public.runs where id=p_run;
  if (p_record->>'run_id')::uuid is distinct from p_run
      or (p_record->>'project_id')::uuid is distinct from r.project_id
      or not exists(select 1 from public.run_candidates where run_id=p_run and candidate_hash=p_candidate)
      then raise exception 'STALE_CALCULATION_CANDIDATE'; end if;
  item_id:=(p_record->>'id')::uuid;
  select * into existing from public.calculations where id=item_id;
  if found then
    if existing.run_id is distinct from p_run or existing.result is distinct from p_record->'result'
        or existing.candidate_hash is distinct from p_candidate or existing.runtime_version is distinct from p_runtime
        or existing.source_path is distinct from p_path
        or not exists(select 1 from public.calculation_sources where calculation_id=item_id
          and source=p_source and runtime_version=p_runtime) then
      raise exception 'IMMUTABLE_CALCULATION_RECEIPT'; end if;
    return;
  end if;
  -- A calculation for an unpublished candidate must not be assigned to the
  -- earlier revision merely because that revision was the run's starting point.
  insert into public.calculations(id,project_id,run_id,result,reproducible,candidate_hash,runtime_version,source_path)
    values(item_id,r.project_id,p_run,p_record->'result',true,p_candidate,p_runtime,p_path);
  insert into public.calculation_sources(calculation_id,source,runtime_version)
    values(item_id,p_source,p_runtime);
end $$;

create function public.invalidate_candidate_calculations() returns trigger
language plpgsql security invoker set search_path='' as $$
begin
  if exists(select 1 from public.runs where id=new.run_id and harness_version=4) then
    update public.calculations set stale=(candidate_hash is distinct from new.candidate_hash)
      where project_id=(select project_id from public.runs where id=new.run_id);
  end if;
  return new;
end $$;
create trigger candidate_calculation_freshness after insert or update of candidate_hash on public.run_candidates
  for each row execute function public.invalidate_candidate_calculations();

create or replace function public.publish_research_revision(p_run uuid,p_worker text,p_generation bigint,
  p_revision uuid,p_summary text,p_manifest jsonb,p_snapshot jsonb,p_artifacts jsonb,p_report jsonb,p_restored uuid default null)
returns uuid language plpgsql security invoker set search_path='' as $$
declare result uuid;
begin
  perform public.check_research_fence(p_run,p_worker,p_generation);
  result:=public.publish_revision_v2(p_run,p_worker,p_revision,p_summary,p_manifest,p_snapshot,p_artifacts,p_report,p_restored);
  update public.revisions set acceptance_state=case
    when p_report#>>'{completion,acceptanceState}'='ready_for_review' then 'ready_for_review' else 'draft' end
    where id=result;
  update public.runs set task_outcome=case
    when p_report#>>'{completion,taskOutcome}'='satisfied' then 'satisfied' else 'incomplete' end where id=p_run;
  update public.calculations set revision_id=result,stale=false where run_id=p_run
    and candidate_hash=p_report#>>'{identity,candidate}' and runtime_version=p_report#>>'{identity,runtime}';
  return result;
end $$;
revoke all on function public.record_research_generation(uuid,text,bigint,jsonb) from public,anon,authenticated;
revoke all on function public.record_research_calculation(uuid,text,bigint,jsonb,text,text,text,text) from public,anon,authenticated;
revoke all on function public.invalidate_candidate_calculations() from public,anon,authenticated;
grant execute on function public.record_research_generation(uuid,text,bigint,jsonb) to service_role;
grant execute on function public.record_research_calculation(uuid,text,bigint,jsonb,text,text,text,text) to service_role;
grant execute on function public.invalidate_candidate_calculations() to service_role;
