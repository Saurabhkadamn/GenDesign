# Engineering harness v4 implementation

Baseline: c9205e1, branch codex/cad-observation-budget-20261022. The architecture
review and original offline probes are preserved as baseline evidence.

## Authorized scope

Implement R1–R10 from FORMA_BACKEND_ARCHITECTURE_REVIEW_2026-10-22.md. Retain
the existing geometry engines and scheduler. Trusted research runs have no
application-level run, spending, repair, or monthly compute quota. Provider
limits, cancellation, process isolation and per-operation safety remain.
No mechanical recipes or pump-specific runtime logic.

## Progress

- [ ] Stage 1: calculations, persistent intent, outcomes, staged connections.
- [ ] Stage 2: shared context, retrieval, iterative engineering, flexible recovery.
- [ ] Stage 3: fenced durable execution, continuation and concurrency.
- [ ] Stage 4: geometry evidence, configuration exports, reports and caching.
- [ ] Stage 5: local/hosted qualification and controlled rollout.

### Hosted qualification in progress (2026-10-28)

- Reconciled twelve historical migration timestamps with production's fetched
  migration history and restored the missing `publish_revision_v2` alignment
  migration. The reconciled checkout dry run listed only the seventeen v4
  migrations. Those migrations applied to production with v4 still disabled;
  the public application remains on its previous deployment. A partial unique
  index retains the legacy single-running-run admission rule while v4 uses
  project fencing. The database migration suite passed 13 checks afterward.
- Vercel Sandbox qualified snapshot `snap_YnZt8GCzut2Pm1Wb0Zu8sEL3u7xh`,
  runtime `forma-050bddb11f2526c8`, with 79 native tests and no skipped tests.
  A transient observer connection failure during native compilation was resumed
  from its recorded remote command; no compile command was duplicated.
- A protected v4 preview deployed successfully. Vercel Hobby rejected the
  five-minute recovery cron, so the cron is a daily safety sweep. Normal
  continuation is scheduled directly by Vercel Workflow; the sweep only
  reconciles pending outbox dispatch after an interruption. Qualify this
  recovery delay under crash tests before default rollout.
- Created a separate free Supabase evaluation project
  `weleqttflalzfjiyfbol`, applied the full reconciled migration history,
  initialized hardened LangGraph checkpoint tables, and created a dedicated
  evaluation administrator. Evaluation credentials are stored outside the
  repository and reports. The isolated preview is
  `https://forma-3jpj7aj35-negens-projects.vercel.app`, deployment
  `dpl_HH4runZgLwhEiBkrb9DhXrpR68pN`. Its health and admin login succeeded;
  its initially empty model list confirmed it is not using production data.
- Staged, passed two-turn tool-protocol checks, and activated pinned OpenRouter
  models in the evaluation database only: Nemotron 3 Ultra for coordination and
  Cohere North Mini Code for CAD. Both are free-tier connections; the provider
  is reachable, but task competence, throughput, and engineering data handling
  are not qualified. Production's older TheGrid aliases remain inactive and
  unchanged; their response model IDs differ from the saved alias names.
- Submitted the original external gear pump as fresh v4 research run
  `7e1bbd22-ec9f-4dbc-a57d-6c09b63b2158` in project
  `fed5049a-565f-42b6-be83-3169ad9e0658`. Submitted a distinct-geometry
  60-part conveyor fixture as run `bca774c9-f7b3-4862-9b3b-0fa026fec658`
  in project `56861ae0-f555-471e-80fc-b95cc4c6bf54`. At the last check the
  pump reached a saved candidate with 14 source files and 14 component
  definitions, then paused as blocked after ten model calls. The CAD provider
  rejected a multi-turn request with HTTP 400, reporting an invalid replayed
  tool call in the request history. The stored tool arguments parse as JSON
  objects, so the remaining fault is in provider-specific history transport
  or interpretation; it is not evidence that the geometry passed. The
  60-part task subsequently started and was still running at the last check.
  Neither has passed independent artifact acceptance.
- The pump's saved tool arguments contained a repeated JSON field. The local
  parser executed the final-key value, while the routed CAD provider rejected
  that original string when it appeared in the next request. Provider replay
  now re-encodes the already-parsed argument object. Signed calls with changed
  argument bytes remain explicit protocol errors rather than risking a broken
  signature. A no-action malformed response also stays a
  repair turn instead of being mistaken for a substitute model. The corrected
  isolated preview is `https://forma-b86dj6ijj-negens-projects.vercel.app`
  (`dpl_DqNiha8WytXFaXyHpcqJjddpDAKf`), health 200.
- Both saved runs were resumed on the corrected preview at a paused boundary
  with no outstanding operations. Each immediately received OpenRouter's
  free-model daily-quota response and paused again, retaining its candidate.
  This does not qualify the replay fix in a complete agentic run. At this
  checkpoint the pump has ten model calls; the 60-part run has thirty.
- After the provider repair, the full API suite passed 446 tests, the v4
  database suite passed 13, ESLint, frontend typecheck and API Ruff passed.
- Commit `b0a5be4` was pushed to
  `codex/cad-observation-budget-20261022`. Production-target deployment
  `dpl_6aedStxhpWhLXQjkTpeWNjZtqhbh` passed API health and main-page
  checks, then was promoted to the public Forma domain. Direct public health
  and main-page checks returned HTTP 200 after promotion. The production
  environment does not set `FORMA_HARNESS_VERSION=4`, so v4 remains guarded;
  the database schema and code are live, but the new agentic path is not the
  public default. Prior public deployment
  `dpl_3KrKnE5xeRsXTVEE2xRK26nZJ4Gj` is the rollback reference.
- Temporary environment, cookie and login files used during qualification
  were moved out of test reports into the private local Forma directory.
  A staged-source scan found no matching provider keys or evaluation secrets.
- Follow-up repair: OpenRouter free-model daily-quota errors now enter the
  durable provider-wait path used for throttling. `Retry-After` wins when
  supplied; without it, the wait targets the next UTC day plus a brief margin.
  That fallback is an inference from OpenRouter's documented daily free cap
  and UTC daily key resets, not a promised reset time for every endpoint.
  The failed operation can retry after that deadline without a false blocked
  task outcome. Existing already-paused evaluations still need one deliberate
  resume after quota returns. Focused provider tests passed 17 checks. This
  follow-up was promoted as production deployment
  `dpl_GbFH6NHNSb35TfhkUjjFV5xq261b` from commit `09fbc43`; the public
  API and main page returned HTTP 200 afterward. The isolated evaluation
  preview was refreshed at
  `https://forma-kx6bshiih-negens-projects.vercel.app`
  (`dpl_9HUCdfWFKE1jzz16HPpyrrAX7zqV`), with health 200, login 200 and
  the same two pinned evaluation models. It remains separate from production.

