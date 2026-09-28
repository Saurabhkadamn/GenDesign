# Forma: live demo and architecture

**[Open the live Forma app](https://forma-cad-eosin.vercel.app/)** · [Read the README and reviewer login guide](../README.md) · [Browse the source](https://github.com/Saurabhkadamn/GenDesign)

This URL is retained for people following the project from my resume. It now describes the current product instead of an old cloud-migration checklist.

Forma is an experimental conversational CAD workspace. The goal is to let a user describe a part or assembly, inspect the generated result, download the CAD files, and ask for changes in the same conversation. It is a copilot for exploration, not a replacement for engineering review.

## Request flow

```text
Chat request
  → engineering triage (clarify if needed)
  → optional calculation and user approval
  → CAD source generation
  → isolated build and bounded repair
  → artifact publication
  → preview, files and follow-up edits
```

The Next.js frontend handles chat and inspection. A Python FastAPI service owns authentication, projects and artifacts. LangGraph stores agent state and checkpoints in Supabase Postgres; Vercel Workflow advances cloud work. Generated CadQuery code runs in Vercel Sandbox, not in the API process. Supabase stores private files, and LangSmith records server-side development traces. [The README](../README.md) explains the service boundaries and how to try the hosted app.

## What to expect when testing

The live app requires an account. The public reviewer account and its bounded usage are documented in the [README](../README.md); there is no self-signup flow. The account is shared, so its projects are not private. Start with a simple part, inspect the preview and exported STEP/GLB, and ask for an edit.

Forma can build and export CAD geometry, but a successful build does not prove every dimensional, assembly, motion, tolerance, strength or manufacturing requirement. In particular, earlier complex-assembly tests exposed interference and clearance errors even when components were correctly positioned. Engineering calculations are preliminary screens; dynamic performance, fatigue, sealing, and real-world safety require independent analysis and testing. If a check cannot be performed deterministically, it should be treated as unverified.

The project is still being developed. Model availability, cloud quotas and the shared demo allowance can interrupt a run. Published outputs are drafts for human review, and the downloadable source and geometry let a reviewer inspect or revise them outside Forma.
