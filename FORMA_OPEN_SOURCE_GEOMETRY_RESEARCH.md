# Forma — Open-Source Geometry and Verification Research

**Prepared:** September 2026  
**Scope:** CAD generation, geometry integrity, assembly semantics, interference, clearance, and motion checks.  
**Out of scope:** orchestration, routing, model-provider selection, and deployment workflow design.

## Executive recommendation

Forma should use a stack, not search for one library that solves the whole product:

1. Keep generated Python as the editable construction source.
2. Keep CadQuery for the current generator and compatibility while the product stabilizes.
3. Make Open CASCADE Technology (OCCT), accessed through OCP/Python bindings, the geometry authority for imported STEP inspection, Boolean-result checks, topology, measurements, and exact solid interference.
4. Add a trusted Forma geometry helper library around OCCT for risky operations such as cavities, raceways, sockets, keyways, shells, pockets, and connected fusions.
5. Use build123d selectively for prototypes or new code that benefits from explicit topology, locations, joints, and assembly objects. Do not assume that changing CadQuery to build123d alone fixes the failures; both rely on OCCT.
6. Use FCL only as an optional broad-phase and continuous-motion accelerator. Reported exact results should still come from an OCCT narrow phase.
7. Study FreeCAD Assembly for joint and multi-state semantics, but do not make the full FreeCAD desktop runtime the first serverless production dependency.
8. Do not use SolveSpace as the production geometry core because its GPLv3 license and non-OCCT representation do not fit the current service boundary.

The important fix is a **code contract plus a trusted geometry layer plus deterministic evidence**. More prompt tokens, more retries, or a different model will not reliably create a missing raceway or a missing housing cavity.

## What Forma is building

Forma is a chat-driven CAD copilot. The user describes a part, an engineering design, or an assembly. The system creates and edits a Python CAD workspace, runs it in an isolated CAD environment, exports STEP and GLB, and shows a preview, component tree, progress, and requirement evidence. The user remains the design reviewer and may accept, edit, or download the candidate.

The intended source of truth is:

```text
user request + accepted assumptions
        ↓
structured requirements and engineering notes
        ↓
generated Python CAD source + parameters + assembly manifest
        ↓
OCCT/CadQuery build
        ↓
STEP/XDE and preview mesh
        ↓
independent deterministic geometry audit
        ↓
human review, edit, or download
```

STEP, GLB, reports, and previews are derived artifacts. Code, parameters, named datums, and declared constraints are the editable design record.

## What currently works

The observed tests show that Forma can already:

- Interpret long natural-language design tasks.
- Route engineering questions and collect assumptions.
- Generate multi-part manifests with repeated instances.
- Produce readable STEP/GLB artifacts in many cases.
- Place axes, holes, ports, and other objects at requested nominal coordinates.
- Preserve a previous revision when a build fails.
- Pause and resume graph work through the current LangGraph/Vercel Workflow design.
- Produce useful preliminary engineering calculations when the inputs are explicit.

The independent audits also show a meaningful strength: nominal placement math passed repeatedly. Examples include exact gear center distances, three-way port concurrency, compound-angle placement, and enclosure hole alignment.

That strength must be separated from physical correctness. A component can be in the mathematically correct location while still being embedded in another solid, missing a cut, disconnected, or colliding with a neighboring component.

## What the test evidence says

### IoT sensor enclosure

The requested six-part enclosure had correct nominal alignment for the tested hole relationship. The independent audit still found a small PCB-to-cable-gland overlap. This is a clearance-generation problem, not an axis-placement problem.

### Motor bracket

The engineering screen produced finite calculations and useful assumptions, but it was a preliminary hand calculation. It did not prove fatigue, local stress concentrations, joint slip, frame stiffness, or certified material allowables. The design was also affected by pauses, model-contract failures, and execution limits in earlier tests.

### Pedal box

The generated file contained more solids than the requested assembly and had physical collisions between pedal pads, arms, bracket regions, and springs. The envelope also exceeded the requirement. The common failure is that the assembly was placed, but neighboring moving or enclosing geometry was not checked in the required states.

### Two-stage gearbox

Both gear center distances were exact: 40 mm and 48 mm. However, the gears and parts of the shafts were embedded in the housing, and two of three keyways were not actually cut. This is the clearest example of a valid placement result paired with failed cavity and feature construction.