- At 18:01 UTC on 2026-10-28, a user-supplied Vercel AI Gateway key was
  tested only in the isolated evaluation environment. Both
  `minimax/minimax-m3.1-flash` and `xiaomi/mimo-v2.6-flash` returned the exact
  requested tool call in a two-turn replay. MiniMax then passed Forma's own
  staged two-turn check for coordinator and CAD; both roles were activated as
  version 2 in the isolated evaluation database. The production model settings
  and public v4 routing were not changed. The saved pump and 60-part runs were
  resumed, not duplicated. At 18:01 UTC the pump had advanced from 10 to 16
  model calls with a version-2 MiniMax receipt; the 60-part run was queued.
  Neither result had engineering acceptance or complete exports at that point.

- The resumed pump exposed a context defect during engineering inspection:
  the 30-message rolling history discarded early successful `read_file` pairs
  while inspecting many components, so the agent began rereading the same
  files. The history window is now 80 messages / 160,000 characters locally;
  model-specific context fitting still applies. Successful reads now enter a
  candidate-bound file index in working memory without storing source text.
  This lets later turns see which files were inspected and whether that read
  predates an edit, while source retrieval remains available. The 20-part read
  regression and relevant agent, history, and safety checks passed (157 tests);
  the full API suite passed 451 tests. Touched files pass Ruff and Git
  whitespace checks. Commit `c51b18f` was pushed and deployed as production
  target `dpl_DPy8gwHBqvJnGBYS3BuEdjs3hy1W`; API health and main page
  passed on the target and after promotion to the public Forma domain. The
  production v4 route remains guarded and provider settings unchanged. The
  isolated evaluation workflows remain bound to their earlier deployment, so
  this fix does not alter the currently running pump or queued 60-part run.

- The pump's live engineering session then exposed a second loop defect. With
  `tool_choice=auto`, the pinned Gateway MiniMax model repeatedly used its
  full 16,384-token output allowance for reasoning without requesting an
  action. Those long, actionless assistant messages entered active history,
  displaced source observations and caused more reads. A direct two-turn
  Gateway check with an approximately 80,000-token prompt confirmed this exact
  pinned model supports `tool_choice=required` and returns tool calls. The
  provider adapter now requests an action for that model only. In v4 research
  sessions, an actionless response retains its complete immutable generation
  record but contributes only a short notice to active dialogue; signed tool
  messages remain intact. The focused agent/provider checks passed 141 tests,
  the complete API suite passed 453 tests, and touched files passed Ruff and
  Git whitespace checks. Commit `af6956e` was pushed; guarded production
  deployment `dpl_BrgcnCNaN4TozN49PhVCWfTjfXTf` was ready, passed target
  health/main-page checks and was promoted to the public Forma domain. The
  public API health and main page returned HTTP 200 after promotion. V4 is
  still off as the public default. The active isolated workflow remains on
  its earlier deployment, so this fix has not yet changed that run. At 19:03
  UTC the pump was still running at 109 model calls and the 60-part run was
  queued at 30; neither had engineering acceptance.

- By 02:58 UTC on 2026-10-29, the old isolated pump workflow paused at 860
  model calls with `TypeError: quote_from_bytes() expected bytes`. Its graph
  checkpoint is still in `engineering_analysis`; the candidate remains at
  version 2, with no accepted revision. The engineering session had requested
  `calculate` before a CAD build. Its checkpoint supplied `sandbox=None`, so
  sandbox preparation recorded `sandbox:None` and failed before the calculation
  executed. The calculation operation is marked started/uncertain and requires
  the existing explicit Continue reconciliation before replay. This is an
  application integration defect, not an engineering result or a model outage.
  The same run had 145 actionless generations and 558 `read_file` requests
  across 859 saved generations; the earlier context/action fix was not on its
  pinned runtime. Eleven rejected `apply_changes` requests left started
  receipts because validation exceptions escaped the receipt callback.
  Engineering now assigns a stable per-run sandbox identity before calculating
  and commits deterministic tool-validation feedback as a completed receipt.
  The engineering regressions passed 7 checks, the full API suite passed 455,
  and touched Python files passed Ruff. The 60-part run was still queued at
  the inspection checkpoint.

- Commit `ffb53d5` was pushed. The corrected isolated preview is
  `https://forma-nl0wped1k-negens-projects.vercel.app`
  (`dpl_GH1UF9NGpxgZJPJen3uuNMJbuvNq`), configured with the saved
  `preview-eval` runtime identity and isolated database. Its health and
  authenticated saved-run request returned HTTP 200. A first preview was
  healthy but used the generic `preview` runtime identity; Forma correctly
  rejected Continue with 409, and no work was dispatched from it. The saved
  pump was then continued exactly once on the corrected preview; the existing
  uncertain calculation receipt was explicitly reconciled by the Continue
  endpoint. At 06:04 UTC it was running at 867 model calls. A newly rejected
  engineering edit produced a complete failure receipt, confirming the
  validation-receipt repair in the hosted run. The queued 60-part workflow's
  overdue scheduler wait was safely re-enqueued through its existing
  workflow ID; no new run or model call was created by that wake. It remains
  pinned to its older deployment and queued behind the pump.
  The resumed run completed sandbox preparation with a real engineering
  sandbox identity, then returned a normal failed calculation receipt for an
  unsaved `calculations/env_probe.py` path. It continued editing and saved
  candidate version 3 with `calculations/seed.py`; at 06:06 UTC it was running
  at 870 model calls. No geometry build or engineering acceptance is claimed.
- Guarded production deployment `dpl_B6qaZauFNa92EsB9xm2cBSKfxa65`
  passed target health and main-page checks and was promoted to the public
  Forma domain. Both public checks returned HTTP 200 and the inspected domain
  resolved to that deployment. The v4 route remains off by default; neither
  the pump nor 60-part task has passed engineering acceptance.

