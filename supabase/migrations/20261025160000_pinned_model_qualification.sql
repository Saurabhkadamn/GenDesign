-- Activation must use the exact model identity established by both protocol turns.
create or replace function public.activate_model_candidate(p_role text,p_version integer) returns boolean
language plpgsql security invoker set search_path='' as $$
declare c public.model_config_candidates;
begin
  perform pg_advisory_xact_lock(hashtext('forma-model:'||p_role));
  select * into c from public.model_config_candidates where role=p_role for update;
  if c.role is null or c.version<>p_version or c.tested_at is null
    or c.qualification->>'protocol' is distinct from 'multi_turn_tools_v1'
    or c.qualification->>'identityStable' is distinct from 'true'
    or c.qualification->>'requestedModel' is distinct from c.model_id
    or c.qualification->>'resolvedModel' is distinct from c.model_id then return false; end if;
  insert into public.model_configs(role,provider,base_url,model_id,max_output_tokens,
    encrypted_key,key_hint,active,tested_at,version,context_length,reasoning_token_reserve,data_collection,qualification)
  values(c.role,c.provider,c.base_url,c.model_id,c.max_output_tokens,c.encrypted_key,c.key_hint,true,c.tested_at,c.version,
    c.context_length,c.reasoning_token_reserve,c.data_collection,c.qualification)
  on conflict(role) do update set provider=excluded.provider,base_url=excluded.base_url,
    model_id=excluded.model_id,max_output_tokens=excluded.max_output_tokens,
    encrypted_key=excluded.encrypted_key,key_hint=excluded.key_hint,active=true,
    tested_at=excluded.tested_at,version=excluded.version,context_length=excluded.context_length,
    reasoning_token_reserve=excluded.reasoning_token_reserve,data_collection=excluded.data_collection,
    qualification=excluded.qualification,updated_at=now();
  delete from public.model_config_candidates where role=p_role and version=p_version;
  return true;
end $$;
