# Forma CAD

Working product name: Forma. A private, chat-operated engineering workspace.

## Product boundaries

- Geometry changes happen only through chat. Viewer controls select, hide, isolate, and navigate.
- The editable source is an immutable snapshot of Python files, parameters, and a manifest.
- CadQuery/OCP generate real geometry. Display meshes are derived artifacts, never measurement authority.
- Coordinator, CAD, and engineering roles have versioned instructions. No hardcoded model or silent fallback.
- One active run globally; durable steps persist checkpoints. Failed candidates never replace the last good revision.
- No billing, public signup, imported designs, integrated FEA/CFD, or manufacturing claims.

## Structure

`apps/web`: Next.js routes, server authorization, durable orchestration, and the browser workspace.
`packages/core`: provider-independent contracts, invariants, and prompts.
`packages/adapters`: Supabase, OpenRouter, authenticated encryption, and Vercel Sandbox adapters.
`runtimes/python`: CAD build/export, independent geometry verification, calculations.
`supabase/migrations`: least-privilege schema, ownership policies, and atomic publication.

## Design

Warm milk-white canvas, quiet sage accents, Geist typography, soft borders, three workspace panels.
Explicit unconfigured and empty states; never simulate a successful AI build or substitute demo geometry.

## Release gate

Build/typecheck, domain and security tests, real Python CAD fixtures, browser verification, then a live configured
OpenRouter → Sandbox → validation → Storage → preview session. A passing local build alone is not release readiness.