- At 06:48 UTC on 2026-10-29 the pump was still in engineering at 905 model
  calls, candidate version 3, without a geometry build. Fourteen of its 38
  generations since ordinal 860 were actionless; ordinals 883–905 included a
  sustained sequence of full 16,384-token reasoning outputs without a tool.
  A separate read-only qualification request reconstructed the saved
  engineering context (about 105,000 characters, 16 tools). MiniMax and MiMo
  each exhausted an 8,192-token response even with low reasoning and required
  tool selection, returning no tool call. These observations implicate the
  broad task handoff and retained dialogue as well as model behavior; they do
  not establish that either model can complete the original pump task.
  Vercel documents that reasoning tokens count toward the output limit:
  https://vercel.com/docs/ai-gateway/models-and-providers/reasoning.
- Locally, repeated actionless assistant turns and duplicate generated
  reminders are now pruned from active v4 dialogue. The last reminder, signed
  tool-call pairs and tool results remain; full provider responses remain in
  immutable generation receipts. The state records a consecutive actionless
  count and instructs the agent to choose one small tool step or report an
  evidence limitation. The engineering role now explicitly returns control
  to CAD after useful independent calculations when geometry has not yet been
  built, instead of repeatedly requesting measurements. No model-call,
  runtime, spending or repair-count stop was added. The focused history tests
  passed 109 checks, the full API suite passed 456, and touched files passed
  Ruff and whitespace checks. This adjustment is not yet on the active
  isolated workflow; qualify it at a safe boundary before claiming improvement.
  Commit `3f29946` was pushed. Guarded production deployment
  `dpl_4mvYdmX1Mx6cFwfj4Z7hw1aMsSCA` passed target health/main checks,
  was promoted, and passed the same checks on the public Forma domain; v4
  remains off as the public default. A matching isolated preview
  `https://forma-ixu5mc4bg-negens-projects.vercel.app`
  (`dpl_7PytGm7duZLj6um3ALxeC6MiGAgK`) passed health and an authenticated
  request for the saved pump run. It has the correct `preview-eval` runtime
  identity but does not replace the running workflow. At 07:03 UTC the pump
  was still running on the earlier preview at 916 model calls; the 60-part
  run remained queued on its earlier workflow.
- At 07:38 UTC the saved pump had reached 942 calls and remained in
  engineering without a geometry build. Generations 927–942 were sixteen
  consecutive 16,384-token responses without a tool call. This is a live
  failure pattern, not proof that the new guard has helped: the active
  workflow is still pinned to its earlier deployment. For future v4 turns,
  an actionless response now narrows the next dialogue to the original task
  and recent complete tool pairs; the authoritative project context,
  candidate and immutable generation records remain available. The recovery
  prompt explicitly directs retrieval of omitted evidence. The focused graph
  checks passed 92 tests, Ruff and whitespace checks passed. This change
  still needs hosted qualification at a safe workflow boundary. Commit
  `ae5cacd` was pushed and guarded production deployment
  `dpl_DFoRVp4fQt2LpQKtERRWLyhfMwVu` became ready. Its protected target
  health and main-page checks returned HTTP 200 through Vercel's authenticated
  curl; it was promoted and the public Forma health and main page returned
  HTTP 200. The public domain resolves to that deployment. V4 remains off
  as the public default and the active eval workflows remain on their older
  pinned deployments.
- The matching isolated evaluation preview is
  `https://forma-lpfxotj4w-negens-projects.vercel.app`
  (`dpl_58425TF7T7UJrmBsZpAMdZiFsvm4`). It uses the isolated database,
  `preview-eval` runtime identity and v4 research settings; its protected
  health endpoint and an authenticated GET for the saved pump run returned
  HTTP 200. At 07:50 UTC the old pinned pump workflow was still running at
  949 model calls, and the 60-part workflow was still queued at 30. Neither
  workflow was replaced or interrupted by the new preview. The focused
  recovery behavior remains unproven on these saved runs until a safe
  continuation boundary is reached.
- At 08:35 UTC the pump was still running on its old pinned workflow at 982
  calls, candidate version 3, without a geometry build; the 60-part task was
  still queued at 30. Generations 976–981 again had no tool call and each
  consumed the 16,384-token output allowance. A v4 engineering session that
  produces no executable action now hands control back to its coordinator or
  CAD caller with an explicit failed specialist result and unchanged candidate.
  This prevents the engineering phase itself from cycling on a provider's
  actionless response; it does not mark any calculation or design requirement
  satisfied, and it does not impose a research call/compute cap. The 93
  focused graph checks, full 458-test API suite, Ruff and whitespace checks
  passed. The change has not yet affected the active pinned evaluations;
  hosted behavior and engineering completion remain unverified. Commit
  `0abe60f` was pushed. Guarded production deployment
  `dpl_9cKqnaMMV3kDCJJbgc6vTrpxnfUu` passed protected health and main-page
  checks, was promoted, and passed public health and main-page checks. V4
  remains off as the public default. Matching isolated eval preview
  `https://forma-9tcl99swx-negens-projects.vercel.app`
  (`dpl_2QdUtx214wdPBg6s3fSY3t4jUQnB`) passed protected health and an
  authenticated saved-run read with the `preview-eval` runtime identity.
  The active pump remained pinned to the previous preview and was running at
  989 calls at 08:47 UTC; the 60-part run was still queued at 30. No live
  workflow was replaced or interrupted.
- At 09:35 UTC the pump was still running at 1,023 model calls on its older
  pinned workflow without a geometry build; the 60-part run remained queued.
  To prevent an immediate coordinator/CAD bounce from reentering the same
  actionless engineering task, v4 now records a request fingerprint covering
  task text, candidate, authoritative request, current evidence and pinned
  engineering model configuration. An identical retry receives the existing
  no-action feedback and must advance CAD, gather evidence or choose a
  different focused calculation. A changed candidate, evidence, user request
  or model configuration permits a new attempt. This is idempotent recovery,
  not a spending, call-count or repair cap. The focused graph suite passed
  94 tests; the full API suite passed 459; Ruff and whitespace checks passed.
  Commit `7e53020` was pushed. Guarded production deployment
  `dpl_36rfT9UzdCoUjStyA2GVuNKoYinp` passed protected health and main-page
  checks, was promoted, and public health and main page returned HTTP 200.
  V4 remains off as the public default. The matching isolated eval preview
  `https://forma-chzerbclt-negens-projects.vercel.app`
  (`dpl_4XEMoK3LGT96T9pkN2MJMWBKusjj`) passed protected health and an
  authenticated saved-run read under `preview-eval`. At 09:44 UTC the active
  pump was still pinned to an older preview at 1,029 calls; the 60-part run
  remained queued at 30. Neither workflow was replaced or interrupted. The
  new recovery still needs hosted qualification after a safe boundary.
