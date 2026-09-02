"""Bounded cleanup after the public run is complete; never delete current artifacts."""
import math
from datetime import datetime, timezone

from . import db
from .contracts import AppSettings
from .execution import executor


async def release_sandbox(name):
    await executor().destroy(name)
    usage = await db.one("resource_usage", {"operation_key": f"eq.{name}"}, required=False)
    if usage:
        elapsed = math.ceil((datetime.now(timezone.utc) - datetime.fromisoformat(usage["created_at"])).total_seconds())
        await db.update("resource_usage", {"reserved_seconds": min(usage["reserved_seconds"], max(1, elapsed))}, operation_key=name)


async def cleanup_run(run_id):
    run = await db.one("runs", {"id": f"eq.{run_id}"})
    if run["status"] in ("queued", "running"):
        return
    private = await db.one("run_private", {"run_id": f"eq.{run_id}"})
    # Cancellation may race sandbox creation. The workflow runs this only after
    # its in-flight step settles, so destroy any environment created after the
    # HTTP cancellation handler's first cleanup attempt.
    if run["status"] == "cancelled":
        for key in ("sandbox", "validator"):
            name = private["checkpoint"].get(key)
            if name:
                try:
                    await release_sandbox(name)
                except Exception:
                    pass  # Already destroyed, or bounded by the sandbox lifetime.
    keep = {item["storagePath"] for item in private["checkpoint"].get("validated", {}).get("artifacts", [])} if run["status"] in ("paused", "waiting_input") else set()
    staged = await db.rest("artifact_staging", params={"run_id": f"eq.{run_id}", "limit": 1000})
    for item in staged:
        path = item["storage_path"]
        published = await db.one("artifacts", {"storage_path": f"eq.{path}"}, required=False)
        if path in keep and not published:
            continue
        if not published:
            await db.storage("object/cad-private", "DELETE", body={"prefixes": [path]})
        await db.rest("artifact_staging", "DELETE", params={"storage_path": f"eq.{path}"})
    config = AppSettings.model_validate((await db.one("app_settings", {"id": "eq.true"}))["settings"])
    project = await db.one("projects", {"id": f"eq.{run['project_id']}"})
    revisions = await db.rest("revisions", params={"project_id": f"eq.{run['project_id']}", "order": "ordinal.desc", "limit": 1000})
    retained = {r["id"] for r in revisions[:config.limits.retainedExports]} | {project["current_revision_id"]}
    artifacts = await db.rest("artifacts", params={"project_id": f"eq.{run['project_id']}", "limit": 1000})
    for artifact in artifacts:
        if artifact["revision_id"] in retained:
            continue
        await db.storage("object/cad-private", "DELETE", body={"prefixes": [artifact["storage_path"]]})
        await db.rest("artifacts", "DELETE", params={"id": f"eq.{artifact['id']}"})
