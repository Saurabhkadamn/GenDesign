# Historical verification notes — 31 August 2026

The current cloud verification is maintained in [CLOUD_VERIFICATION.md](CLOUD_VERIFICATION.md). This older report is retained as history and includes superseded free-only statements.

Forma is deployed at `https://forma-cad-eosin.vercel.app`. Supabase Auth, schema, private Storage, Vercel build output, and the Vercel Sandbox runtime are configured. The owner has now authorized the exact Nemotron free endpoint's data-collection exception for testing. Paid inference stays disabled; connection tests still verify actual model availability and structured tool support.

## Checks

- TypeScript checking covers the app, domain packages, adapters, scripts, and tests.
- ESLint passes for application code; Ruff passes for the Python runtime and fixtures.
- 35 domain/database/model-policy tests pass, including RLS isolation, private-source access denial, credential encryption/tampering, atomic publication, stale-revision rejection, compute reservations, owner-checked pause/resume, free-price enforcement, exact-model synthetic logging scope, safe error messages, portable tool schemas, bounded protocol repair, and specialist configuration inheritance.
- Seven Python tests cover real STEP round trips, parameter changes, open surfaces, stable assembly instance IDs, placement mismatch rejection, assembly constraints, path containment, repeat calculations, and scientific unit/numerical checks.
- The production Next.js build passes. The localhost workspace explicitly disables live generation when service configuration is absent.
- Desktop and mobile browser checks cover chat suggestions, composer state, preview/calculation tabs, setup guidance, and responsive layout. No browser errors were observed.
- A separate read-only harness loads an actual CAD-exported GLB through the production renderer. Part visibility and wireframe controls were visually verified. Three.js emits a dependency deprecation warning about Clock; no renderer errors were observed.
- The installed JavaScript dependency tree reports zero known advisories in the live npm audit. Scoped Workflow overrides pin patched Nano ID and Undici versions.

## Runtime compatibility

Python 3.12, CadQuery 2.8.0, and OCP 7.9.3.1.1 are locked. On this Windows machine, newer CasADi wheels caused a native failure during interpreter shutdown when combined with the CAD solver libraries. The Windows-only CasADi 3.6.7 pin restores a clean process exit and passes the actual assembly-constraint test. Linux wheels are independently resolved in the same lockfile.

## Live model checks

The user's testing key authenticated with OpenRouter; it is stored only in an ignored local test environment file. Live requests were restricted to named zero-price models, with zero provider price caps and no fallback.

- The requested `nvidia/nemotron-3-ultra-550b-a55b:free` passed synthetic coordinator delegation, missing engineering-input clarification, and CAD source/manifest preparation in five model calls. The reviewed source defines a centered parametric plate. This contract result alone does not establish geometry validity.
- The exact generated plate candidate (SHA-256 `184f36d00eead92ea03f61f710d315a3bffaecae00bec270da293feb800c1151`) was manually reviewed before local execution. Separate real CAD build and STEP-validation processes passed at 40×20×5 mm and after a parameter-only change to 60×20×5 mm, with volumes 4000 and 6000 mm³, centered bounds, one solid, and genuine STEP/GLB artifacts. This is a reviewed synthetic fixture check, not the hosted Sandbox integration or a general local execution permission for model code.
- The generated plate's GLB also loaded in the production R3F renderer through the read-only harness. Selection, hiding, and wireframe were checked in the browser. The current workspace's desktop layout and Files panel passed inspection without browser errors; the harness retains the existing Three.js Clock deprecation warning.
- NVIDIA's free endpoint logs prompts. The user chose it for testing after that warning. A separate exact-model flag permitted logging during the hosted synthetic connection check. That endpoint timed out once and then rejected the second hosted check, matching current upstream instability reports. Retries stopped, the saved connection was removed, and production was restored to `data_collection: deny`.
- The exact reviewed candidate then ran in the pinned Vercel snapshot. It passed a fresh unprivileged build sandbox, a separate clean validation sandbox, centered 40×20×5 mm geometry/4000 mm³ volume, and real STEP/GLB header and size checks. This exposed and fixed detached-command polling: terminal state is now obtained through a bounded SDK `wait()` rather than assuming `getCommand()` refreshes it.
- MiniMax M3 free passed a structured connection test after switching from forced to automatic tool selection. Its broader test later timed out in the CAD phase. Inkling free returned HTTP 403. Neither is the selected test model.
- OpenRouter's catalog is now surfaced in admin model settings. Catalog inclusion does not imply provider access, privacy compatibility, or a passed connection test.
- Versioned agent guidance now includes CadQuery placement/API details. Missing tool calls get at most two persisted, budgeted corrections, then pause; model/provider errors produce safe, actionable messages.

Sources: [OpenRouter NVIDIA endpoint terms](https://openrouter.ai/nvidia/nemotron-3-ultra-550b-a55b:free), [MiniMax endpoint](https://openrouter.ai/minimax/minimax-m3:free), [Inkling endpoint terms](https://openrouter.ai/thinkingmachines/inkling:free), [provider routing policy](https://openrouter.ai/docs/guides/routing/provider-selection). Availability is point-in-time, not a reliability guarantee.

## Hosted interruption fix

Two mounting-plate requests stopped during coordinator-to-CAD handoff because saved but inactive specialist overrides prevented inheritance from the active default. Production now inherits the tested default for missing, inactive, or untested overrides; a genuinely unavailable configuration pauses with recovery instructions instead of failing with the generic interruption message. Settings describes this inheritance. Four regression tests cover runtime selection, credential-role preservation, exact-role connection tests, and rejection of unusable defaults. TypeScript, lint, all 35 tests, and the hosted production build pass.

The exact 80×50×6 mm mounting-plate request was retried in its existing project after deployment. It passed the previous handoff failure, reached four model calls, and then paused with the provider's free-model daily-quota message. No revision or artifacts were published, so this is not a completed end-to-end geometry test. Saved model choices were preserved; paid inference remains disabled and the owner-authorized Nemotron testing exception remains enabled. Resume after provider capacity is available rather than submitting duplicate requests.

## Remaining handoff work

1. Sign in with the locally stored owner credential and move it into a password manager.
2. The owner has since saved and activated a free default connection. Wait for its provider quota to reset or deliberately test and activate another available free connection, then Continue the paused mounting-plate request. A paid model is not required for testing. For later private work, disable `OPENROUTER_NEMOTRON_TESTING`; if deliberately switching to a paid model, also set `OPENROUTER_FREE_ONLY=false` and redeploy.
3. Complete live chat → Workflow → Sandbox → private Storage → browser preview with an available model. Exercise cancellation, history restoration, and retention before inviting another engineer.

No existing Supabase projects were changed. The hosted Forma project is isolated and public signup is closed. Model credentials subsequently saved through Settings are encrypted server-side; keys are not exposed in browser responses or committed to the repository.