- At 10:42 UTC the pinned pump run remained active at 1,060 model calls, but
  it had moved from engineering through CAD back to coordinator. Candidate
  version 8 contains 14 distinct component definitions and 16 source files,
  including a gear-pump calculation module. It has **no assembly instances,
  root component, native assembly, or configuration**, and no completed
  geometry build or STEP evidence. The current candidate has a reproducible
  calculation execution record and three current interval records: radial
  tip clearance 0.065–0.0875 mm passed its 0.04–0.12 range; axial clearance
  0.060–0.090 mm **failed** the 0.04–0.08 range; and journal-to-bushing
  clearance 0.016–0.052 mm passed 0.015–0.060. The radial record also shows
  the 0.05 mm basic-dimension nominal separately from the tolerance range.
  These are calculation evidence, not measurements from a built pump. Latest
  coordinator generations again exhausted 8,192 output tokens without tool
  calls on the old pinned runtime. The 60-part run remained queued at 30
  calls. Preserve these candidate and evidence records; do not claim the
  original design task is satisfied or refresh either live workflow unsafely.
- At 12:15 UTC both isolated evaluation runs reached a safe pause with their
  workflow identities and worker leases cleared. The pump is paused/blocked
  at 1,142 calls with candidate version 8, 14 component definitions and no
  assembly instances or STEP. The 60-unique-part run is paused/blocked at
  30 calls with candidate version 6, two component definitions and one
  instance. Each run recorded an HTTP 402 provider-payment blocker; neither
  was marked satisfied or accepted. A minimal read-only probe against the
  saved Vercel AI Gateway key confirmed HTTP 402
  `quota_for_entity_exceeded` for **both** selected models, MiniMax M3.1 Flash
  and MiMo V2.6 Flash. Do not retry these saved runs until the gateway quota
  changes or a separately qualified connection is activated. Their
  checkpoints, candidate versions, calculation records and receipts remain.
  A read-only alternative-provider probe found the saved TheGrid `code-max`
  connection responds and can complete a two-turn tool exchange with
  `tool_choice=auto`, but rejects `required` and resolves the alias to a
  different model identity. That alias is not a pinned qualified replacement
  under the approved provider rules, so it was not activated or used for
  either saved project. No provider keys or tokens are in this report.
- At 15:42 UTC on 2026-10-29, the saved runs were still paused with the same
  candidate versions and call counts. TheGrid's published instrument description
  says `code-max` may route to any qualifying model and use automatic fallbacks;
  pinning the alias would therefore not pin the underlying model. Its terms also
  restrict use of the Consumption API for benchmarking or competitive analysis
  without prior written consent. Do not use this route for the planned model
  comparison or treat its stable two-turn probe as an engineering qualification.
  Sources: https://app.thegrid.ai/ and https://thegrid.ai/terms-of-use.

Next: leave both saved runs paused until Gateway quota becomes available or a
separately qualified provider with a pinned model and compatible data terms is
available. Their absent workflow identities and leases make this a safe resume
boundary; start each preserved run only once on the latest isolated preview.
Then inspect receipts, complete the missing assembly work, independently reopen
all STEP states and inspect requirement evidence. Run numeric variants,
conflicting briefs, the remaining evaluation corpus and interruption tests
before any default-v4 release. Production schema and guarded v4 code are live;
the public v4 route and production model configuration remain disabled.

### Implemented locally, not yet release-qualified (updated 2026-10-25)

The latest user direction is to prioritize the working application flow, with targeted
verification of changes. This paragraph records the 25 October local baseline;
the hosted qualification section above supersedes its deployment status.

- General exact interval arithmetic replaces the v4 fixture tolerance answer. Original
  user messages are backfilled into immutable source records; pageable source/history
  tools and sourced acceptance-plan versions are available.
- Source-page reads now leave immutable span receipts without copying document text
  into the event. A new acceptance plan cannot silently account for an uploaded
  brief document that was shortened in prompt context until its pages have been
  retrieved in full. Existing identical plan replays remain valid. The targeted
  source/plan tests (4) and adjacent graph tests (94) passed on 2026-10-29;
  Ruff and the live isolated PostgREST read query also passed. Commit `dd9a093`
  was pushed and guarded production deployment `dpl_EgD2H3Tmaz9MvTmM4KxKoSo4w42k`
  reached Ready; public health and main page returned HTTP 200. The Vercel CLI
  lost its polling connection after creating the deployment, so the existing
  deployment was inspected by ID rather than submitted again. The saved hosted
  runs remain paused by provider quota and have not exercised this change.
- The same full-read gate now covers long user messages when the common context
  builder replaces their text with retrieval references. It uses the context
  builder's own omission decision, so ordinary short messages do not cause
  extra reads. Existing identical plan replays stay valid. Five targeted
  source/plan tests and 94 adjacent graph tests passed. Commit `0742f06` was
  pushed and guarded production deployment `dpl_EekZMBRJpxuqtfeA3TaN44GxKs7D`
  reached Ready; public health and main page returned HTTP 200. The Vercel CLI
  lost its polling connection after creation, so the existing deployment was
  inspected by ID. Hosted engineering qualification is still pending.
- Explicit task outcome and revision acceptance states. Availability of a draft does
  not satisfy engineering requirements. Completion requires current requirement
  evidence, real artifact receipts and an independent review of requirement coverage.
- Iterative engineering with current candidate/measurements, calculation source edits,
  source-free STEP measurements, and notebook/CSV/JSON reports built from receipts.
- Three requested configuration STEP exports independently reopen and verify placement.
  Broad-phase pair filtering is compared with exhaustive enumeration in native tests.
- V4 reading/repair/request caps removed, research budgets bypassed, full history
  retained outside the prompt, and model-window-based context fitting applied.
  Legacy graph and prompts remain separately routed; shared dependency behavior still
  needs migration qualification.
- Candidate connection staging, versioned provenance, no automatic rate-limit model/key
  cycling, and provider Retry-After parsing with persisted cooldown timestamps.
- Durable graph-node receipts survive checkpoint loss. Candidate changes and node
  outcomes commit atomically with expected-version checks and worker fencing. Fenced
  writes cover operations, candidates, generations, calculations and their sources,
  project records, acceptance-plan heads and publication. The remaining write audit
  includes event, artifact-staging, resource-admission and cancellation paths.
