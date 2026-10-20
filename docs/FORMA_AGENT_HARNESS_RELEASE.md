# Continuing CAD harness

This release repairs the generic agent harness after the original complex pump stopped at eight model calls, before a CAD build. It preserves the established Python/LangGraph implementation and isolated CadQuery/OCCT executor. No mechanical recipe, fixed component catalog, part lock, dependency change or database migration is introduced.

## Information and execution flow

The exact human brief and updates remain durable. The coordinator proposes measurable requirements and delegates design work. CAD receives the saved manifest, source paths, working notes/tasks, retained failures, the current acceptance evidence and scoped part-test freshness. It chooses a focused edit or diagnostic. The existing execution ledger makes external operations recoverable; source executes only in the isolated worker and fresh STEP validation remains independent.

An edit may upsert only supplied complete manifest entries by ID (`mergeById=true`), replace an intentional full array, replace a source file, or patch exact text after reading current source. The assembled workspace still passes the same atomic contracts before saving. `dryRun=true` validates a proposed edit without saving or execution. Invalid edits do not partially update the project.

Tool declarations preserve patterns, numerical bounds and nullability for ordinary JSON Schema hosts. Only Gemini receives the restricted schema presentation; unavailable constraints are spelled out in descriptions. Positive density and `#RRGGBB` are now visible to the model. This does not claim every host supports strict grammar decoding or that valid JSON establishes correct mechanics.

Large rejected source proposals no longer refill active dialogue. Their complete generation remains private; CAD can selectively read retained rejected source, explicitly labelled unsaved and unverified. Saved workspace files remain authoritative. Small error exchanges retain the normal tool protocol.

`build(componentId=...)` tests a part/surface and its dependency closure using the existing isolated builder and independent STEP validator. The projected execution input never replaces the project candidate, requirement list or assembly validation. Reports carry their own source/requirement/runtime identity. `inspect_geometry(componentId=...)` retrieves scoped evidence with source freshness. Changing source invalidates freshness. Assemblies continue to use `build(final=false)` milestones and a complete build before review/publication.

The old compulsory full build after three edits is replaced by a testing suggestion. Root staging problems remain actionable diagnostics while source inspection and individual part tests remain available. The agent selects the next useful step.

Repeated unusable CAD actions and exhausted local build repair allowances route to an automatic recovery planner. It receives the exact request, saved state, diagnostics and preceding plan; it proposes a different focused repair and verification. Local counters reset after that plan. Candidate identity, total model calls, build attempts and exact requirements remain retained. Planning is bounded to two attempts for an unchanged candidate/failure family and six per run, within the existing total call/execution/resource budgets. Exhaustion still uses the durable user recovery interrupt; no unbounded retry promise is made.

Working tasks are explicitly agent-reported. The acceptance context only marks supported requirements from a report matching the current candidate, requirements and runtime identity. Unsupported checks stay unverified. Part success cannot establish assembly clearance, motion, manufacturability or physical performance. This release does not add missing measurement primitives for shaft distance, GD&T or engineering tolerance chains.

The waiting UI now describes a required response without assuming every pause is a missing design dimension or engineering approval.

## Research basis

Anthropic's [long-running harness research](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) motivates incremental work, durable progress and end-to-end testing. Its [context engineering guidance](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) motivates selective retrieval and concise useful context. These published application patterns are not evidence of qualified mechanical CAD performance.

## Qualification

Local coverage exercises provider-facing constraints, unchanged unrelated definitions, exact patches, atomic previews/rejections, engineering role boundaries, stale evidence, isolated test state, private rejected-source retrieval, and automatic recovery across a recreated LangGraph checkpoint. The preceding qualified runtime remains `forma-537dd448bdeb66e6`.

The local API suite passed 329 tests. The web suite passed 16 tests; TypeScript checking and ESLint passed. These results precede hosted complex-task qualification.

Hosted acceptance, source commit, deployments, model identity, artifact checks and unresolved engineering findings are recorded under `D:/v1/test-results`. The unchanged original pump remains the complex acceptance task. Local harness tests alone do not qualify that pump or establish unattended completion.

### October 18 follow-up

The October 16 preview run `bbb81ff9-f74e-428f-b437-9c351552b4b1` consumed its configured 96 model calls and paused. It saved 14 definitions/source files and 29 instances, with a single-part root; no revision or complete assembly exports were published. Three isolated housing builds ran. Two failed on generated edge selection/filleting; the third passed B-rep integrity but failed its proposed size requirement. Independent measurements were 90 × 130 × 7.48 mm; this is not the requested 20.06 mm housing thickness, nor proof of bore clearance. The coordinator's proposed bounding-box requirements also do not establish shaft spacing or gear diameters.

Calls 50–95 were source reads without further edits or new CAD evidence. The previous immediate-repeat guard missed cycles through multiple unchanged files. Recovery also retained the saturated dialogue, and the durable part-test summary omitted failed requirement details after the full tool report left recent context.

