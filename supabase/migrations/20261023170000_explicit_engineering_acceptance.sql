create function public.accept_engineering_revision(p_project uuid,p_revision uuid,p_actor uuid,p_decision text)
returns boolean language plpgsql security invoker set search_path='' as $$
declare r public.revisions;
begin
  perform 1 from public.projects where id=p_project and owner_id=p_actor and current_revision_id=p_revision for update;
  if not found then raise exception 'CURRENT_OWNED_REVISION_REQUIRED'; end if;
  select * into r from public.revisions where id=p_revision and project_id=p_project for update;
  if r.acceptance_state='accepted' then return true; end if;
  if r.acceptance_state<>'ready_for_review' then raise exception 'REVIEW_READINESS_REQUIRED'; end if;
  if length(trim(p_decision))<8 or length(p_decision)>4000 then raise exception 'ENGINEERING_DECISION_REQUIRED'; end if;
  insert into public.project_records(project_id,run_id,kind,record_key,body,candidate_hash)
    values(p_project,r.run_id,'decision','acceptance:'||p_revision,
      jsonb_build_object('type','engineering_acceptance','revisionId',p_revision,'actorId',p_actor,
                        'decision',p_decision,'agentInterpretation',false),r.validation#>>'{identity,candidate}');
  update public.revisions set acceptance_state='accepted' where id=p_revision;
  return true;
end $$;
revoke all on function public.accept_engineering_revision(uuid,uuid,uuid,text) from public,anon,authenticated;
grant execute on function public.accept_engineering_revision(uuid,uuid,uuid,text) to service_role;
