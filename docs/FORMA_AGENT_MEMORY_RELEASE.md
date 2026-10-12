# Durable agent context repair

The pump investigation found that CAD recovery instructions aged out of a rolling 30-message history, earlier build errors were not retained independently, and a 102,163-character response without an executable action inflated later prompts.

This change preserves exact human updates across ordinary pauses, clarification nodes and actual CAD recovery interrupts. The existing private LangGraph checkpoint stores an ordered `user_updates` ledger and the accumulated request. No new database tables or migration are required. Consecutive identical updates are idempotent. Existing checkpoints without the new fields remain readable; already-discarded guidance cannot be reconstructed automatically.

`working_memory` retains tool outcomes, locations, repair guidance, edited-file status and source hashes independently of dialogue. `update_working_notes` lets the continuing agent save its objective, current task, decisions, pending requirements, failed approaches and next action. Partial updates preserve omitted fields. Notes are explicitly agent-reported; they do not pass validation, modify source or lock a component. `read_working_memory` retrieves older outcomes with pagination. Current prompts include recent outcomes and label their relationship to the current candidate. Unchanged historical messages cannot erase known provenance.

The context budget includes serialized messages and tool schemas, using a conservative UTF-8 token estimate for the current provider-neutral integration. `FORMA_AGENT_CONTEXT_TOKENS` defaults to 48,000 estimated input tokens. This is not the model's published context limit or an exact tokenizer count. Over-budget dialogue drops complete old tool exchanges while retaining authoritative context; if the authoritative packet itself is too large, the run pauses instead of silently truncating requirements. Output limits and total model-call limits remain separate. Very large responses without tool calls are compacted in active dialogue; original generations and provider-required reasoning/signature fields remain retained.

Repeated unusable CAD actions now stop at the existing durable recovery interrupt rather than ending the run permanently. This covers missing/malformed tool actions, repeated ineffective actions, missing paths, empty or invalid Python, and stalled workspace contracts. Explicit Continue renews only the local action/repair allowances; total model calls, build attempts, candidate, requirements and unresolved contract feedback remain retained. Failed tool feedback is also captured in working memory, while successful source reads are not copied there. This allows recovery without silently replacing the model or geometry. Historical runs already marked terminal failed are not rewritten by this change.

Automatic three-edit builds now defer when a multi-component workspace still has a single-part root, so the agent receives the existing root repair context first. This changes orchestration ordering, not engineering geometry.

The initial hosted run retained the exact clarification and used both working-memory tools, but then repeatedly staged a script without a registered buildable part. Source/staging diagnostics now accompany the CAD context and successful edits. The optional `diagnose_workspace` tool reports top-level entrypoints, unregistered files and the root without executing source. These observations are advisory and do not reject or rewrite geometry. Full component execution diagnostics remain separate work.

## Validation

Local regression coverage exercises real LangGraph checkpoint interruption/resumption and a new graph instance reading the checkpoint; exact-value preservation beyond the dialogue limit; multiple clarifications; unchanged source and usage counters; failure provenance after edits; retrieval of older outcomes; complete tool protocol pairs; notes as partial patches; and retained full generation output with compact active narrative.

Final test and hosted release evidence are recorded separately under `D:/v1/test-results`. Hosted acceptance uses the saved provider/model and isolated test projects. The full external gear pump is still unverified; this release does not turn nine recovered partial STEP files into a completed pump.

The API regression suite passed 305 tests. The two initial hosted agent runs failed after 42 and 24 model calls respectively, with no published revision. Both retained the exact 8.125 mm clarification. The first exposed missing entrypoints/registration; the second used the new staging diagnostics and exported actual geometry, then exposed an existing validator defect: it required an assembly root even for one standalone occurrence.

The validator now permits one standalone occurrence only when it refers to the root, has no parent and uses no native joints. Existing independent STEP solid inventory, placement and symmetric-difference checks still apply; displaced geometry and multiple occurrences still fail. This runtime change passed 74 qualified CAD tests with zero skips/failures and unchanged native engine attestation. The qualified runtime is `forma-537dd448bdeb66e6`; its release image was snapshotted with expiration explicitly disabled, then independently queried to verify no expiration.

The exact saved agent candidate was rebuilt without source edits and validated in a separate VM containing STEP and metadata, without generated Python. Occurrence validation now passes. Measured dimensions are 60 x 40 x 6 mm and the single through-hole is 8.125 mm. The plate's Z bounds are 0 to 6 mm, so its centre is [0,0,3], failing the requested [0,0,0] centre. Three requirements pass and one fails. This is a model-generated positioning error, correctly exposed after the Forma validator repair; runtime success is not full request acceptance.

## Remaining work from the review

- Component/subassembly diagnostic tools and isolated API probes are not implemented here.
- Version-matched CAD documentation retrieval is not implemented here.
- Agent pending-requirement notes are not a complete measured acceptance ledger.
- Context token estimates should be calibrated against provider usage and native tokenizers where available.
- Cross-run project memory and automatic semantic summaries need separate evaluation. These changes persist the current run's working state.
- Further evidence-based stall detection and repair-budget tuning remain separate. Repeated CAD action stops are now resumable while preserving total usage limits.

No pump fixture, mechanical recipe library or permanent part lock is introduced.
