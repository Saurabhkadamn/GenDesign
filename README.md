# Forma

**[Open the live app](https://forma-cad-eosin.vercel.app/)** · [Project architecture and current limitations](docs/CLOUD_VERIFICATION.md)

Forma is an experimental CAD copilot. You describe a part or assembly in chat; it can ask for missing information, perform a preliminary engineering calculation when needed, generate Python CAD source, build it in an isolated sandbox, and return a 3D preview with downloadable design files. You can continue the conversation to request edits. The engineer remains responsible for reviewing dimensions, fit, strength, and manufacturability before using a design.

## Try the hosted app

This is a shared testing workspace: other visitors can see its projects, so **do not enter private designs or personal information**. The demo is limited to six new design runs in a rolling 24-hour period and three follow-ups per run to bound cloud/model usage.

1. Open the [live Forma workspace](https://forma-cad-eosin.vercel.app/) and sign in with the reviewer account below.
2. Create a project and ask for a simple part, for example: “Create an 80 × 50 × 6 mm mounting plate with four Ø6 mm through-holes at X = ±30 mm and Y = ±15 mm, and R3 outer corners.”
3. Watch the run activity, inspect the 3D preview and files, then request an edit in the same chat. Download the STEP or GLB file if the run publishes one.

**Reviewer account:** Email: `forma.public.demo@example.com` · Password: `ngsa0Jr-R3zLhVyqh-pYZLR1`. There is no self-signup flow.

If the shared run allowance has been used, try later or [contact the maintainer](https://github.com/Saurabhkadamn) for individual access. A healthy website does not guarantee that an AI provider or CAD build will succeed for every request.

## How Forma is built

| Layer | Responsibility |
| --- | --- |
| `apps/web` | Next.js chat, project workspace, preview, files, downloads and Web Analytics. |
| `apps/api` | Python FastAPI routes, accounts, projects, model settings, run admission and artifacts. |
| LangGraph | Engineering triage, optional calculation/approval, CAD generation, repair and publication state. Checkpoints are stored in Supabase Postgres. |
| Vercel Workflow | Advances graph work after the HTTP request finishes; human pauses resume through the API. |
| Vercel Sandbox | Runs generated CadQuery code away from the API process and exports STEP/GLB. |
| Supabase | Authentication, project and run records, private artifacts and graph checkpoints. |
| LangSmith | Server-side traces for diagnosing model and graph behavior. |

The frontend never receives model provider keys, database secrets or sandbox credentials. Model connections are configured by an administrator. The model can produce plausible geometry that still misses a requested constraint; the UI and downloadable files are **draft engineering outputs**, not certification of safety or manufacture. See the [architecture and limitations page](docs/CLOUD_VERIFICATION.md) for the current verification boundary.

## Development

The repository is a Vercel Services project: `/api/*` routes to Python and the remaining paths route to Next.js. Local Docker is not required. Local development still needs access to the configured Supabase project and hosted CAD sandbox.

```sh
npm ci
npm run typecheck
npm test
npm run lint
uv sync --project apps/api
uv run --project apps/api pytest apps/api/tests
```

Browser contracts are generated from FastAPI's OpenAPI schema with `npm run contracts`. Never commit local `.env` files or service credentials.
