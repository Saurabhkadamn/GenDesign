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

## Fresh hosted pump rerun and harness defects

The unchanged request was submitted to a fresh private Forma project on the verified live deployment on 2026-10-05 (`f19c858c-fd80-4442-9ed9-bb858436fc75`; run `06298abe-c3b0-4aa0-b986-e1de6ebfee9a`). The first four attempts found invalid gear B-reps, an invalid housing through-cut, and an exported solid inventory mismatch. Attempt 7 exposed a valid-but-wrong figure-eight cavity: its driven gear overlapped housing by 16,460.915 mm³. A separate, source-free local fixture reproduced this failure mode. Separately cutting each bore from a valid cylinder fixed the cavity; attempt 8 reported 0.050 mm nominal clearance from each gear to housing and no interference in the three-part as-built check. Attempt 9 found and repaired a draft-extrusion sketch error; attempt 10 passed geometry integrity on that same three-part milestone.

The complete run then paused when the configured Baseten endpoint rejected the next model request with HTTP 402 and `please check your current payment status`. Admin metadata confirms that coordinator model is `deepseek-ai/DeepSeek-V4.1-Flash` at `inference.baseten.co`; Forma has no configured alternate model role. The run has 60 model calls and no published revision or artifacts. The private candidate is retained; the full pump acceptance remains incomplete until that provider account can make requests and the run can continue.

The test independently reproduced two API defects and their fixes are in the code below:

- Quantity-marked mechanical component lists were parsed as numbered acceptance paragraphs instead of part types. The parser now reads either numbered lists or comma-separated names with quantity tuples; a regression fixture extracts the 12 required types from the pump brief.
- Triage normalization widened a stated 0.010 mm limit to 0.050 mm. It now preserves the submitted tolerance; numerical kernel resolution belongs in measurement comparison, not in the user’s design limit.

After these two changes, all API tests pass (**235 passed**). This does not resume the hosted run, validate the missing hardware or cover geometry, resolve the failing axial tolerance chain, certify sampled gear flanks as exact involutes, or produce the three required STEP files. Detailed private evidence is in `D:/v1/test-results/designs/external-gear-pump-rerun-20261005`.
