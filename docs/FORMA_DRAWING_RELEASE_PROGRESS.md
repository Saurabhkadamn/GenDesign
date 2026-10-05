# Forma engineering drawings release

Authoritative worktree: `C:\Users\SAURABH\.codex\worktrees\native-assembly-bom\v1`; branch `codex/engineering-drawings`, based on released `dc3a22c`. Preserve unrelated original-checkout work in `D:\v1`.

## Current checkpoint — 5 October 2026

Native OCCT projections and sections, measured dimensions, geometric references, engineer-authored datums/control frames, SVG/PDF/DXF exports, root-assembly BOM/balloons, direct durable drawing jobs and the Drawings workspace are implemented. Removed or ambiguous references block export/publication. The direct job transfers owned accepted STEP into a fresh validator and does not invoke an LLM or execute part source. Existing revision publication and lease checks remain the final acceptance gate.

The UI preserves unsaved edits while switching sheets and document tabs, labels the saved preview when edits are pending, and prevents exporting stale drawing files as the new edits. New sheet selection permits choosing a different part. The Supabase `engineering_drawings` migration was applied successfully; it adds bounded drawing requests and a service-only admission function, extends artifact kinds and private MIME support, and retains ownership/RLS.

Verified locally: **229 API tests**, **68 native runtime tests, zero skips**, **16 database tests**; TypeScript, focused ESLint and the production web build pass. Native tests include analytic dimensions, hole locations, missing/ambiguous references, dimension regeneration, section hatching, vector GD&T and DXF dimensions that recalculate after endpoint edits. A fresh STEP validator qualifies a 60-occurrence assembly drawing with matching BOM quantity/identity and balloon anchors. This is repeated simple hardware, not 60 distinct complex industrial parts.

PDF sheets were rendered and inspected. The local browser fixture exercised the actual web UI with real native regeneration and preserved edits through sheet/tab switches. It substitutes local authentication/database responses; it is not evidence of the actual hosted API, durable job or production access controls.

The Linux factory `forma-engineering-drawings-20261005` passed all **68 native tests with zero failures or skips**, root ownership, runtime attestation and supervisor preparation, and saved a qualified immutable image. A compile log-stream timeout was recovered against the same confirmed terminal command; the runner now records command IDs and reads terminal status separately from output. The initial CLI failures came from an expired login; normal CLI refresh resolved them. Private state/logs and the exact image receipt are under ignored `D:\v1\test-results\drawings-linux-20261005`.

**Drawings are not yet released live.** Production remains on the qualified native assembly/BOM release `dpl_DVKNbNP82CMwGgUCNbSBJrHUQkwd`, main `dc3a22c`. Required next evidence: qualified Linux image; real hosted direct job/publication and private downloads; actual browser acceptance; source review/integration; verified production publication and stable URL checks.

## Engineering boundary

All sheets are drafts requiring engineering review. The annotation convention covers the implemented subset, not full ASME/ISO certification. GD&T records design intent, not inspection of manufactured parts. Plane/cylinder queries resolve only unique geometry; this is not a general solution to persistent topological naming. Curved display traces use bounded sampling while dimensions use native geometry. Assembly drawing BOM currently targets the root assembly. Large/crowded sheets, arbitrary surfacing annotations and advanced drafting workflows need further qualification.

Keep the complete implementation/release goal active until the required native, backend, UI and hosted evidence is verified. Do not substitute the local browser fixture or an export-only test for hosted acceptance.
