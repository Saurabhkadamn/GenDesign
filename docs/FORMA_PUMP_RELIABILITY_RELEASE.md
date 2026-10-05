# Pump-run reliability repair

The first hosted external gear pump run (`69eb388e-a06d-47cd-85d0-04883f336a31`) stopped on the housing after three failed builds. It created no gears, accepted assembly, revision or STEP exports. The first source called a solid fillet on a wire. The two later errors had empty diagnostics; their exact termination cause cannot be recovered from the saved normalized errors.

Controlled reproduction confirms that extruding the two overlapping circle profiles together produces invalid B-rep geometry. Joining separately extruded valid cylinders produces a valid figure-eight cutter. This identifies a defective construction in the retained housing source, without claiming it proves the cause of the old silent terminations.

## Changes

- Unbuffered worker output and Python fault handling retain the active component and native crash stack. Supervisor receipts always give exit code/signal/deadline context, even without a traceback.
- API failures retain exit, timeout and cleanup fields; distinct signals no longer share an empty diagnostic fingerprint.
- Invalid component geometry is rejected before STEP export. The independent fresh STEP publication gate remains in place.
- CAD guidance describes safe overlapping cutter construction and feature isolation.
- A validated intermediate milestone resets the automatic repair allowance. Exhausting that allowance retains the private candidate and enters a durable recovery interrupt; continuation re-plans the candidate without losing its failed identity, total model calls or attempt count. Automatic loops remain bounded.

Failed source is retained privately for continuation; this change does not introduce a draft-source editor or a public diagnostic download. It does not implement a deterministic involute gear service, contact/backlash solver, pressure validation or arbitrary pump acceptance rules.

## Qualification

Local API: **234 passed**. Native Windows and actual Linux: **70 passed each, zero failures/skips**. New tests reject the overlapping-profile extrusion before export, reopen the repaired cavity STEP, and verify durable continuation from the same candidate.

Hosted unprivileged fixtures check the wire-fillet error, invalid profile rejection, a deliberate SIGSEGV with file/stack evidence, a silent SIGKILL with explicit reason, process cleanup and no STEP from failures. The repaired cavity passes a separate source-free STEP validator. The first positive probe did not complete its assertions; the separately persisted positive rerun passed. These are construction/diagnostic fixtures, not a completed pump.

Qualified image: `snap_5pN2HAViJqXOCN8wvUDAmOtsO9Hm`; runtime `forma-8bf337601d968def`; source hash `9d329810edb5a951fe956dcc6cb3e03eb0cb5e0a2115cf1f6285ea5beee7e711`. It derives from `forma-343517bf371ce748` with unchanged installed native binaries, packages, assembly and drawing code; those facts are checked before and after installation, and the full native suite runs again.

Private receipts: `D:/v1/test-results/pump-linux-20261005`, `pump-diagnostic-acceptance`, `pump-reliability-api.xml`, `pump-reliability-native.xml`. Retain production deployment `dpl_4z1yYKRUBjgWz5tFpEDNuoNwk1Aj` and runtime `forma-343517bf371ce748` / `snap_8Ud0MQfvXntkB1acdc9RcdQwHElt` as rollback.

## Acceptance boundary

Submit the exact original brief in a new private project after live verification. Its request SHA-256 is `b197af0c4a608dbb7e2a0cc921c3fa899ce2217e5144d07842ac30ef3716c3e3`. Do not silently modify tolerances: axial clearance is **0.060–0.090 mm**, failing the specified **0.040–0.080 mm** range. A new run is not accepted merely because it starts or publishes a draft. Three-state STEP, involute/backlash/interference, 27 independent occurrences, calculations, measured comparisons, tolerance chains, standards, DFM and mass still require their own evidence. Pressure sealing, ripple, wear, cavitation and efficiency remain unverified.
