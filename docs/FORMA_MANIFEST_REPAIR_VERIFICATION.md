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