### Three-way valve

All three port axes converged at the intended ball center. One perpendicular outlet still interfered with the body. Concurrency therefore passed while the local Boolean relief was wrong.

### Suspension strut and control arm

The compound camber/caster axis matched the independent vector calculation. The ball joint was nevertheless embedded in the arm and knuckle. Again, placement passed while socket clearance failed.

### Deep-groove bearing

The requested bearing was the most revealing stress test. The manifest count itself was inconsistent: the prompt says 12 total instances, but the listed components sum to 13. The exported root assembly contained 20 solids because the cage was split into eight pieces. The races were plain cylindrical forms in the audit rather than the required toroidal raceways. Balls were buried into the race surfaces instead of having the requested small positive contact clearance. The PCD and nominal ball angles were present, but the raceway geometry and capture/clearance were not.

This proves that the current system can preserve numbers from a request without producing the corresponding topology.

## The actual technical gap

The dominant gap is not the agent loop. It is the lack of a trusted semantic geometry contract between generated code and deterministic inspection.

Today, a generated candidate can effectively say:

```text
"I made a gear, a pocket, and a groove."
```

The validator mostly asks:

```text
"Did the script run, and is the exported file readable?"
```

Those are different questions. The validator needs evidence that the requested feature exists in the final BRep and that the feature has the requested relation to other parts.

The recurring gaps are:

- No trusted post-export exact pairwise solid intersection check.
- No minimum-distance/clearance report for every relevant pair.
- No feature registry for named axes, holes, groove centerlines, pockets, mating faces, or contact surfaces.
- No check that a Boolean cut produced the expected topology and volume change.
- No check that a requested raceway is toroidal/revolved rather than a plain cylinder.
- No check that a shell or cage remains one connected component when it should.
- No assembly-level state representation for rest, travel, valve positions, or rotated bearing checks.
- No requirement types for center distance, axis concurrency, tangency, contact clearance, containment, or tolerance.
- No semantic repair feedback when the build succeeds but the geometry is wrong.
- No distinction between “CAD file built” and “all requested requirements passed.”

The product can remain human-reviewed, but it still needs to tell the reviewer exactly what was built, what was measured, and what remains unverified.

## Current code audit

The worktree confirms that this is a real capability gap rather than a theoretical concern:

- `runtimes/python/forma_runtime.py` imports each component STEP independently, checks basic BRep validity, creates preview meshes, and reconstructs preview placement from manifest transforms. It does not compute exact pairwise solid intersections, minimum distances, contact/tangency, or pose-specific interference.
- `runtimes/python/requirements_check.py` currently recognizes only `dimensions`, `center`, `solid_count`, `through_holes`, `corner_radius`, and `unverified`. There are no executable checks for axes, center distances, concurrency, containment, clearance ranges, motion states, raceway type, keyway presence, or mass.
- `apps/api/forma_api/contracts.py` limits the requirement contract to those same basic kinds. The manifest stores components, dependencies, instances, frames, and parameters, but no named datums, feature references, mating interfaces, or poses.
- `apps/api/forma_api/graphs/design.py` can normalize invalid instance parents by flattening them to a buildable draft. That protects execution, but it can hide a semantic assembly-tree defect unless the flattening is treated as a failed requirement rather than a successful repair.
- The CAD repair context receives build/contract failures, but it does not receive a structured semantic report such as “gear is 96% embedded in housing,” “raceway feature absent,” or “ball clearance is -2.25 mm.” A model cannot reliably repair measurements it never receives.
- The current publication policy intentionally allows a buildable draft with failed or unsupported advisory requirements. That is compatible with a human-reviewed copilot, but the UI and report must never label that result as fully validated.

These findings explain the test pattern: the current code can prove that a file was produced and that some nominal dimensions are present, but it cannot prove that the final assembly has the physical relationships requested by the user.

## Open-source options

