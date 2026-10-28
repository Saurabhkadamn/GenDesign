# Forma code standards

Keep the engineering record, application API, and native CAD runtime as separate
boundaries. Generated CAD Python stays in the isolated runtime; it must not be
imported into the API process. Persist requirements and candidate identities
before treating a model response as a design result.

## Before changing code

- Read the relevant `AGENTS.md` and the module's existing contracts.
- Extend the owning module instead of adding a near duplicate. Preserve the v3
  graph and prompts while existing runs can still resume on v3.
- Add a migration for database changes. Never edit a migration that may already
  have been applied. Keep a fence and immutable input identity on v4 worker
  writes.
- Keep provider credentials, private prompts, and signed URLs out of source,
  logs, test fixtures, and generated reports.
- A completed build is a draft. Requirement outcomes need current candidate
  evidence; acceptance requires an explicit engineering decision.

## Style and checks

- Python: four-space indentation, type annotations on public interfaces, Ruff
  lint rules from `apps/api/pyproject.toml`, and Ruff formatting for new files.
  Use direct names and small functions; remove unused imports and variables.
- TypeScript/React: use the repository's ESLint and Prettier settings. Do not
  derive state in an effect when a keyed component or render-time value works.
- Keep test fixtures and geometry recipes out of production decision logic.
  Retain a legacy file while a live run or import still depends on it.

Run `npm run lint:api`, `npm run lint`, and `npm run typecheck` for affected API
and web code. Run focused tests for changed behavior; use the full suites and
agentic engineering tasks for release qualification.