The follow-up tracks observation receipts in typed checkpoint state, independently of rolling dialogue. Three repeated observations following the last newly informative observation route to the existing bounded automatic planner. Initial inspection of 60 distinct files is allowed; source edits, new test evidence and human updates renew the observation window. Reads still return their source. No mechanical rule or part lock is added, and total call/recovery limits remain enforced.

Recovery now starts a focused dialogue while retaining the exact request, source, notes, receipts and validation in authoritative context. Part-test summaries retain measured component extents, requirement statuses and explicit verification status. CAD integrity success is explicitly distinguished from dimensional acceptance; stale measurements remain historical. Missing-file patch errors now explain how unsaved proposals differ from saved source.

The API suite passed 340 tests, including checkpoint recreation during a nonconsecutive read cycle, initial 60-file inspection, evidence/source freshness and recovery with retained failed measurements. A fresh hosted run is still required to qualify this follow-up. The original pump remains incomplete and unverified.

The hosted control also exposed the existing service-wide single-active-run queue. This policy remains unchanged, but waiting previously consumed the scheduler's 120-transition allowance and discarded a pending resume payload before a worker claimed it. The scheduler now counts only completed graph transitions, preserves the response across busy polls, and uses durable queue backoff from 5 to 60 seconds. Its 512-transition safety ceiling leaves room for build/validation/recovery nodes within the graph's unchanged model/resource budgets. Unexpected scheduler states fail with a diagnostic. Queue waiting is not evidence of provider rate limiting. This is consistent with the platform's [durable run and sleep model](https://vercel.com/docs/workflows/pricing); it does not qualify multi-tenant concurrency or indefinite retention. Scheduler tests cover 180 busy polls followed by progress, resume consumption exactly after a claim, and bounded/invalid transition handling. The live pump run was preserved on its original deployment; the unstarted queued control was cancelled for requalification on the scheduling fix.

Further live inspection found a direct loss of useful actions: pump model call 7 returned a source read, an edit containing 16 source files, and a manifest edit. The previous single-action adapter handled only the read. The remaining proposals were privately recorded but not executed or queued. Output token counts therefore did not describe actual executed progress. This is a demonstrated harness limitation, not evidence that the model only attempted to read.

CAD now checkpoints the provider's remaining actions and executes one per graph transition in response order, without another model request. Every action uses the existing role, source, manifest and independent build checks. A failed prerequisite, control handoff or changed human request cancels the remaining proposals with explicit feedback; unsaved source can be selectively retrieved. A pending build's tool protocol remains intact until validation supplies its result. Original model/resource budgets remain in force, and this does not claim transactionally exactly-once source patches across a process failure between a source write and its graph checkpoint. Tests cover queued read/edit execution after checkpoint recreation, unchanged model-call accounting, failed prerequisite cancellation, new human guidance and pending-build protocol preservation. Prompt version is `2026-10-18.agent-harness.14`.


### October 21 qualification and scope repair

The part-test/full-build interface is now explicit: `test_component(componentId=...)` only returns isolated diagnostics; `build(final=true)` has no model-facing component ID and builds the complete saved root for review. Legacy calls combining a component ID with final=true receive an actionable scope error rather than silently repeating a probe. Reused probes participate in observation-cycle recovery. Queued actions remain retained after a component test.

The previous hosted control `c188128b-12a4-4077-94b3-eb9a86199644` ultimately succeeded at 14 model calls after automatic recovery. Its independent full build passed four checks and published the measured 60 x 40 x 6 mm plate with one 8.125 mm Z through-hole. This qualifies that control flow, not the pump. Downloads and latest-source qualification remain separate checks. The pump resumption remained queued with 22 consumed model calls when checked October 21; its scheduler state must be reconciled before redispatch.

The full local API suite passed 344 tests, including distinct model-facing scopes, actionable legacy-scope feedback, queue preservation after probes and repeated-probe recovery. Prompt version is `2026-10-21.agent-harness.15`. These changes add no mechanical recipes or part locks. Production release and complete pump acceptance remain pending.


The preserved pump scheduler `wrun_01M57QYVCJ9GRW54VFYNXCNHK7` was independently confirmed running with its latest event an October 18 `wait_created`, due at 14:55:21 UTC that day. No subsequent workflow event or model call was present on October 21. An owned-run administrator recovery endpoint now re-enqueues that same workflow on its original deployment only when the CAD run is queued, no worker lease is active, and the latest durable queue wait is at least two minutes overdue. The wake key uses the persisted workflow/event identity to deduplicate repeated requests. It does not create a CAD run, reset usage, bypass model retry authorization or promote geometry. Active/unconfirmed work receives a conflict instead. Tests use the installed SDK's typed event model and cover admin/ownership checks. The complete API suite passed 352 tests before deployment; hosted recovery remains to be exercised.
