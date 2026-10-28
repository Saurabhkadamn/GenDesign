"""Owner-visible downloads from committed build receipts, before publication."""

from fastapi import HTTPException

from .. import db
from ..config import settings


async def latest_validation(run_id):
    rows = await db.rest(
        "research_jobs",
        params={
            "run_id": f"eq.{run_id}",
            "status": "eq.complete",
            "result->validation->identity->>candidate": "not.is.null",
            "select": "validation:result->validation,completed_at",
            "order": "completed_at.desc,job_key.desc",
            "limit": 1,
        },
    )
    return rows[0] if rows else None


async def view(run):
    if run.get("harness_version", 3) < 4:
        return None
    row = await latest_validation(run["id"])
    if not row:
        return None
    validation = row["validation"]
    artifacts = validation.get("artifacts") or []
    if not artifacts:
        return None
    candidate = await db.one("run_candidates", {"run_id": f"eq.{run['id']}"}, required=False)
    # The visible tree must come from the source that produced these files,
    # even when a later edit has already changed the working candidate.
    built = await db.one(
        "candidate_versions",
        {
            "run_id": f"eq.{run['id']}",
            "candidate_hash": f"eq.{validation['identity']['candidate']}",
            "select": "manifest:snapshot->manifest",
            "order": "version.desc",
        },
        required=False,
    )
    if not built:
        return None
    from ..acceptance_plan import current_plan
    from ..execution import identity

    plan = await current_plan(run["project_id"])
    requirements = [r["requirement"] for r in plan.get("requirements", [])]
    current = bool(
        candidate and validation["identity"] == identity(candidate["snapshot"], requirements)
    )
    return {
        "runId": run["id"],
        "candidateHash": validation["identity"]["candidate"],
        "current": current,
        "savedAt": row["completed_at"],
        "acceptanceState": "draft",
        "manifest": built["manifest"],
        "artifacts": [
            {
                key: a[key]
                for key in ("name", "kind", "bytes", "componentId", "contentHash")
                if key in a
            }
            for a in artifacts
        ],
        "validation": validation["report"],
    }


async def signed_download(run, name, candidate_hash):
    row = await latest_validation(run["id"])
    validation = row["validation"] if row else {}
    if validation.get("identity", {}).get("candidate") != candidate_hash:
        raise HTTPException(409, "The draft changed. Refresh the available files.")
    matches = [a for a in validation.get("artifacts", []) if a["name"] == name]
    prefix = f"{run['owner_id']}/{run['project_id']}/{run['id']}/{candidate_hash}/"
    if len(matches) != 1 or not matches[0].get("storagePath", "").startswith(prefix):
        raise HTTPException(404, "This file is not part of the saved draft.")
    artifact = matches[0]
    staged = await db.one(
        "artifact_staging",
        {"storage_path": f"eq.{artifact['storagePath']}", "run_id": f"eq.{run['id']}"},
        required=False,
    )
    if not staged or staged["bytes"] != artifact["bytes"]:
        raise HTTPException(409, "The saved draft file is not available.")
    result = await db.storage(
        "object/sign/cad-private/" + db.object_path(artifact["storagePath"]),
        "POST",
        body={"expiresIn": 120},
    )
    url = result.get("signedURL", result.get("signedUrl"))
    return {
        "url": url if url.startswith("https://") else settings().supabase_url + "/storage/v1" + url
    }
