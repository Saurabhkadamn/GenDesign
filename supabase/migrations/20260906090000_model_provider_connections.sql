-- Keep the original OpenRouter rows valid while allowing any OpenAI-compatible
-- chat-completions endpoint to be selected per role.
alter table public.model_configs
  add column if not exists provider text not null default 'openrouter'
    check (provider in ('openrouter', 'openai_compatible')),
  add column if not exists base_url text;

alter table public.model_configs
  drop constraint if exists model_configs_base_url_no_credentials;

alter table public.model_configs
  add constraint model_configs_base_url_no_credentials check (
    base_url is null or (base_url not like '%@%' and base_url not like '%?%')
  );
