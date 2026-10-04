# Native assembly and BOM selection

Research and first qualification: 2026-10-01. This decision describes the integration being built; it does not claim the hosted release is complete.

## Selected responsibilities

| Responsibility | Selected technology | Why and qualification boundary |
| --- | --- | --- |
| Parametric geometry, B-rep and surfaces | Existing CadQuery/OCP/OpenCascade stack | Preserve editable Python and the native geometric kernel. Prior local fixtures verified B-spline and rational NURBS exchange; they did not qualify Class-A surfacing or every operation. |
| Assembly joints and mechanisms | A narrow C++ process adapter to pinned FreeCAD/OndselSolver | Native engine, typed marker frames, explicit grounding and bounded execution. Five supported joint types, numeric drivers, four-bar motion and contradictory grounding passed adapter qualification on Windows and hosted Linux. |
| Occurrence identity and CAD exchange | OCP XCAF/XDE | STEP hierarchy distinguishes a reusable definition from its placed occurrences. XDE carries exchange attributes; it does not define company part numbers or release policies. |
| BOM | Forma deterministic traversal of validated occurrences and explicit component metadata | Structured and flat BOMs need application-specific revision, variant, exclusion and purchased/phantom assembly policies. A solver or solid count is not a BOM source. Initial JSON/CSV exports need no additional package. |

References: [OndselSolver](https://github.com/FreeCAD/OndselSolver), [OpenCascade XDE](https://occt3d.com/dev/doc/overview/html/occt_user_guides__xde.html), [FreeCAD BOM implementation](https://github.com/FreeCAD/FreeCAD/blob/main/src/Mod/Assembly/App/BomObject.cpp), [CadQuery assembly API](https://cadquery.readthedocs.io/en/latest/assy.html).

FreeCAD's BOM is an application document object with spreadsheet and UI integration. Its linked-object grouping and recursive traversal are useful references; importing a GUI command into Forma's headless service would bring unrelated dependencies and would still not implement Forma's identity policies. No FreeCAD BOM source is copied into the application.

## Qualification findings that change the adapter

Pinned native commit: `4be80eef02a3486cda0d78f3ccbb308d207a9639`. The portable Windows build uses GCC 16.2.0 and MinGW 14 UCRT. The ordinary Release build removes assertions, including input-consuming expressions in the upstream ASMT parser. The qualification build retains assertions. The production adapter constructs typed engine objects directly and accepts no ASMT file or arbitrary motion expression from generated code.

The qualification helper itself initially dereferenced a temporary shared pointer in a C++17 range loop. That lifetime bug was fixed before recording results. Saved ASMT history also appends assembly times while replacing part series; the fixture probe clears the old time history before a new run. Native output includes an input-state frame at time zero before the solved initial frame. The adapter must distinguish these rather than accepting the unsolved input frame as a solution.

The independent four-bar oracle checked 408 joint/frame combinations. Maximum closed-loop position residual was approximately `6.35e-9 mm`; maximum driver-angle error was approximately `4.00e-15 rad`. These are measurements on one planar mechanism, not promised accuracy for arbitrary customer assemblies. See [raw qualification summary](FORMA_NATIVE_MECHANISM_QUALIFICATION.json).

## Required acceptance behavior

1. Preserve definition IDs and occurrence IDs separately. Each joint endpoint identifies an occurrence and a component-local reference frame; repeated bolts cannot share an ambiguous component-only endpoint.
2. Begin with fixed, revolute, slider, spherical and cylindrical relationships only as each passes qualification. Unsupported types and limits remain explicitly unverified or fail activation; do not silently interpret them as supported joints.
3. Require explicit grounded occurrences and report remaining degrees of freedom. A mechanism may intentionally retain freedom; it needs an explicit allowance and, for motion, a bounded numeric driver. Contradictory constraints must fail an independent residual gate even if the native engine drops redundant constraints.
4. Run the native binary as a bounded child in the existing isolated CAD worker. Crash, timeout, non-finite poses, incomplete motion or excessive residuals produce a recoverable build failure, not a published solved result.
5. Derive STEP, preview and engineering inspection from the same accepted occurrence state. Validate individual occurrence placement and geometry after independent STEP reopening, including internal changes that leave the outer bounding box unchanged.
6. Generate BOMs only from that validated inventory. Quantities count occurrences, not solids. Missing part numbers remain visibly missing; generated IDs are never presented as approved company part numbers. Do not merge revisions, variants, materials or configurations just because names match.
7. Bind BOM and assembly evidence to the candidate/runtime identity and published revision. Download links enforce the existing project ownership controls.

## SaaS and packaging

OndselSolver's repository license is LGPL-2.1. The JSON header candidate is nlohmann/json 3.12.0 under MIT; its official single-header SHA-256 is pinned. This architecture keeps the adapter source and native library build reproducible. Include dependency notices and corresponding source/relinking material when distributing LGPL-linked binaries as required by the actual distribution arrangement. Server-only use and redistribution have different obligations; the license inventory is not a legal clearance for every deployment model.

Hosted build qualification is separate from Windows qualification. Install the locked Python dependencies and the Linux native library/binary in the CAD snapshot, preserve notices, attest their actual installed versions and hashes, and run the same positive and negative cases before promotion. Copying a lockfile or producing a website preview does not qualify the runtime.

## Qualified release scope

The native adapter, degree-of-freedom checks, occurrence-level STEP gate, BOM exports/UI and actual hosted Linux installation are implemented and qualified. Authenticated preview/production publication and nine private downloads passed. Clean AI workflows changed a repeated 60-occurrence plate assembly from 4 to 3 mm while preserving 59 joints and the complete owned baseline structure. Independent downloaded-STEP/BOM checks passed; contradictory grounding is rejected. The verified production candidate is promoted. See [hosted acceptance evidence](FORMA_HOSTED_ASSEMBLY_ACCEPTANCE.json).

This is limited assembly/BOM qualification with draft outputs, not industrial assembly scale or CATIA/SOLIDWORKS parity. There is no engineering drawing/GD&T generator. Final source integration and resulting main-deployment verification are tracked in [release progress](FORMA_ASSEMBLY_BOM_RELEASE_PROGRESS.md).
