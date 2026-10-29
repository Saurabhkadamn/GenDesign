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

Next: monitor the two saved evaluation runs through build, engineering and
export without duplicating work. Refresh the isolated preview with the
context, actionless-response and engineering-sandbox fixes, then explicitly
continue the paused pump after reconciling its uncertain calculation receipt.
Independently reopen STEP artifacts
and inspect requirement evidence. Run numeric variants, conflicting briefs,
the remaining evaluation corpus and interruption tests before any default-v4
release. Production schema and guarded v4 code are live; the public v4 route
and production model configuration remain disabled.

### Implemented locally, not yet release-qualified (updated 2026-10-25)

The latest user direction is to prioritize the working application flow, with targeted
verification of changes. This paragraph records the 25 October local baseline;
the hosted qualification section above supersedes its deployment status.

- General exact interval arithmetic replaces the v4 fixture tolerance answer. Original
  user messages are backfilled into immutable source records; pageable source/history
  tools and sourced acceptance-plan versions are available.
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

### Remaining before any default rollout

- Qualify provider-switch/resume using real OpenRouter and Nebius connections, including
  model identity, data handling and difficult-task competence. Local mocked transport
  checks do not establish model competence.
- Finish expired/ambiguous scheduler reconciliation and safe successor import of paused
  legacy work. Do not
  change active legacy checkpoints or runtimes. Verify that human amendments after
  checkpoint loss cannot duplicate already committed effects.
- Add selective dependency-based evidence invalidation, versioned documentation and
  standard-part provenance, and immutable component caching. Do not reuse geometry
  merely because only calculation files changed: CAD may dynamically depend on them.
- Continue splitting planning, execution, recovery, evidence and publication concerns.
  Context, structured correction, engineering sessions, durable jobs and recovery now
  have separate modules; the central graph still contains substantial implementation.
- Qualify report exports, coverage review, UI, and engineering acceptance end to end.
- Establish an isolated local application database/provider setup. This worktree has
  no loaded Forma credentials or running local Supabase/PostgREST service, so the
  actual UI-to-provider-to-CAD run has not yet been performed here. Do not silently
  point local qualification at the production database.
- Run fresh agentic pump tasks, a 60-unique-part assembly, and the 120-run evaluation
  against a frozen legacy harness; report cost, time, interventions and false success.
- Hosted qualification, review, push and release remain pending. No production settings,
  provider credentials or deployed runtimes were changed by this implementation work.

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

Next session: finish the scheduler/restart and legacy-successor boundaries, add
dependency-safe caching and standards provenance, then exercise the actual local
application with an isolated database and provider before hosted qualification.
Keep checks targeted and prioritize usable end-to-end behavior.

Provider implementation references checked 2026-10-23:
- https://docs.tokenfactory.nebius.com/ai-models-inference/rate-limits
- https://openrouter.ai/docs/api_reference/errors-and-debugging
- https://openrouter.ai/docs/guides/best-practices/reasoning-tokens
- https://vercel.com/docs/cron-jobs/manage-cron-jobs
