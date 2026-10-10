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
