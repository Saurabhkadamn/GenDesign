# Forma native assembly and BOM release

Goal: qualify native assembly technology, integrate assembly and deterministic BOM end to end, strengthen engineering reliability, verify local and hosted workflows, push reviewed source and publish the verified release. The goal remains active until every release gate below passes.

## Authoritative checkpoint — 2026-10-01 17:45 UTC

- Worktree: `C:\Users\SAURABH\.codex\worktrees\native-assembly-bom\v1`; branch `codex/native-assembly-bom`, base `3cdebe0`. Preserve the original `D:\v1` checkout, its authorized Forma patch and unrelated projects. Conflicts were reconciled; no unmerged entries remain.
- Pinned native engine: OndselSolver `4be80eef02a3486cda0d78f3ccbb308d207a9639`, via typed C++ process adapter. CadQuery/OCP performs geometry; XDE preserves placed occurrence identities; Forma derives BOMs from accepted physical inventory. See `FORMA_NATIVE_ASSEMBLY_BOM_DECISION.md` and `FORMA_ENGINEERING_ARCHITECTURE_DECISION.md`.
- Fixed, revolute, slider, spherical and cylindrical joints qualified. Numeric drivers, grounding, residuals and instantaneous DOF independently checked. Four-bar oracle: 101 solved frames, 408 joint/frame checks. Contradictory grounding is rejected even when the upstream solver reports success.
- STEP, preview, inspection and BOM derive from accepted placements. A separate STEP-only worker recomputes the solve and checks each physical solid and XDE occurrence. Internal displacement with unchanged overall bounds is rejected.
- BOM supports flat and structured quantities, repeated/nested occurrences, purchased/phantom/reference/exclusion policies, explicit part/revision/variant metadata and JSON/CSV exports. Missing identities stay incomplete; all outputs remain drafts.
- API suite: **171 passed**. Runtime suite: **45 passed locally and 45 on Linux, zero skips**. Database suite: **10 passed**. TypeScript, ESLint and production web build passed, including masked provider-key fields.
- Hosted private-storage migration `20261001073730_native_assembly_bom_artifacts.sql` applied and verified: `bom`/`assembly` artifact kinds, private bucket, artifact RLS. Original production remains unchanged.
- Actual Linux image is qualified: snapshot `snap_CffU94eyOAoj7JvJEKy4SRtKm53D`, runtime `forma-2d813e6a363b4808`, source hash `b6ea1833b7ac2751e7a509e0fd7f97888a73272afe7d3deb638203516eaa771a`. Source bytes still match. Changing runtime source requires new qualification. Builder `forma-native-release-20261001` is stopped; do not duplicate it.
- Image installs locked Python dependencies, pinned C++ binary/shared libraries, RPATH, LGPL/MIT notices and solver source bundle; attests actual installed versions/hashes; tests with network denied. Private evidence: `D:\v1\test-results\native-linux-release`.
- Real hosted supervisor acceptance passed **60 occurrences at 2 mm and 4 mm thickness**, fresh independent validation, all placement checks, draft BOM quantity 60 and all artifact reads. A contradictory grounded fixture failed before acceptance. Downloaded STEP files also passed a local analytic volume oracle. Evidence: `FORMA_HOSTED_ASSEMBLY_ACCEPTANCE.json`; private originals in `D:\v1\test-results\native-supervisor-acceptance`.
- BOM browser fixture passed both layouts, quantity 60, six artifact links and no console errors; screenshot inspected. API ownership was mocked for this UI check. It does not establish authenticated application downloads.
- Only confirmed local server: `http://127.0.0.1:3107`, production Next server, session `86119` (inspect before reusing). Browser session `forma-assembly-release`. Completed jobs must not be polled/restarted as live jobs.
- No queued/running hosted application runs at the last read-only check. No source push, preview or production cutover yet.

## Remaining release gates

1. Finish source/diff/secret review, stage only Forma files, commit and push this branch.
2. Deploy the existing Vercel project `forma-cad` preview with the qualified snapshot/runtime pair. Preserve existing Supabase project `bisbakbhybkhcjztqnag`, model connections and credentials.
3. Exercise authenticated application publication, candidate identity and lease fences, private BOM/assembly/STEP downloads, ownership rejection, then actual model workflow edit/pause/resume. Runtime supervisor and mocked UI evidence are separate gates.
4. Deploy using production environment without stable alias cutover; verify the complete workflow and artifacts, then promote and verify the stable application. Save old/new runtime settings and deployment IDs privately for rollback.
5. Audit all requirements, mark the goal complete only after the full end state passes, and stop the follow-up automation. If the 2026-10-02 21:57 UTC deadline expires, preserve incomplete work and evidence and end the scheduled follow-up.

## Boundaries

This qualifies a limited native assembly adapter and a repeated-part corpus. It does not establish CATIA/SOLIDWORKS parity, industrial large-assembly scaling, persistent face-attached references, joint limits, gears/contact dynamics, swept collision checks, load-bearing simulation, engineering drawings/GD&T, manufacturing release or enterprise PLM. Unsupported capabilities must remain explicit.

Keep secrets out of source, logs and public evidence. Private environment and authentication files stay under ignored `D:\v1\test-results`. Resume confirmed jobs after account availability returns; do not repeat uncertain charged operations automatically.
