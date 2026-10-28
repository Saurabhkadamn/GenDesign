alter table public.model_configs
  add column context_length integer check(context_length>=4096),
  add column reasoning_token_reserve integer not null default 0 check(reasoning_token_reserve>=0),
  add column data_collection text not null default 'deny' check(data_collection in ('deny','allow')),
  add column qualification jsonb not null default '{}';
alter table public.model_config_candidates
  add column context_length integer check(context_length>=4096),
  add column reasoning_token_reserve integer not null default 0 check(reasoning_token_reserve>=0),
  add column data_collection text not null default 'deny' check(data_collection in ('deny','allow')),
  add column qualification jsonb not null default '{}';

create function public.stage_model_candidate(p_role text,p_expected_version integer,p_config jsonb)
returns integer language plpgsql security invoker set search_path='' as $$
declare current_version integer; next_version integer;
begin
  perform pg_advisory_xact_lock(hashtext('forma-model:'||p_role));
  select greatest(coalesce((select version from public.model_configs where role=p_role),0),
                  coalesce((select version from public.model_config_candidates where role=p_role),0)) into current_version;
  if current_version<>p_expected_version then raise exception 'STALE_MODEL_CONFIGURATION'; end if;
  next_version:=current_version+1;
  insert into public.model_config_candidates(role,provider,base_url,model_id,max_output_tokens,
    encrypted_key,key_hint,active,tested_at,version,context_length,reasoning_token_reserve,data_collection,qualification)
  values(p_role,p_config->>'provider',p_config->>'base_url',p_config->>'model_id',
    (p_config->>'max_output_tokens')::integer,p_config->>'encrypted_key',p_config->>'key_hint',false,null,next_version,
    (p_config->>'context_length')::integer,coalesce((p_config->>'reasoning_token_reserve')::integer,0),
    coalesce(p_config->>'data_collection','deny'),'{}')
  on conflict(role) do update set provider=excluded.provider,base_url=excluded.base_url,
    model_id=excluded.model_id,max_output_tokens=excluded.max_output_tokens,
    encrypted_key=excluded.encrypted_key,key_hint=excluded.key_hint,active=false,tested_at=null,
    version=excluded.version,context_length=excluded.context_length,
    reasoning_token_reserve=excluded.reasoning_token_reserve,data_collection=excluded.data_collection,
    qualification='{}',updated_at=now();
  return next_version;
end $$;

create or replace function public.activate_model_candidate(p_role text,p_version integer) returns boolean
language plpgsql security invoker set search_path='' as $$
declare c public.model_config_candidates;
begin
  perform pg_advisory_xact_lock(hashtext('forma-model:'||p_role));
  select * into c from public.model_config_candidates where role=p_role for update;
  if c.role is null or c.version<>p_version or c.tested_at is null
    or c.qualification->>'protocol' is distinct from 'multi_turn_tools_v1' then return false; end if;
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
revoke all on function public.stage_model_candidate(text,integer,jsonb) from public,anon,authenticated;
grant execute on function public.stage_model_candidate(text,integer,jsonb) to service_role;
