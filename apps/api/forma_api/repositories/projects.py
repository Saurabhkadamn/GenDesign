import asyncio
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException

from .. import db
from ..config import settings
from ..contracts import Snapshot
from ..tracing import sanitize


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def identifier(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        raise HTTPException(400, "Invalid identifier.") from None


async def owned_project(project_id: str, owner_id: str):
    return await db.one("projects", {"id": f"eq.{identifier(project_id)}", "owner_id": f"eq.{owner_id}"})


async def owned_run(run_id: str, owner_id: str):
    return await db.one("runs", {"id": f"eq.{identifier(run_id)}", "owner_id": f"eq.{owner_id}"})


async def load_snapshot(revision_id: str | None) -> dict:
    if not revision_id:
        return Snapshot().model_dump()
    row = await db.one("source_snapshots", {"revision_id": f"eq.{revision_id}"})
    return Snapshot.model_validate(row["snapshot"]).model_dump()


async def workspace(project_id: str, owner_id: str):
    project = await owned_project(project_id, owner_id)
    query = {"project_id": f"eq.{project_id}"}
    revisions, messages, runs, artifacts, calculations = await asyncio.gather(
        db.rest("revisions", params={**query, "order": "ordinal.desc", "limit": 100}),
        db.rest("messages", params={**query, "order": "created_at.desc,id.desc", "limit": 150}),
        db.rest("runs", params={**query, "order": "created_at.desc", "limit": 25}),
        db.rest("artifacts", params=query),
        db.rest("calculations", params={**query, "order": "created_at.desc", "limit": 100}),
    )
    events = await db.rest("run_events", params={"run_id": f"in.({','.join(r['id'] for r in runs[:3])})", "order": "id.desc", "limit": 50}) if runs else []
    return {"project": project, "revisions": revisions, "messages": list(reversed(messages)),
            "runs": runs, "artifacts": artifacts, "calculations": calculations, "events": list(reversed(events))}


async def agent_context(project_id: str, owner_id: str, run_id: str,
                        revision_id: str | None, selected_ids: list[str]) -> dict:
    """Build a bounded, owned project memory for a new graph thread.

    A Forma run has its own checkpoint, but a project spans many runs. Fetching
    previous chat and revision evidence here prevents follow-up turns from
    starting as if the project were empty. Source remains in the snapshot,
    rather than being copied into every graph checkpoint.
    """
    project = await owned_project(project_id, owner_id)
    rows, first_rows, revision_rows, run_rows, calculation_rows = await asyncio.gather(
        db.rest("messages", params={"project_id": f"eq.{project_id}",
            "order": "created_at.desc,id.desc", "limit": 60}),
        db.rest("messages", params={"project_id": f"eq.{project_id}",
            "role": "eq.user", "order": "created_at.asc,id.asc", "limit": 1}),
        db.rest("revisions", params={"project_id": f"eq.{project_id}",
            "select": "id,ordinal,summary", "order": "ordinal.desc", "limit": 8}),
        db.rest("runs", params={"project_id": f"eq.{project_id}",
            "select": "id,status,error,created_at", "order": "created_at.desc", "limit": 6}),
        db.rest("calculations", params={"project_id": f"eq.{project_id}",
            "select": "result,reproducible,stale,created_at", "order": "created_at.desc", "limit": 4}),
    )
    previous = [row for row in reversed(rows) if row.get("run_id") != run_id]
    first_brief = next((row for row in first_rows if row.get("run_id") != run_id), None)
    recent = previous[-12:]
    if first_brief and first_brief not in recent:
        recent = [first_brief, *recent]
    revision = (await db.one("revisions", {"id": f"eq.{revision_id}",
        "project_id": f"eq.{project_id}"}, required=False)) if revision_id else None
    prior_runs = [row for row in run_rows if row["id"] != run_id]
    events = (await db.rest("run_events", params={"run_id": f"eq.{prior_runs[0]['id']}",
        "select": "kind,stage,message", "order": "id.desc", "limit": 8})) if prior_runs else []
    validation = revision.get("validation") if revision else None
    report = validation if isinstance(validation, dict) else {}
    measurements = report.get("components") if isinstance(report.get("components"), dict) else {}
    # Inspection can be large. Keep only the latest claims relevant to a
    # conversational answer; the candidate snapshot remains available to tools.
    return sanitize({
        "project": {"id": project_id, "name": project.get("name")},
        "previousMessages": [{"role": row["role"], "content": row["content"][:12000]}
                             for row in recent],
        "revision": ({"id": revision["id"], "summary": revision.get("summary"),
            "manifest": revision.get("manifest"),
            "requirements": report.get("requirements", []),
            "allRequirementsVerified": report.get("allRequirementsVerified", False),
            "componentMeasurements": {cid: {key: facts[key] for key in ("dimensions", "solids", "valid")
                if key in facts} for cid, facts in measurements.items()
                if isinstance(facts, dict)},
            "inspection": {key: report.get("inspection", {}).get(key)
                for key in ("bounds", "components", "configurations")
                if isinstance(report.get("inspection"), dict) and key in report["inspection"]}}
            if revision else None),
        "selectedIds": selected_ids,
        "recentRevisions": revision_rows,
        "recentRuns": prior_runs[:5],
        "latestRunEvents": list(reversed(events)),
        "calculations": calculation_rows,
    })


async def event(run_id: str, message: str, *, kind="status", stage=None, attempt=None, elapsed_ms=None):
    await db.insert("run_events", {"run_id": run_id, "kind": kind, "message": message,
                                  "stage": stage, "attempt": attempt, "elapsed_ms": elapsed_ms})


async def signed_artifact(artifact_id: str, owner_id: str):
    artifact = await db.one("artifacts", {"id": f"eq.{identifier(artifact_id)}"})
    await owned_project(artifact["project_id"], owner_id)
    result = await db.storage(f"object/sign/cad-private/{db.object_path(artifact['storage_path'])}", "POST", body={"expiresIn": 120})
    url = result.get("signedURL", result.get("signedUrl"))
    return {"url": url if url.startswith("https://") else settings().supabase_url + "/storage/v1" + url}
