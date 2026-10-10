# Manifest repair verification — 2026-10-10

The Nebius pump run failed after successful model responses, not from a
reported provider rate limit. Replay of the three rejected tool manifests
against the production contract produced:

| CAD turn | First authoritative contract error |
| --- | --- |
| 7 | Unknown root component |
| 8 | Unknown root component |
| 9 | Missing source: assemblies/gear_pump_assembly.py |

The same proposed manifests also ground a housing occurrence that is used
as a parent of a cover, request a nonexistent motion joint, and combine a
motion driver with zero allowed DOF. None of these edits was accepted.

## Repair behavior

- Return related staging, grounding and motion blockers with exact field
  paths and actionable guidance, without echoing private source.
- Retain the rejection in graph state and current model context independently
  of the rolling dialogue. Reads do not clear it; successful atomic staging does.
- Count three stalled contract repairs, allowing resolved blockers to reset
  that counter. The existing global model-call budget still bounds the run.
- Block both automatic and provider-selected builds while the rejected edit
  needs repair. An invalid edit is never saved or executed.
- Persist source-free diagnostic codes and paths as run validation events.
- Keep Snapshot, geometry validation, solver qualification and publication
  gates unchanged. Do not fabricate an assembly source or gear constraints.

## Local evidence

Python API suite: **242 passed**. Regression coverage includes the rejected
pump relationships, private-source exclusion, stalled retries, root-to-source
repair progress, context after dialogue loss, atomic candidate preservation,
successful contract repair, and refusal of premature builds.

This establishes harness repair behavior. It does not establish that a model
can generate the complete pump, that generated geometry is correct, or that
the pump is suitable for manufacture. A fresh hosted acceptance run is required.

## Hosted retest and coordinator correction

PR #26 was released as commit `1795567119d55fff2826036b7abea21d066c5af4`.
Production deployment `dpl_FYBkhfCEfCJXqVQE9Rj8ZN7bTafL` is READY and owns
`forma-cad-eosin.vercel.app`. Authenticated production routes and the saved
Nebius connection/tool check returned HTTP 200.

Fresh project `054f825f-3f63-440d-a255-7466a6c0a286`, run
`f8f72149-94fb-48d5-ad55-ae9f89bea661`, used the identical request. It paused
at 12 coordinator actions before CAD. The coordinator repeatedly inspected
unchanged context and rejected delegation supplied scalar arrays for three-axis
dimension requirements. No geometry or artifacts were published. The checkpoint
contained the matching inspection tool results: this was not lost conversation
history. There was no reported provider 429 in this run.

The follow-up correction caches inspected context by digest, removes unchanged
inspection from available tools, and rejects provider calls to unavailable tools.
It carries complete field-level delegation feedback separately from rolling
history. Portable tuple schemas now state their exact numeric arity. Unsupported
diameter/spacing/clearance checks remain unverified instead of padding dimensions
with invented values. Successful delegation resets the local coordinator action
counter; the global model-call budget remains intact.

Local API suite after this correction: **245 passed**, including inspection-cache
recovery/invalidation, enforcement when a provider ignores tool availability,
unsupported scalar-dimension repair, and visible tuple arity. A new hosted pump
run remains necessary to qualify the complete generated design.

The coordinator correction was released as `ef444732e60a2d1f6cc5163827ab81b63f869e85`,
deployment `dpl_H1VK8vvLTWqVmUwqwhNy3qsw1Wx5`. Fresh project
`5fc2efc9-1e19-4df1-a3d5-5468c2fa15d2`, run
`6abcd76d-adb0-44f3-9be2-7356dfcece99`, progressed from one inspection to CAD
delegation in three calls. It failed at seven model calls because CAD repeated
the same rejected edit. No sources, revisions or artifacts were accepted.
The proposed manifest left rootComponentId null while enabling native motion.

This exposed a diagnostic omission: a null native root was mentioned by the
authoritative Snapshot error but absent from the related repair-item list.
The follow-up diagnostic correction explicitly lists the null root and missing
physical occurrences, and always retains an authoritative error not covered by
other repair items. The repeated-action terminal message retains unresolved
contract issues. Snapshot validation and the stalled-action limit stay intact.
Local API suite with this correction: **247 passed**. Full generated pump
acceptance remains unverified until a fresh hosted test produces the required
geometry and evidence.

## Requirement tolerance semantics

The next fresh test reached delegation with exact solid_count and unsupported
DFM notes, but their unused tolerance fields (2 pieces, 1 degree, 2 mm fillets,
4 mm wall) were rejected by the shared 0.1 mm maximum. That limit belongs to
supported measured geometry checks. It is not meaningful for exact integer
inventory or requirements that the runtime explicitly cannot verify.

Requirement validation now applies the existing positive, at-most-0.1-mm limit
to dimensions, maximum dimensions, centres, through holes and corner radii.
Exact count/state requirements canonicalize tolerance to zero; their comparisons
remain exact. Nonnegative finite tolerance metadata on unverified notes cannot
turn them into measured or passing requirements. No runtime/kernel changes or
new image are involved.

Local API suite: **261 passed**. A regression replays the rejected delegation
and runs the actual trusted requirement checker: 26 solids still fail an exact
27 count despite the model's supplied tolerance=2, and draft/fillet/wall notes
remain unverified. Tests retain the original bounds for every measured check
and reject negative/infinite/NaN metadata.