- Transactional dispatch intents preserve initial scheduling and human resume payloads.
  Known Workflow identities can be safely requeued; unknown scheduler creation stays
  blocked rather than being blindly duplicated. Continuation uses the original token
  and cannot overwrite a successor. Maintenance recovery remains behind
  FORMA_RESEARCH_RECONCILIATION=true and authenticated scheduling.
- An interrupted Workflow can stop its own run through immutable dispatch ownership;
  it cannot stop a successor. This fixes a swallowed-error path that left runs appearing
  active after their scheduler stopped. Explicit human continuation selects a fresh
  model ordinal after every earlier attempt, retaining original receipts when settings
  or guidance change. Real provider-switch/resume qualification remains pending.
- Continue now rejects a run that is already active before authorizing any retry of
  uncertain operations. The adjacent API checks passed on 2026-10-29. A concurrent
  status transition between the read and authorization still needed an atomic
  resume/authorization boundary. The new migration places retry authorization,
  preservation of the previous operation receipt, the follow-up message and
  dispatch intent inside one locked transaction. The PGlite v4 migration suite
  passed 14 tests and targeted API safety/dispatch checks passed 68. The migration
  is applied to the isolated evaluation database; the production database URL
  was unavailable to local migration tooling, so production schema and guarded
  deployment still use the earlier resume path. Do not deploy this API change
  to production before its schema migration is verified there.
- Commit `2bffe04` was pushed with the atomic resume migration and API change.
  Migration `20261029221000` was applied and recorded in the isolated evaluation
  database; its service-role EXECUTE grant and preserved authorization event
  were checked. Isolated preview
  `https://forma-ffaj7k4p6-negens-projects.vercel.app`
  (`dpl_J2jJqFwkAztyFVqBDyRwztaAr3EZ`) reached Ready. Protected health
  returned 200, and authenticated GET of both saved run IDs returned 200,
  `paused`, and `preview-eval`. Neither run was resumed. Production remains on
  `dpl_EekZMBRJpxuqtfeA3TaN44GxKs7D`: the new migration is not yet applied
  there and this commit is not deployed there. A Vercel environment pull exposed
  no usable production database URL to local migration tooling; because that
  variable is encrypted in Vercel, this does not establish whether the running
  production service has it. Obtain a verified production migration path before
  deploying the new API. Gateway HTTP 402 still prevents agentic acceptance.
- The saved Supabase CLI session provided a production migration route without
  retrieving a database password. `supabase db push --dry-run --linked` named
  only `20261029221000_atomic_research_resume_authorization.sql`; the linked
  production push applied it, and a second dry run reported no pending
  migrations. This supersedes the production-migration blocker above. Current
  branch commit `f57d51d` then deployed as guarded production
  `dpl_DpsTwm2hcGWRkHT5zn1m9zP4bMEN`. Vercel reported Ready and the public
  Forma alias pointed to that deployment; public `/api/health` and `/` each
  returned HTTP 200. The v4 route remains off as public default pending
  engineering acceptance. These smoke checks do not validate the paused pump
  or 60-part designs, so neither run was resumed or marked complete.
- Unexpected graph transition exceptions now leave a paused, recoverable run
  with the exception class in its visible status, while internal logs retain
  source locations. The exception message itself is no longer copied into
  the run status because a provider or runtime can include credentials or
  private request data there. A regression test injected a token-like error
  and confirmed it was absent from the saved status. Focused graph, durable-job
  and dispatch checks passed 14 tests; Ruff and whitespace checks passed.
  Commit `a8f8d37` was pushed. The production-target CLI lost its polling
  connection after creating deployment `dpl_BVfkLk1mzwhGR6RnrZNrVqac4H2P`;
  the existing deployment was inspected by ID rather than submitted again.
  It reached Ready, received the public Forma alias, and public `/api/health`
  and `/` each returned HTTP 200. The isolated evaluation runs are still
  paused by Gateway quota and have not exercised this change.
- Provider HTTP error bodies can echo an arbitrary connection key that does not
  match a known token prefix. Both OpenAI-compatible and OpenRouter transports
  now redact the exact selected key before returning an error body to the
  traced transport and before retaining its diagnostic payload. Classification
  still reads the response content, so cooldown and context recovery decisions
  are unchanged. Direct redaction and mocked HTTP transport tests passed;
  targeted provider suites passed 57 tests, with Ruff and whitespace checks
  clean. These checks do not establish model competence or a successful design.
  Commit `8230bab` was pushed and production-target deployment
  `dpl_4Pig9JZqsB3Rg5fR8nLb9jFX66EB` reached Ready after the CLI lost its
  polling connection. It received the public Forma alias; public `/api/health`
  and `/` each returned HTTP 200. Guarded v4 remains off as the public default,
  and saved isolated evaluations remain paused on provider quota.
- The isolated CAD runtime now allows generated component and calculation
  modules to import shared Python helpers from their own staged workspace.
  Workspace paths are scoped to the build/calculation operation, and imported
  workspace modules and bytecode are cleared between operations so a same-size
  helper edit cannot silently reuse old geometry. The validator still receives
  only reopened STEP files. A local import/reload regression passed; local
  CadQuery cannot execute on this Windows Python installation because its OCP
  package is incompatible. A new Vercel Sandbox runtime-only snapshot,
  `snap_PBqX4BX6mxnkMLYZ4B7Qu6vA6NNR` / `forma-f2edabf726acfe05`,
  derived from the previously qualified native engine, passed all 80 native
  tests with no skips or failures. The builder attested that the native engine
  and package set were unchanged. Isolated evaluation preview
  `https://forma-dr7bs088x-negens-projects.vercel.app`
  (`dpl_EDxDY1i2Sod1sAUYHKih56HXzDou`) was deployed with that snapshot and
  runtime identity at the saved runs' safe pause boundary. The CLI lost its
  polling connection after creating the deployment; inspection of that same
  deployment showed Ready. Protected health, administrator login, and
  authenticated GET of both saved runs returned HTTP 200 with `preview-eval`
  runtime identity and unchanged paused states/call counts. The model quota
  still prevents those runs from exercising the new runtime. Production keeps
  the previously qualified native runtime; complete an application build and
  inspect artifact identity before any production runtime change.
