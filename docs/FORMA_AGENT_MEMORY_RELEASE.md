# Durable agent context repair

The pump investigation found that CAD recovery instructions aged out of a rolling 30-message history, earlier build errors were not retained independently, and a 102,163-character response without an executable action inflated later prompts.

This change preserves exact human updates across ordinary pauses, clarification nodes and actual CAD recovery interrupts. The existing private LangGraph checkpoint stores an ordered `user_updates` ledger and the accumulated request. No new database tables or migration are required. Consecutive identical updates are idempotent. Existing checkpoints without the new fields remain readable; already-discarded guidance cannot be reconstructed automatically.

`working_memory` retains tool outcomes, locations, repair guidance, edited-file status and source hashes independently of dialogue. `update_working_notes` lets the continuing agent save its objective, current task, decisions, pending requirements, failed approaches and next action. Partial updates preserve omitted fields. Notes are explicitly agent-reported; they do not pass validation, modify source or lock a component. `read_working_memory` retrieves older outcomes with pagination. Current prompts include recent outcomes and label their relationship to the current candidate. Unchanged historical messages cannot erase known provenance.

The context budget includes serialized messages and tool schemas, using a conservative UTF-8 token estimate for the current provider-neutral integration. `FORMA_AGENT_CONTEXT_TOKENS` defaults to 48,000 estimated input tokens. This is not the model's published context limit or an exact tokenizer count. Over-budget dialogue drops complete old tool exchanges while retaining authoritative context; if the authoritative packet itself is too large, the run pauses instead of silently truncating requirements. Output limits and total model-call limits remain separate. Very large responses without tool calls are compacted in active dialogue; original generations and provider-required reasoning/signature fields remain retained.

Automatic three-edit builds now defer when a multi-component workspace still has a single-part root, so the agent receives the existing root repair context first. This changes orchestration ordering, not engineering geometry.

The initial hosted run retained the exact clarification and used both working-memory tools, but then repeatedly staged a script without a registered buildable part. Source/staging diagnostics now accompany the CAD context and successful edits. The optional `diagnose_workspace` tool reports top-level entrypoints, unregistered files and the root without executing source. These observations are advisory and do not reject or rewrite geometry. Full component execution diagnostics remain separate work.

## Validation

Local regression coverage exercises real LangGraph checkpoint interruption/resumption and a new graph instance reading the checkpoint; exact-value preservation beyond the dialogue limit; multiple clarifications; unchanged source and usage counters; failure provenance after edits; retrieval of older outcomes; complete tool protocol pairs; notes as partial patches; and retained full generation output with compact active narrative.

Final test and hosted release evidence are recorded separately under `D:/v1/test-results`. Hosted acceptance uses the saved provider/model and isolated test projects. The full external gear pump is still unverified; this release does not turn nine recovered partial STEP files into a completed pump.

## Remaining work from the review

- Component/subassembly diagnostic tools and isolated API probes are not implemented here.
- Version-matched CAD documentation retrieval is not implemented here.
- Agent pending-requirement notes are not a complete measured acceptance ledger.
- Context token estimates should be calibrated against provider usage and native tokenizers where available.
- Cross-run project memory and automatic semantic summaries need separate evaluation. These changes persist the current run's working state.
- Repair quotas and additional evidence-based stall detection remain separate from this context fix.

No pump fixture, mechanical recipe library or permanent part lock is introduced.
