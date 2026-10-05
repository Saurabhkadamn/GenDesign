# Engineering drawings implementation

Implement revision-linked engineering drawings, automatic measured dimensions and engineer-authored GD&T in the backend and workspace UI. Preserve the native assembly/BOM release and unrelated original-checkout work. Work branch: `codex/engineering-drawings`, based on released main `dc3a22c`.

## Required end state

- Generate orthographic, isometric and section views from independently reopened, accepted STEP B-reps using OCCT hidden-line removal; never measure the preview mesh.
- Store bounded, typed sheet/view/dimension/datum/tolerance definitions in the versioned manifest. Preserve definitions through ordinary parameter edits.
- Generate useful automatic dimensions and allow explicit dimension selection, tolerances and layout adjustment. Resolve geometric references uniquely after regeneration; removed or ambiguous geometry must produce explicit unresolved annotations, not substitution.
- Provide structured datum labels and feature-control frames. Engineers supply tolerance intent. Validate the supported combinations and distinguish annotation correctness from physical manufacturing conformance. No claim of complete ASME/ISO standards coverage or manufacturing approval.
- Produce vector SVG/PDF/DXF and drawing evidence JSON from the same revision identity. Bind assembly balloons and drawing BOM to accepted occurrence/BOM evidence.
- Provide a usable Drawings workspace: sheet/part selection, readable zoomable sheets, annotation editing, standards/projection/scale controls, issues and private downloads.
- Permit direct UI generation without requiring an LLM call; use bounded durable execution and the existing ownership, lease, idempotency, artifact and revision publication infrastructure. AI editing must use the same typed drawing definitions and fresh validation gate.
- Qualify analytic part measurements, projection/section geometry, removed/ambiguous references, tolerance validation, regeneration, private ownership/downloads and a representative assembly locally and in the actual Linux runtime. Verify the backend/UI flow and publish only verified changes.

## Work and evidence

Native projection/measurement and typed contracts first; then exporters, API/durable jobs and UI. Tests must challenge geometric accuracy and reference behavior, not only mirror serialization. Use the existing qualified native adapter and pinned dependency workflow. Runtime source changes require a newly qualified image. Keep credentials and private evidence outside source. Do not redefine success around a static drawing preview or symbol-only GD&T.

Current status (5 October 2026): implemented, qualified and verified live through PR #20. The additive database migration is applied. Local qualification passes 229 API tests, 68 native runtime tests with zero skips, and 16 database tests; TypeScript, focused ESLint and the production web build pass. All 68 native tests also pass on the actual Linux image. Hosted candidate and final public production acceptance verify direct durable jobs, fresh validation, private downloads, missing-reference rejection, ownership and browser-submitted regeneration of three sheets with a 60-occurrence assembly BOM. See [drawing release progress](FORMA_DRAWING_RELEASE_PROGRESS.md) for exact deployment/runtime provenance, usage and remaining engineering limits.
