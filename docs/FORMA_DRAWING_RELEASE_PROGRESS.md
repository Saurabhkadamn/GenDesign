# Forma engineering drawings release

Authoritative worktree: `C:\Users\SAURABH\.codex\worktrees\native-assembly-bom\v1`; branch `codex/engineering-drawings`, based on released `dc3a22c`. Preserve unrelated original-checkout work in `D:\v1`.

## Current checkpoint — 5 October 2026

Native OCCT projections and sections, measured dimensions, geometric references, engineer-authored datums/control frames, SVG/PDF/DXF exports, root-assembly BOM/balloons, direct durable drawing jobs and the Drawings workspace are implemented. Removed or ambiguous references block export/publication. The direct job transfers owned accepted STEP into a fresh validator and does not invoke an LLM or execute part source. Existing revision publication and lease checks remain the final acceptance gate.

The UI preserves unsaved edits while switching sheets and document tabs, labels the saved preview when edits are pending, and prevents exporting stale drawing files as the new edits. New sheet selection permits choosing a different part. The Supabase `engineering_drawings` migration was applied successfully; it adds bounded drawing requests and a service-only admission function, extends artifact kinds and private MIME support, and retains ownership/RLS.

Verified locally: **229 API tests**, **68 native runtime tests, zero skips**, **16 database tests**; TypeScript, focused ESLint and the production web build pass. Native tests include analytic dimensions, hole locations, missing/ambiguous references, dimension regeneration, section hatching, vector GD&T and DXF dimensions that recalculate after endpoint edits. A fresh STEP validator qualifies a 60-occurrence assembly drawing with matching BOM quantity/identity and balloon anchors. This is repeated simple hardware, not 60 distinct complex industrial parts.

PDF sheets were rendered and inspected. The local browser fixture exercised the actual web UI with real native regeneration and preserved edits through sheet/tab switches. It substitutes local authentication/database responses; it is not evidence of the actual hosted API, durable job or production access controls.

The Linux factory `forma-engineering-drawings-20261005` passed all **68 native tests with zero failures or skips**, root ownership, runtime attestation and supervisor preparation, and saved a qualified immutable image. A compile log-stream timeout was recovered against the same confirmed terminal command; the runner now records command IDs and reads terminal status separately from output. The initial CLI failures came from an expired login; normal CLI refresh resolved them. Private state/logs and the exact image receipt are under ignored `D:\v1\test-results\drawings-linux-20261005`.

Qualified image: `snap_8Ud0MQfvXntkB1acdc9RcdQwHElt`, runtime `forma-343517bf371ce748`, build source hash `3d03894b4d659f4f183e89a079beaf1e0e20592ffdfbb19bd64ff24e418859b1`.

Actual hosted candidate `dpl_6frd2fZF4a2mv17UVMdCWR3BCx2o` (`https://forma-6515e2z10-negens-projects.vercel.app`, application commit `1318475`) passed real authenticated API, durable Workflow/LangGraph checkpoints, fresh STEP validation, private storage and lease-fenced publication. Run `62b9d56e-3de9-4879-ae59-ee3d407d4438` published three sheets and nine SVG/PDF/DXF exports with zero model calls. Downloaded native dimensions match analytic width 8 mm and bore 2 mm; DXF audit passes with native dimensions and millimeter units. PDFs were rendered and inspected, including the bore section and GD&T frame. The assembly drawing and BOM report 60 validated occurrences.

Negative run `7566e0fa-b59f-47ec-9dee-ce439f326a3f` rejected a missing bore reference and preserved the last good revision. Real access checks return anonymous download 401; another owner's download, project and drawing submission 404. An exact request retry returns the same run; conflicting key and stale revision return 409; malformed sheet requests return 400 without work.

The actual browser preserved edits across tabs/sheets and disabled stale exports. Browser-submitted regeneration `49bce13f-00df-4a30-b5ec-e58ebe8705b8` passed and published revision `ed8c095d-13ae-54b5-afd5-364b35aa6d38`; the saved title, native geometry, three sheets, exports and BOM survived regeneration. The PDF button works. Desktop focus and 390 px mobile layout were inspected; no uncaught browser errors. Existing Three.js deprecation/context-lost console messages appeared during viewport changes. Private receipts, downloads and screenshots: ignored `D:\v1\test-results\drawing-hosted-acceptance`. `scripts/verify_drawing_release.py` retains submission/run identity before polling and verifies the hosted acceptance cases.

**Public production promotion is pending at this checkpoint.** The stable URL still serves the qualified assembly/BOM release `dpl_DVKNbNP82CMwGgUCNbSBJrHUQkwd`, main `dc3a22c`; retain it as rollback. Remaining release gates: integrate PR #20, pair the qualified production runtime settings, verify the main build and stable URL after publication.

## Engineering boundary

All sheets are drafts requiring engineering review. The annotation convention covers the implemented subset, not full ASME/ISO certification. GD&T records design intent, not inspection of manufactured parts. Plane/cylinder queries resolve only unique geometry; this is not a general solution to persistent topological naming. Curved display traces use bounded sampling while dimensions use native geometry. Assembly drawing BOM currently targets the root assembly. Large/crowded sheets, arbitrary surfacing annotations and advanced drafting workflows need further qualification.

Keep the complete implementation/release goal active until the required native, backend, UI and hosted evidence is verified. Do not substitute the local browser fixture or an export-only test for hosted acceptance.
