# Operating Forma

The deployment is cloud-only: Python/FastAPI under Vercel Services, Vercel Sandbox for CAD, hosted Supabase for application and LangGraph state, and LangSmith for tracing. Docker is not required. See [CLOUD.md](CLOUD.md) for the current runbook.

## Before inviting an engineer

1. Apply the migration to a dedicated Supabase project. Disable public email signup in Auth settings.
2. Configure the server and publishable environment variables from `apps/web/.env.example`.
3. Create the administrator with `npm run bootstrap:admin`. The command hides password entry.
4. Create the pinned runtime snapshot with `npm run runtime:snapshot`. This consumes sandbox resources; run it deliberately.
5. Configure the snapshot ID and runtime version on the web deployment.
6. Sign in, enter an OpenRouter model ID and key, run the connection test, and activate the configuration.
7. Run a complete real session before inviting a tester: part → dimensional edit → nested assembly → calculation → export → restore.

The model field accepts any OpenRouter model ID, paid or free. Save, test the exact model's tool calling, and activate it deliberately; Forma never substitutes another model. `OPENROUTER_FREE_ONLY=false` is configured for the current deployment.

Owner-authorized testing exception: the deployed instance has `OPENROUTER_NEMOTRON_TESTING=true`. The exact `nvidia/nemotron-3-ultra-550b-a55b:free` endpoint may log prompts; use synthetic designs. The coordinator's `deepseek/deepseek-v4-flash-0731` connection has also passed a structured tool check and is active. Turn the testing exception off before using private designs.

If a previously tested connection fails a new connection test, it is deactivated. Quota, access, privacy-filter, and timeout failures pause active agent work with a safe message and preserve the saved design. Change the connection or wait for the provider before continuing; repeated blind retries consume free quotas.

CAD and engineering roles use their own override only after it has passed testing and been activated. Missing, inactive, or untested specialist overrides inherit the active, tested default; their saved credentials are left unchanged. A connection test always tests the exact saved role, never the inherited default. If no usable connection exists, the run pauses with settings and Continue instructions. Provider failures never trigger automatic model switching.

Keep the encryption master key backed up securely. Losing it makes saved API keys unreadable. Rotate model credentials through admin settings;
do not rotate the master key without re-encrypting the existing envelopes.

## Limits and recovery

- Only one run may hold the global execution slot. Other requests queue.
- Model calls and repairs are bounded. A paused run requires explicit continuation.
- Each generated command has a timeout. Sandboxes also auto-expire as a backstop.
- CPU usage is conservatively reserved by wall-clock seconds, not a claim about the provider's billable CPU accounting.
- A valid CAD B-rep is not evidence of safe engineering design. Users must supply and review engineering inputs.
- History preserves source snapshots. Export retention applies to generated artifacts; restoring a pruned revision rebuilds its source.
- The emergency stop takes effect at the next short execution boundary. Existing private projects remain readable.
- Source snapshots, model envelopes, generation records, and run checkpoints have no authenticated Data API grants.

If a workflow is interrupted, inspect its run ID in Vercel and its corresponding `runs`/`run_private` records using authorized server tools.
Never paste raw private checkpoints into a support ticket: they contain source, prompts, and private model output.

## Known release gates

The local build and tests do not establish hosted integration readiness. Verify the actual Supabase Auth settings, RLS/Storage policies,
Vercel snapshot creation and user permissions, Workflow replay behavior, the selected model, network denial, cancellation, and artifact retention
on the new deployment before enabling tester accounts. No existing inactive Supabase project has been repurposed.

## Local renderer verification

After running the Python fixtures with `--basetemp=test-results/python`, run `node scripts/verify-viewer.mjs`.
This starts a read-only localhost page on port 3001 with the actual exported assembly GLB and the same renderer used by the app.
It does not mock an AI run or connect to a user's project. Stop the server when inspection is complete.

`node scripts/verify-viewer.mjs --nemotron` loads the reviewed Nemotron plate's actual GLB if its validation artifacts exist in `test-results/models/reviewed-plate/verified-40`. This option never executes the candidate source.

The CAD environment is locked with uv, including platform-specific wheels. The production execution target is Linux in Vercel Sandbox.
Check the Python process exit code as well as pytest's assertion summary: a native-library shutdown failure must not be reported as a clean pass.
The Windows lock uses CasADi 3.6.7: the newer wheel produced a native shutdown failure when loaded with NLopt through CadQuery.
Do not remove that platform-specific pin without rerunning both import-exit and constraint-solver checks.

## Production migration

Keep project IDs, revision IDs, source snapshots, component IDs, instance IDs, and artifact manifests stable. Replace adapters, not the domain model.
Introduce measured concurrency, provider quota monitoring, object-store accounting, isolated build caching, job reconciliation, and load tests before
expanding beyond one engineer. No code automatically upgrades an infrastructure plan or adds billing.
