# Forma

A chat-driven CAD workspace with a milk-white/sage interface, private Supabase data, independently verified STEP exports and a 3D GLB preview.

## Cloud services

- `apps/web`: Next.js/React presentation only. No TypeScript authentication, database, model or workflow server.
- `apps/api`: Python 3.12 FastAPI authentication, projects, model settings, agent tools, artifacts, administration and durable Vercel Workflows.
- `packages/core`: browser types generated from Python/Pydantic OpenAPI, plus generated defaults.
- `runtimes/python`: locked CadQuery/OCP runtime and deterministic STEP validator. Generated code executes only in Vercel Sandbox, never in the API or on the developer laptop.
- `supabase/migrations`: account and ownership rules, LangGraph persistence, fenced publication and operation ledgers.

Vercel Services routes `/api/*` to Python and all other requests to Next.js under one domain. The API service entrypoint is `pyproject.toml`, so the Python builder discovers the workflow registry. Production remains on its previous deployment until the new preview passes the hosted acceptance checks.

**No Docker or local CAD execution is required.** Application data and LangGraph checkpoints stay in the existing Supabase project. Traces go directly to LangSmith.

## Models

Admin → Models accepts any exact OpenRouter model ID, paid or free. The dropdown is only a suggestion list containing the full catalog. Save, test tool calling, then activate the connection. Specialists inherit the active default unless they have an active tested override. Provider failures never switch models automatically.

`OPENROUTER_FREE_ONLY=false` follows the owner's request to allow any selected model. Verification scripts use the requested Nemotron free endpoint. The supplied DeepSeek paid connection passed a structured tool-call check and is active for the coordinator; a complete paid CAD run is still untested. The exact Nemotron testing exception allows provider prompt retention; do not submit confidential designs while it is enabled.

## Tracing

Vercel server configuration uses the variables in `apps/api/.env.example`. Never put the LangSmith key, database URL, model keys or encryption key in frontend variables. LangGraph automatically traces graph transitions; custom OpenRouter and CAD boundaries use sanitized LangSmith instrumentation. Trace delivery is best-effort and cannot invalidate a CAD revision.

## Lightweight development checks

```sh
npm ci
npm run typecheck
npm test
npm run lint
uv sync --project apps/api
uv run --project apps/api pytest apps/api/tests
```

Generate browser contracts with the API environment's Python:

```sh
python scripts/generate_contracts.py
npx --yes openapi-typescript@7.13.0 packages/core/openapi.json -o packages/core/src/generated.ts
```

Cloud verification scripts under `scripts/` require ignored server configuration files in `test-results/`; they never print keys. `verify_execution.py` runs reviewed fixtures remotely and records preparation/build timings and independent geometric evidence. `verify_hosted_api.py` checks LangSmith and submits the exact mounting-plate request.

See [cloud operations](docs/CLOUD.md) and [verification evidence](docs/CLOUD_VERIFICATION.md). Historical implementation reports in the other documents describe the earlier TypeScript release and are not current deployment evidence.