| Project | Best use in Forma | Strengths | Risks or limits | License / fit |
|---|---|---|---|---|
| [OCCT](https://occt3d.com/dev/doc/overview/html/index.html) | Geometry authority and exact validator | BRep solids, Boolean operations, shape validity, distances, STEP/XDE assemblies, names and instances | C++-oriented API; Python bindings and packaging need pinning | LGPL 2.1 with exception; strong fit |
| [CadQuery](https://github.com/CadQuery/cadquery) | Current generated Python construction layer | Python parametric CAD, primitives, booleans, assemblies, STEP export, Apache ecosystem | Free-form model code can omit cuts or create bad topology; history/selectors can be fragile | Apache-2.0; keep initially |
| [build123d](https://build123d.readthedocs.io/en/stable/) | New helper/template layer and assembly prototypes | OCCT-based, explicit topology and locations, builder/algebra modes, joints, labels, assemblies | Not a validator by itself; migration would be substantial | Apache-2.0; selective use |
| [FreeCAD Assembly](https://reqrefusion.github.io/FreeCAD-Documentation-html/wiki/Assembly_Workbench.html) | Reference for joints, constraints, BOM, and static pose checks | Revolute, cylindrical, slider, ball, distance, angle, gears, repeated instances, simulation | Large desktop-oriented runtime; packaging/startup risk in serverless functions | LGPL-2.1; reference or optional service |
| [FCL](https://github.com/flexible-collision-library/fcl) | Broad-phase and continuous-motion candidate filtering | Collision, minimum distance, tolerances, contact points, continuous collision detection | Triangle/mesh based; exact BRep truth still needed; Python binding maintenance | Verify repository license before shipping; optional |
| [SolveSpace](https://solvespace.com/index.pl) | Constraint and mechanism research only | Parametric constraints, mechanisms, fit verification, linkage simulation | GPLv3; different geometry representation; poor fit for the BRep/STEP core | GPLv3; reject as primary runtime |

OCCT is the only item in this list that directly addresses the central failure class across Boolean construction, BRep validity, exact distance, and assembly-aware STEP data. CadQuery and build123d are construction interfaces over that kernel. FCL can reduce the cost of motion screening, but it should not be trusted alone for tight CAD clearances.

## Proposed code-first architecture

### 1. Generated source remains editable

Every candidate must produce source files, a manifest, and machine-readable metadata. The source can use CadQuery, build123d, or direct OCP APIs, but the output contract is the same.

### 2. Add a trusted Forma Geometry Library

This is a small reviewed Python package. Generated code calls it for operations where a missing or malformed Boolean is dangerous:

```python
make_shell(...)
cut_cavity(...)
make_raceway(...)
make_socket(...)
make_keyway(...)
make_pocket(...)
fuse_connected(...)
assert_single_solid(...)
```

Each helper must:

- Work in explicit millimetre units.
- Check Boolean success and non-empty results.
- Run BRep validity analysis.
- Check expected solid count and connectivity.
- Compare volume/area before and after a cut where a relief is required.
- Return structured diagnostics with feature ID, operation, expected result, measured result, and repair guidance.

The helper library does not restrict the agent to a catalog of shapes. It makes high-risk operations observable and repeatable.

### 3. Require a feature and datum registry

In addition to `manifest.json`, a candidate should emit `feature_registry.json` and `constraints.json`.

Example feature registry:

```json
{
  "features": [
    {
      "id": "outer_race.raceway",
      "type": "toroidal_raceway",
      "component_id": "outer_race",
      "axis": {"origin_mm": [0, 0, 0], "direction": [0, 0, 1]},
      "radius_mm": 3.1,
      "source_operation": "make_raceway"
    }
  ],
  "datums": [
    {"id": "shaft.input_axis", "component_id": "input_shaft", "origin_mm": [0, 0, 0], "direction": [0, 0, 1]}
  ]
}
```

Example constraint types:

```json
{
  "constraints": [
    {"type": "axis_concentricity", "a": "shaft.input_axis", "b": "housing.input_bearing_axis", "tolerance_mm": 0.1},
    {"type": "center_distance", "a": "input_pinion.pitch_axis", "b": "compound.large_pitch_axis", "value_mm": 40.0, "tolerance_mm": 0.05},
    {"type": "minimum_clearance", "a": "ball.01", "b": "outer_race.raceway", "range_mm": [0.005, 0.02]},
    {"type": "no_interference", "a": "gear.output", "b": "housing.body"},
    {"type": "pose", "id": "pedals.full_travel", "angle_deg": 25.0}
  ]
}
```

This prevents the validator from guessing which cylinder or face represents a requested design feature.

### 4. Validate the imported result, not the model's claim

The validator should import the final STEP/XDE and independently measure it with OCCT/OCP:

1. Confirm file readability and BRep validity.
2. Count solids and connected components per named component.
3. Read assembly names, instances, and transforms through STEP/XDE where available.
4. Measure bounding boxes, volumes, areas, axes, centers, radii, and distances.
5. Compute exact pairwise solid intersections for declared collision pairs and relevant broad-phase candidates.
6. Compute minimum distances for clearance and contact pairs.
7. Verify Boolean-created features through topology, volume delta, and feature metadata.
8. Transform the assembly into every declared pose and repeat collision/clearance checks.
9. Compute mass only when density is assigned; otherwise report it as unavailable.
10. Produce evidence per requirement, including expected, measured, tolerance, method, and status.

OCCT supplies Boolean `Common`, `Fuse`, and `Cut` operations, BRep validity analysis, distance computation, and STEP/XDE support. See the [OCCT Boolean guide](https://sso.opencascade.com/doc/occt-6.8.0/overview/html/occt_user_guides__boolean_operations.html), [BRepCheck documentation](https://dev.opencascade.org/doc/occt-7.8.0/refman/html/package_brepcheck.html), [distance API](https://dev.opencascade.org/doc/occt-7.8.0/refman/html/BRepExtrema__DistShapeShape_8hxx.html), and [STEP/XDE guide](https://sso.opencascade.com/doc/occt-6.8.0/overview/html/occt_user_guides__xde.html).

### 5. Use FCL only to reduce work

For a large assembly or multiple poses, FCL can first identify likely collisions and compute approximate mesh distances. The validator then runs OCCT exact checks on those pairs. This gives a faster broad phase without replacing the authoritative BRep result.

### 6. Feed semantic failures into repair

Build success is not enough to enter a generic repair loop. Repair input must include typed failures such as:

- `missing_feature`
- `boolean_empty_result`
- `boolean_did_not_relieve_volume`
- `disconnected_component`
- `solid_count_mismatch`
- `interference`
- `clearance_out_of_range`
- `axis_mismatch`
- `center_distance_mismatch`
- `pose_interference`
- `topology_mismatch`
- `mass_unavailable`

Each failure should include component/feature IDs, expected and measured values, the exact pair or pose, and a suggested repair. A repair is valid only if the candidate hash changes and the failure is resolved or explicitly remains unverified.

## How this addresses the observed failures

| Observed failure | Root cause | Required fix |
|---|---|---|
| Gear embedded in housing | Housing cavity not reliably cut | `cut_cavity` helper, Boolean volume-delta check, exact intersection validator |
| Keyways absent | Source mentioned a feature but final topology did not contain it | Named keyway feature, topology/volume check, source-to-result evidence |
| Perpendicular valve port intersects body | Correct axis placement without local body relief | Port feature registry plus exact intersection and wall-thickness checks |
| Suspension joint embedded in arm/knuckle | Socket clearance not constructed | `make_socket` helper plus minimum-distance/intersection check |
| Bearing race plain cylinder | Requested toroidal feature was not created | `make_raceway` helper; surface/type/radius inspection; fail closed on missing feature |
| Bearing cage split into pieces | Pocket cuts severed the ring | connected-component check and minimum remaining ligament check |
| Balls buried in races | PCD placement was right, radial clearance was not | compute contact geometry and exact minimum distances at all 16 contacts |
| Pedal/spring collisions | No pose-level pairwise collision validation | explicit rest/full-travel poses and exact collision checks |
| Extra solids | Manifest and exported topology disagree | per-component solid count and XDE identity checks |
| Envelope or mass mismatch | Requirement coverage is incomplete | typed envelope and density-aware mass checks |

## What not to do

- Do not treat a valid Python exit code as a valid design.
- Do not trust the LLM to infer that a Boolean cut succeeded.
- Do not use only bounding boxes; they miss local collisions and curved contact behavior.
- Do not use only triangle collision as the final answer for tight CAD fits.
- Do not rely on persistent face numbers as semantic references; use named datums and feature IDs.
- Do not hide failed requirements behind a generic “validated geometry” label.
- Do not expect a model with a larger context window to repair an unmeasured geometric defect.
- Do not replace CadQuery with build123d as a standalone “fix.” The missing layer is deterministic evidence and trusted helpers.
- Do not make FreeCAD or SolveSpace a mandatory production dependency before measuring cold-start, memory, licensing, and export behavior.

## Adoption plan

### P0 — Make exported geometry trustworthy

- Add OCCT/OCP BRep validity and exact pairwise interference checks.
- Add Boolean-result and volume-delta checks to cavity, pocket, shell, keyway, and groove helpers.
- Reject malformed or disconnected components where a single solid is required.
- Separate labels: `CAD build passed`, `geometry integrity passed`, `requirements passed`, `unverified`, and `human review required`.

### P1 — Make requirements executable

- Add feature/datum registry and typed constraint schema.
- Implement axis, center-distance, concurrency, containment, minimum-clearance, and envelope checks.
- Add named poses and repeat validation for motion/state requirements.
- Preserve component definition versus instance identity in STEP/XDE and the preview.

### P2 — Make difficult geometry repairable

- Add trusted helpers for raceways, sockets, cavities, pockets, keyways, and connected fusions.
- Feed semantic validator diagnostics to the CAD repair step.
- Require changed candidate hashes and stop repeated identical failures.
- Add fixture tests for the gearbox, valve, suspension, pedal, and bearing cases.

### P3 — Scale the checking cost

- Add FCL broad-phase filtering for large assemblies and motion states.
- Cache exact checks by candidate hash, requirements hash, pose hash, and runtime hash.
- Add mass properties with explicit materials and density.
- Evaluate build123d helpers and optional FreeCAD interoperability only after the OCCT validator is stable.

## Decision gates before calling the pipeline reliable

Forma should not claim reliable assembly generation until it can:

- Detect the gearbox's missing cavity and embedded gears before publication.
- Detect the bearing's missing raceways, split cage, and buried balls.
- Detect pedal collisions at both rest and full-travel poses.
- Detect valve port/body interference even when all axes are concurrent.
- Detect a missing keyway even when the shaft and gear are correctly placed.
- Produce per-requirement evidence instead of a single model-generated pass statement.
- Resume a validation run without repeating external operations.
- Preserve the last good revision when a candidate fails.

The acceptance target is not “the model never makes a mistake.” It is “a bad candidate cannot silently pass, and the user receives a precise, actionable explanation.”

## Licensing and operational notes

- OCCT is LGPL 2.1 with an exception. Review the exact exception and linking obligations before distributing a bundled runtime.
- CadQuery and build123d are Apache-2.0 projects and are the easiest fit for generated Python and helper code.
- FreeCAD is LGPL-2.1; its workbench and source are useful references, but packaging the full application requires separate runtime evaluation.
- SolveSpace is GPLv3 and should not be linked into the core service without a deliberate licensing decision.
- Confirm the current FCL repository license and the maintenance status of any Python binding before adding it to a hosted runtime.
- Pin the exact OCCT/OCP, CadQuery/build123d, and FCL versions. Geometry behavior, Boolean robustness, and export topology can change across versions.

## Research questions for a deeper study

1. Which OCP distribution gives the smallest reliable Vercel Sandbox image while retaining Boolean, BRepCheck, distance, XDE, and tessellation APIs?
2. Can exact pairwise OCCT intersection handle the largest expected assembly within the execution budget, or is FCL broad-phase necessary from the first release?
3. Which feature recognition methods reliably distinguish a toroidal raceway, keyway, socket, and pocket in imported STEP when source metadata is absent?
4. Which XDE export/import path preserves component names, repeated instances, and transforms through the browser preview pipeline?
5. What minimum geometry evidence should be required before a human can download a candidate?
6. How should intentionally open surfaces, sheet-metal solids, and multi-body parts be represented without forcing every component into one-solid rules?
7. Which operations need parameterized trusted templates, and which can safely remain free-form generated code?
8. What are the cold-start, memory, and licensing costs of an optional FreeCAD interoperability service compared with direct OCP?

## Bottom-line design choice

Keep the current conversational product and graph architecture. Strengthen the CAD core around a simple rule:

> **Generated code proposes geometry. OCCT builds and measures it. Only measured evidence can describe what was delivered.**

That rule directly addresses the failures found in the five assembly audits while preserving the broad modeling capability Forma is intended to offer.
