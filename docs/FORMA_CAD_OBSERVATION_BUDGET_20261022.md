# CAD observation budget and loop recovery — 2026-10-22

## Failure observed

The hosted external gear-pump run stopped after repeated reads of unchanged source. The run had no accepted revision or generated geometry; continuing it from the current browser returned a runtime-identity mismatch. A read-only generation could consume tens of thousands of input tokens while producing no edit or test evidence.

## Change in harness 25

- Keep only the original request and recent protocol-safe exchanges, with a 30-message limit and an 80,000-character ceiling. The 30-message window preserves the complete 14-file provider read batch used by the recovery-source handoff.
- Count observation-only model decisions per unchanged candidate/evidence epoch. Several read tools returned by one provider generation count as one decision.
- After six observation-only decisions, withhold source-reading, searching, and workspace-diagnosis tools. Keep edits, component tests, assembly builds, engineering analysis, and concrete user questions available.
- A candidate edit or new validation/probe evidence starts a new observation epoch. This does not lock a component or report geometry as accepted.

## Verification

- Current harness API suite: **369 passed**.
- Local external gear-pump prototype: 29 named solids; all three STEP states reopen as valid 29-solid files; all-pairs collision checks pass at 0°, 5.29°, and 10.59°.
- The supplied axial-clearance tolerance chain still fails at worst case: 0.060–0.090 mm against a 0.040–0.080 mm requirement. The original dimensions remain unchanged in the test deliverables.
- No hosted pump build, publication, or release has been verified by this change. The old hosted run cannot be resumed under the current runtime identity.
