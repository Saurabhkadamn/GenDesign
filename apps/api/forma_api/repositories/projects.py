import asyncio
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException

from .. import db
from ..config import settings
from ..contracts import Snapshot


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
        db.rest("messages", params={**query, "order": "created_at.desc", "limit": 150}),
        db.rest("runs", params={**query, "order": "created_at.desc", "limit": 25}),
        db.rest("artifacts", params=query),
        db.rest("calculations", params={**query, "order": "created_at.desc", "limit": 100}),
    )
    events = await db.rest("run_events", params={"run_id": f"in.({','.join(r['id'] for r in runs[:3])})", "order": "id.desc", "limit": 50}) if runs else []
    return {"project": project, "revisions": revisions, "messages": list(reversed(messages)),
            "runs": runs, "artifacts": artifacts, "calculations": calculations, "events": list(reversed(events))}


async def event(run_id: str, message: str, *, kind="status", stage=None, attempt=None, elapsed_ms=None):
    await db.insert("run_events", {"run_id": run_id, "kind": kind, "message": message,
                                  "stage": stage, "attempt": attempt, "elapsed_ms": elapsed_ms})


async def signed_artifact(artifact_id: str, owner_id: str):
    artifact = await db.one("artifacts", {"id": f"eq.{identifier(artifact_id)}"})
    await owned_project(artifact["project_id"], owner_id)
    result = await db.storage(f"object/sign/cad-private/{db.object_path(artifact['storage_path'])}", "POST", body={"expiresIn": 120})
    url = result.get("signedURL", result.get("signedUrl"))
    return {"url": url if url.startswith("https://") else settings().supabase_url + "/storage/v1" + url}
