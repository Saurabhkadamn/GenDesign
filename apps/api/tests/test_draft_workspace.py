from copy import deepcopy

import pytest
from fastapi import HTTPException

from forma_api import acceptance_plan, db
from forma_api.contracts import DraftWorkspace, Snapshot
from forma_api.execution import digest, identity
from forma_api.services import drafts


@pytest.mark.asyncio
async def test_draft_preview_uses_built_manifest_and_labels_newer_edits_stale(monkeypatch):
    built = Snapshot().model_dump()
    current = deepcopy(built)
    current["files"]["parts/newer.py"] = "# New source is not built yet"
    expected = identity(built, [])
    run = {"id": "run", "project_id": "project", "owner_id": "owner", "harness_version": 4}
    path = f"owner/project/run/{digest(built)}/receipt/content/preview.glb"
    validation = {
        "identity": expected,
        "artifacts": [{"name": "preview.glb", "kind": "glb", "bytes": 12, "storagePath": path}],
        "report": {"identity": expected, "requirements": [], "allRequirementsVerified": False},
    }

    async def latest(_run):
        return {"validation": validation, "completed_at": "2026-10-25T10:00:00Z"}

    async def one(table, params, **kwargs):
        if table == "run_candidates":
            return {"snapshot": current}
        if table == "candidate_versions":
            assert params["candidate_hash"] == f"eq.{digest(built)}"
            return {"manifest": built["manifest"]}
        if table == "artifact_staging":
            return {"bytes": 12}
        raise AssertionError(table)

    async def plan(_project):
        return {}

    async def storage(path, method, body):
        assert method == "POST" and body == {"expiresIn": 120}
        return {"signedURL": "https://storage.example.test/owned-draft"}

    monkeypatch.setattr(drafts, "latest_validation", latest)
    monkeypatch.setattr(db, "one", one)
    monkeypatch.setattr(db, "storage", storage)
    monkeypatch.setattr(acceptance_plan, "current_plan", plan)
    view = DraftWorkspace.model_validate(await drafts.view(run))
    assert view.current is False and view.acceptanceState == "draft"
    assert view.manifest.model_dump() == built["manifest"]
    assert "storagePath" not in view.artifacts[0].model_dump()
    assert (await drafts.signed_download(run, "preview.glb", digest(built)))["url"].endswith(
        "owned-draft"
    )
    with pytest.raises(HTTPException) as changed:
        await drafts.signed_download(run, "preview.glb", digest(current))
    assert changed.value.status_code == 409
    with pytest.raises(HTTPException) as missing:
        await drafts.signed_download(run, "invented.step", digest(built))
    assert missing.value.status_code == 404
