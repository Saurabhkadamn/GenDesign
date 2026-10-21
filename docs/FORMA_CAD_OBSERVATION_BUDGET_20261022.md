# CAD observation budget and loop recovery — 2026-10-22

## Failure observed

The hosted external gear-pump run stopped after repeated reads of unchanged source. The run had no accepted revision or generated geometry; continuing it from the current browser returned a runtime-identity mismatch. A read-only generation could consume tens of thousands of input tokens while producing no edit or test evidence.

## Change in harness 25

- Keep only the original request and recent protocol-safe exchanges, with a 30-message limit and an 80,000-character ceiling. The 30-message window preserves the complete 14-file provider read batch used by the recovery-source handoff.
- Count observation-only model decisions per unchanged candidate/evidence epoch. Several read tools returned by one provider generation count as one decision.
- After three observation-only decisions, withhold source-reading, searching, and workspace-diagnosis tools. A provider batch of many reads still counts as one decision. Keep edits, component tests, assembly builds, engineering analysis, and concrete user questions available.
- A candidate edit or new validation/probe evidence starts a new observation epoch. This does not lock a component or report geometry as accepted.

## Long-run agent research

Anthropic's official [agentic systems guidance](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/prompt-templates-and-variables) recommends incremental work across context windows, structured state such as tests and progress files, concise notes, Git checkpoints, and verification tools. The [OpenAI Agents SDK sessions guide](https://openai.github.io/openai-agents-python/sessions/) describes persistent sessions with threshold-triggered compaction between turns and accounts for compaction usage. Both approaches keep durable task state separate from an ever-growing transcript.

Forma already stores the candidate, graph state, acceptance evidence, component probes, and working notes outside the model prompt, and it selectively restores source around a failure. The hosted pump evidence still showed read-only calls consuming roughly 42,000 input tokens each, so history trimming alone cannot solve repeated no-progress decisions. The new three-decision gate limits that waste while retaining one batched inspection opportunity per model call. A preview qualification must still verify actual provider token counts and successful source edit/build behavior.

## Verification

- Current harness API suite: **371 passed**, including an integration regression that simulates three distinct source-read turns and verifies the fourth turn makes a substantive source edit with observation tools withheld.
- Local external gear-pump prototype: 29 named solids; all three STEP states reopen as valid 29-solid files; all-pairs collision checks pass at 0°, 5.29°, and 10.59°.
- The supplied axial-clearance tolerance chain still fails at worst case: 0.060–0.090 mm against a 0.040–0.080 mm requirement. The original dimensions remain unchanged in the test deliverables.
- No hosted pump build, publication, or release has been verified by this change. The old hosted run cannot be resumed under the current runtime identity.
