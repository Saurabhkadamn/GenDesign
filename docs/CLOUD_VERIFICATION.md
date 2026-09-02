# Python cloud migration verification

Updated 1 September 2026. This report separates verified components from the remaining production release gates.

## Confirmed

- Vercel Services deploys Next.js and Python under one preview URL; `/api/health` identifies Python 0.2.0.
- A hosted Python workflow completed two steps separated by durable sleep, returning 42.
- Authenticated preview checks previously passed for login, HTTP-only secure cookies, session, projects, administration and duplicate chat submission. The LangGraph/LangSmith cutover requires a new preview acceptance run.
- Python API tests cover credential isolation, CSRF, model selection, role permissions, bounded failures, exact request extraction, LangSmith redaction and paid model selection.
- Database tests cover cross-environment rejection, worker fencing, cancellation precedence and publication guards.
- Browser verification confirms persistent Show/Hide workspace controls and unrestricted model-ID fields with all-catalog suggestions. Saved keys remain masked.
- The new testing key passed a real free Nemotron tool call; all three saved Nemotron role connections were updated using role-bound encryption.
- The supplied paid key passed a real structured tool call for `deepseek/deepseek-v4-flash-0731` through OpenRouter and the deployed Python API. The coordinator connection is encrypted, tested and active; no model fallback is used.
- LangSmith verification is pending the new preview deployment.

## Actual cloud geometry and timings

The exact mounting plate fixture was built remotely, then imported and independently verified in a separate Vercel sandbox. STEP and GLB were produced. All five checks passed: 80×50×6 mm dimensions, origin centering, one solid, four Ø6 mm through-holes at X=±30/Y=±15, and four R3 outer corners. Bounding-box tolerances are reported in the evidence. An intentionally failed next build could not reuse the previous STEP output.

Latest measured sample, no model calls:

| Attempt | Environment | Preparation | Python execution | Total build |
|---|---|---:|---:|---:|
| 1 | Fresh | 6.49 s | 4.28 s | 11.71 s |
| 2 | Reused | 2.53 s | 2.93 s | 6.69 s |
| 3 | Reused | 2.19 s | 2.88 s | 5.95 s |

Independent validation is additional; these totals are build measurements, not end-to-end chat latency. Results are a small fixture benchmark, not an SLA. Raw evidence is in ignored `test-results/cloud-execution.json`.

## Still being verified

- Complete live model request → build → publication → preview/download path with paid DeepSeek has not been run end to end; its structured tool connection check passed. Free Nemotron has intermittently rejected model requests; runs paused safely before publication.
- Full resume/restart and cancellation acceptance against hosted workflows, beyond unit/database coverage.
- Mobile layout, measured contrast and actual published GLB inspection in the new frontend.
- Production cutover is complete: the stable `forma-cad-eosin.vercel.app` health endpoint reports Python 0.2.0. The promoted production deployment was verified before alias promotion.
- Full paid-model design inference remains untested. The app allows paid/free model selection at the owner's request; the only paid call so far was the synthetic structured-tool connection check.