- The new runtime passed an isolated application-level smoke build on the
  evaluation preview. In a new project, the existing deterministic 2 mm fixture
  built 60 repeated plate instances, passed native placement and fresh STEP
  validation, uploaded nine private artifacts, fenced publication to its worker,
  and authenticated download of STEP, GLB, BOM and assembly evidence. The
  downloaded `assembly.step` is 54,812 bytes. The draft revision is
  `2309ef85-d9e1-5a96-aadb-8377aa1a361c`; evidence is in
  `D:\v1\test-results\harness-v4-helper-app-smoke-20261030`. The first
  verifier invocation completed the build but its final second-account login
  failed because that public demo account does not exist in the isolated
  evaluation database. The verifier now records that check as unavailable in
  `preview-eval` and was resumed against the same passed case, without a
  second build. Anonymous access was rejected. This is a deterministic legacy
  adapter smoke test, **not** the 60-unique-part agentic acceptance test or a
  pump result. Production stays on the older runtime while its active legacy
  work is preserved. Next: obtain a qualified available model provider,
  resume each saved v4 evaluation once, and inspect independently reopened
  pump STEP states, calculation/tolerance reports and component inventory
  before any default rollout or production runtime switch.
- A separate deterministic fixture now exercises **60 distinct component
  definitions and source files**, each with a distinct width and part number,
  using one imported workspace-local Python helper. Its isolated application
  build passed native placement and fresh STEP validation, uploaded and
  authenticated the download of 127 artifacts, and published one draft revision
  `aa66e84a-4b77-510f-878c-f72ff8f8308a`. The 60 part STEP files have 60
  distinct SHA-256 hashes; BOM has 60 distinct part numbers at quantity one;
  assembly STEP is 1,311,573 bytes. Evidence is in
  `D:\v1\test-results\harness-v4-unique-app-smoke-20261031`. The first
  verification attempt completed the build but could not publish after its
  legacy six-minute worker lease expired; it stopped at 6 minutes 7 seconds
  with no revision. The verifier now reclaims only the same worker's lease
  after a long build and refuses to publish if another worker owns it. The
  saved paused run was resumed once after checking its prior workers were gone
  and its project base was unchanged; stable artifact paths and upsert made
  repeated uploads idempotent. This verifies geometry scalability and the
  application adapters, **not** agentic generation of 60 unique parts or the
  pump task. V4's production operation wrapper has its own fenced lease
  heartbeat; the expired lease here was in the deterministic legacy verifier.
