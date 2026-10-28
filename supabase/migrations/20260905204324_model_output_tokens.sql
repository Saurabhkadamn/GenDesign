-- Store an optional per-connection output budget for OpenAI-compatible
-- providers. NULL keeps the provider/application default.
alter table public.model_configs
  add column if not exists max_output_tokens integer;

alter table public.model_configs
  drop constraint if exists model_configs_max_output_tokens_range;

alter table public.model_configs
  add constraint model_configs_max_output_tokens_range check (
    max_output_tokens is null or max_output_tokens between 16 and 131072
  );