- The 60-unique-part fixture exposed a **hosted execution boundary** still
  missing from v4. Its saved event timestamps show the second attempt entered
  `build_candidate` at 10:14:19 UTC and reached validated draft evidence at
  10:19:49 UTC, about 329.5 seconds later, before publication or artifact
  downloads. `vercel.json` configures the Python API function for 300 seconds;
  [Vercel's function duration documentation](https://vercel.com/docs/functions/configuring-functions/duration)
  says Hobby functions terminate at that limit. The isolated verification
  script ran locally, so its success does **not** establish that the hosted v4
  graph can complete the same workload. V4's fenced lease heartbeat protects
  database ownership but cannot extend a function invocation. The next R8
  implementation must split staging, Sandbox execution, fresh STEP validation,
  and artifact transfer into separately receipted workflow operations below
  the function limit. A process started in Sandbox must be identified and
  reattached after a scheduling boundary; ambiguous outcomes must be inspected
  before replay. Each artifact transfer needs a stable identity and receipt.
  Keep the existing valid draft and paused agentic runs intact while this is
  implemented and qualified on the isolated preview.
- The first detached Sandbox execution adapter is implemented on the branch,
  without changing the active graph route. It starts one identified CAD
  process, can list process identities after an uncertain start, and reconnects
  to inspect the supervisor receipt from a separate host process. A real
  isolated probe produced a 15,500-byte STEP file after reconnection; evidence
  is in `D:\v1\test-results\harness-v4-detached-process-20261031`.
  The Sandbox SDK continued reporting this completed detached command as
  `running` after the receipt and STEP existed and no Python process remained.
  Inspection therefore uses the supervisor receipt as completion evidence,
  while start refuses a prior receipt or another active CAD command. Targeted
  adapter tests passed 3/3 with Ruff and whitespace checks clean. This is a
  foundation for the phase split, **not** yet a hosted long-build fix: the
  graph still calls the synchronous combined build path. Next integrate
  stage/execute/validate/upload as separate durable graph operations, confirm
  process/receipt identity and cancellation across a function boundary, and
  then rerun the unique-part hosted acceptance.
- For v4 candidates, independent STEP reads and validator-artifact transfers
  now use bounded concurrency of four; result order and the 120 MB transfer
  guard remain intact. Sibling transfers settle before a failure releases the
  validator, and the legacy v3 path retains sequential fail-fast behavior.
  Targeted transfer, assembly and sandbox-recovery tests passed 19/19 with
  Ruff and whitespace checks clean. This reduces time spent moving many
  immutable files but has not yet been measured in a hosted v4 build. It does
  not replace the required durable phase split or remove the 300-second risk.
- Detached CAD commands now accept a 64-character operation identity. The
  sandbox supervisor writes it into an atomically replaced receipt, and the
  host can recover exactly one matching process after an uncertain start.
  Receipt inspection rejects a mismatched identity. A new runtime-only
  snapshot `snap_ToWrByRI8FTr6ByIzZXoRGzUfdM6` /
  `forma-3d2cd65f6221f144` preserved the qualified native engine/package
  identity and passed all 80 native tests. Its initial build observer lost a
  filesystem read after completed stages; the same named build was resumed
  without replaying those stages. A separate host process then recovered a
  real key-bound CAD command and independently read its 15,500-byte STEP;
  evidence is in `D:\v1\test-results\harness-v4-detached-identity-20261031`
  and `D:\v1\test-results\harness-v4-detached-key-probe-20261031`.
  Four targeted adapter tests, Ruff, syntax and whitespace checks passed.
  This snapshot is not selected by production or the isolated evaluation
  preview yet. Next, connect these identities to separately checkpointed
  build/validate/upload graph phases before switching the isolated preview.
- The first engine seam for that graph split now separates validation of an
  already completed builder from the build invocation. A later graph node can
  reopen STEP files from the named builder Sandbox and create fresh validator
  evidence without storing STEP bytes in the LangGraph checkpoint. The existing
  combined path passes its already-read bytes to the same seam, preserving its
  behavior. The focused test verifies independent reads and validator inputs
  (2/2 transfer tests passed, Ruff and whitespace clean). This first seam did
  not yet change the graph route; the subsequent guarded phase split below
  connects it. Hosted duration and agentic acceptance remain separate gates.
- The guarded v4 graph now has separate durable transitions for candidate
  staging, keyed CAD process start, receipt polling, independent STEP
  validation, groups of at most 16 artifact transfers, and final evidence
  recording. A running CAD process schedules another workflow turn instead of
  occupying the hosted function. An interrupted start first reconciles the
  exact process key and never blindly submits another command. Artifact paths
  remain content addressed and completed transfer receipts replay by candidate
  and batch identity. The v3 graph still uses its prior path. Local API tests
  passed 477/477, including a 17-artifact two-batch transition and ambiguous
  start recovery; a subsequent focused check confirmed that the validator
  remains alive across the validation-to-transfer boundary. Touched Python
  files passed Ruff and whitespace checks.
  This implementation is **not yet hosted qualified**: the new runtime snapshot
  is not selected by the isolated preview, and the 60-distinct hosted graph
  build has not run. Next deploy the guarded preview with the new runtime,
  exercise the transitions across real workflow invocations, inspect the
  independently reopened STEP and artifact receipts, then assess whether the
  validator phase stays under the 300-second function limit.
- The guarded phase-split code was pushed as `5880191` and deployed to the
  isolated evaluation preview with runtime snapshot
  `snap_ToWrByRI8FTr6ByIzZXoRGzUfdM6`. Deployment
  `dpl_25HU2vakWzqMdonB14ikdAaK1RZ4` at
  `https://forma-4kqf7okfn-negens-projects.vercel.app` reached Ready;
  protected health returned HTTP 200, administrator login returned HTTP 200,
  and authenticated reads of both saved evaluations returned HTTP 200 with
  `preview-eval` identity and unchanged paused states/model-call counts. An
  earlier preview submitted during this rollout, `dpl_7TgxxgbKE9hNVBm1BKVt5n8bNnvW`,
  used a different private database configuration; its login returned 401 and
  it is excluded from all evaluation evidence. Neither saved run was resumed.
  The hosted 60-distinct graph build and independent artifact inspection are
  still required before production runtime promotion or default-v4 release.
- A fresh 60-distinct deterministic fixture was staged in the isolated
  evaluation database as run `4bbab69e-30e4-59a6-a41b-7615f5c6049e` with
  61 component definitions and 60 instances. The first hosted dispatch
  reached no CAD work because a synthetic initial checkpoint plus an explicit
  Continue triggered a LangGraph ambiguous-update error; it paused safely.
  The same run was resumed through the durable dispatch outbox without that
  unnecessary resume update. Hosted staging, keyed process start and receipt
  polling then completed, with zero model calls. Independent validation exposed
  a null-handling defect in the validation trace cleanup: its optional
  `validated` checkpoint field was `None`, and the trace path called `.get`
  on it. The run paused without accepting or publishing the fixture; its
  completed build receipts and process identity remain saved. A focused
  regression now supplies `validated=None`, and the trace path handles it.
  Next deploy this small fix to the isolated preview, continue the same paused
  fixture at its safe boundary, and verify the validator and batched artifact
  phases plus reopened STEP files. This is still deterministic fixture
  qualification, not the agentic 60-part evaluation.
- The null-handling fix `f0ae475` was deployed as guarded isolated preview
  `dpl_EEf7mQvSMJSykLcw2xzsT1YQnmiQ` at
  `https://forma-i9bks13zw-negens-projects.vercel.app`. Protected health,
  administrator login and the paused fixture read passed. The same saved
  60-distinct run continued at its pending validation node; no duplicate
  build or model call occurred. Independent validation, eight groups of at
  most 16 artifact transfers, final evidence recording and the graph's
  validation node all completed in hosted Workflow transitions. The longest
  completed node was 26.3 seconds, below the 300-second function limit.
  Its draft has 127 private artifacts: 61 STEP, 62 GLB, one assembly record
  and three BOM exports. All 127 objects were downloaded from private storage
  and matched their saved byte counts and SHA-256 receipts. The 60 part STEP
  files have distinct hashes; the assembly STEP is 1,311,573 bytes, and the
  flat and structured BOMs each have 60 rows. A fresh isolated validator
  reopened the **downloaded** STEP files without source code and reproduced
  the saved component, assembly placement, BOM and inspection reports.
  Evidence and the read-only verifier are in
  `D:\v1\test-results\harness-v4-hosted-phase-20261101`. The workflow then
  reached model-based review and paused on the provider's HTTP 402 payment
  limit, with zero completed model calls. This is a successful hosted CAD
  phase and artifact qualification, **not** an accepted design, pump result
  or agentic 60-part evaluation. Keep v4 off the public default until those
  remaining engineering gates pass.
- The committed project acceptance plan persists across runs. Sourced amendments and
  a matching base version are required to change or retire requirements/deliverables.
  Focused delegation cannot replace the full inventory. V4 bypasses legacy keyword
  extraction and automatic component/semantic rebinding of requirements.
- Build review returns to the lead agent to finish outstanding measurements and reports;
  publication is an explicit lead action. Engineering returns to its requesting session.
  Stalled CAD work returns saved evidence to the lead for context retrieval and a new
  hypothesis or independent task. Reads, diagnostics and coherent patches remain usable.
- Malformed structured responses preserve original receipts and continue through
  durable correction steps. Complete tool-call relationships and opaque provider
  reasoning/signature fields survive compaction. The same context fitting is used for
  structured and ordinary tool turns.
- Built draft previews/downloads are available before publication. The viewport and
  part tree use the immutable source that produced the files; later edits label that
  preview stale. Owners can switch between the built draft and saved revision. Download
  requests require an owned run, matching candidate and staged artifact receipt.
- Calculations are labeled “Calculation executed,” not engineering verification.
  Candidate edits invalidate older results; runtime changes also mark them stale.
  Fenced calculation/source writes are atomic and immutable. Publication associates
  matching calculations with the actual revision instead of its earlier base revision.
- Provider candidate settings activate atomically after multi-turn qualification.
  Retesting the active connection is read-only; failed candidate tests leave it usable.
  Context reservation errors produce an actionable settings error. OpenRouter and
  Nebius/compatible capability profiles now require pinned model IDs and an explicit
  compatible-provider context size. Both protocol turns must resolve to the selected
  model; SQL rejects activation when identity is missing or different. Streaming
  aggregation preserves response/model identity and opaque tool-call content. Later
  responses from a qualified connection are also rejected if the provider silently
  substitutes or omits the selected model identity.
- A confirmed provider context rejection retries up to three times with fewer complete
  dialogue groups. The authoritative task/requirements stay intact, tool pairs stay
  together, and the effective prompt digest and retry count enter provenance. An
  authoritative prefix that cannot fit remains an explicit blocker.
- V4 progress events, artifact staging and demo resource reservations require the
  current worker fence. Owner cancellation now invalidates the active lease and
  pending scheduler dispatch atomically, emits one cancellation receipt and prevents
  a late worker from recording success. Active operations check the fence every 15
  seconds. The SDK smoke test runs in an isolated process because the local SDK
  caches its world across tests.
- Production API modules now pass Ruff lint, frontend passes ESLint and typecheck.
  New Python modules/tests were formatted, a pinned API Ruff dependency and
  `npm run lint:api` were added, and CONTRIBUTING.md records repository conventions.
  Unused imports/variables were removed. The new modules all have live references;
  `design_v3.py` and `prompts_v3.py` remain required for active legacy runs, while
  the architecture probe script and review evidence remain part of the baseline.
- Browser contracts were regenerated and the frontend type check passed. V4 remains
  disabled by default; worker concurrency defaults to one until qualification.
- On 2026-11-01, nullable validation checkpoint reads in the legacy model,
  delegation and sandbox paths, plus the v4 publication history, were made
  null-safe. A draft or fresh checkpoint can store `validated: null`; treating
  it as a mapping could crash the next action. The targeted recovery and graph
  suite passed 97/97, and `git diff --check` passed. This change is source-only
  pending a guarded deployment; it does not qualify an engineering result.

### Remaining before any default rollout

- Before promoting the new CAD runtime in production, recheck active legacy
  workflows and leases against the production database; the read-only check on
  2026-11-01 could not establish a database connection and no promotion was made.
  Deploy the nullable-checkpoint fix under the guard and verify production health.
- Qualify provider-switch/resume using real OpenRouter and Nebius connections, including
  model identity, data handling and difficult-task competence. Local mocked transport
  checks do not establish model competence.
- Finish expired/ambiguous scheduler reconciliation and safe successor import of paused
  legacy work. Do not
  change active legacy checkpoints or runtimes. Verify that human amendments after
  checkpoint loss cannot duplicate already committed effects. Split long CAD
  builds and multi-artifact transfers at the hosted 300-second function boundary
  as described above before claiming large assemblies work agentically.
- Add selective dependency-based evidence invalidation, versioned documentation and
  standard-part provenance, and immutable component caching. Do not reuse geometry
  merely because only calculation files changed: CAD may dynamically depend on them.
- Continue splitting planning, execution, recovery, evidence and publication concerns.
  Context, structured correction, engineering sessions, durable jobs and recovery now
  have separate modules; the central graph still contains substantial implementation.
- Qualify report exports, coverage review, UI, and engineering acceptance end to end.
- Establish an isolated local application database/provider setup. Private
  credentials for the hosted isolated evaluation database are available outside
  this worktree, and the hosted application build adapters have now been
  exercised. A local Supabase/PostgREST service and a completed local
  UI-to-provider-to-CAD run remain absent. Do not silently point local
  qualification at the production database.
- Run fresh agentic pump tasks, a 60-unique-part assembly, and the 120-run evaluation
  against a frozen legacy harness; report cost, time, interventions and false success.
- Guarded production and isolated-preview deployments are live, as recorded above.
  Engineering acceptance and the default-v4 release remain pending. No saved
  provider credential was added to source control.

## Release gates

Do not enable v4 by default before local, hosted and agentic acceptance gates
pass. Preserve legacy active runs and checkpoints. A failed or unsupported
engineering check never becomes a satisfied requirement. Original pump axial
clearance is 0.060–0.090 mm and fails the specified maximum of 0.080 mm.

## Verification

Prior review baseline: 388 API tests passed; five offline probes reproduced the
documented defects. On 2026-10-23, before the latest report/provider additions,
400 API tests passed; native geometry tests passed 19 with 1 skipped; the new
three-state export test passed separately. These are historical checks, not a claim
that every subsequent change passed a full suite.

Targeted checks on 25 October 2026:
- Frontend TypeScript check passed after draft-viewer and contract integration.
- Graph, provider, resume and draft-access checks: **97 passed**.
- Acceptance plan, arithmetic, evidence/report handling and draft access: **30 passed**
  after the project-record fencing change.
- PostgreSQL-compatible migrations and ownership/replay checks: **10 passed**,
  including immutable calculations/generations, stale-worker rejection, calculation
  invalidation, interrupted-scheduler ownership, fresh resume identities and record fencing.
- Earlier targeted durable-node/dispatch/provider run: **14 passed**. Earlier native
  STEP-query/configuration checks: **5 passed**; native code was not changed by the
  latest application-flow work.
- After provider/cancellation/style integration: full API suite **442 passed**;
  PostgreSQL-compatible v4 migration checks **13 passed**; native geometry/configuration
  checks **5 passed**; ESLint, frontend typecheck, API Ruff lint, and Git whitespace
  checks passed. A first full API pass found a staged-provider fixture lacking the
  newly required context size and an SDK local-world test-order issue; both were
  corrected, then the complete suite passed.

As of the 25 October local baseline, no fresh agentic pump, 60-unique-part run,
hosted acceptance, 120-run evaluation, production deployment or complex-task
completion had qualified v4. The hosted qualification section above supersedes
that deployment status. Production readiness remains unproven. Tracked files were restored to their original line-ending
style for substantive review; unrelated work and baseline evidence remain preserved.

Independent work while the provider is unavailable: finish the scheduler/restart
and legacy-successor boundaries, dependency-safe caching and standards
provenance. Exercise the local application with an isolated database and a
qualified provider when available. Keep checks targeted and prioritize usable
end-to-end behavior.

Provider implementation references checked 2026-10-23:
- https://docs.tokenfactory.nebius.com/ai-models-inference/rate-limits
- https://openrouter.ai/docs/api_reference/errors-and-debugging
- https://openrouter.ai/docs/guides/best-practices/reasoning-tokens
- https://vercel.com/docs/cron-jobs/manage-cron-jobs
