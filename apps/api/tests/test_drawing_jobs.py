"""Direct drawing jobs keep immutable accepted geometry and bypass model calls."""

from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import HTTPException

from forma_api.contracts import Limits, Snapshot
from forma_api.execution import ExecutionFailure
from forma_api.services import drawings

RUN = {
    "id": str(uuid4()),
    "owner_id": str(uuid4()),
    "project_id": str(uuid4()),
    "base_revision_id": str(uuid4()),
}
BASE = Snapshot.model_validate(
    {
        "files": {"parts/plate.py": 'raise RuntimeError("must never execute")'},
        "manifest": {
            "components": [
                {"id": "plate", "name": "Plate", "source": "parts/plate.py", "kind": "solid"}
            ],
            "rootComponentId": "plate",
        },
    }
).model_dump()


@pytest.fixture
def setup(monkeypatch):
    captured = {}

    async def owned(project, owner):
        assert (project, owner) == (RUN["project_id"], RUN["owner_id"])

    async def one(table, query):
        assert table == "revisions" and query["project_id"] == f"eq.{RUN['project_id']}"
        return {"id": RUN["base_revision_id"], "validation": {"identity": {"candidate": "base"}}}

    async def load(revision):
        assert revision == RUN["base_revision_id"]
        return deepcopy(BASE)

    async def artifacts(table, *, params):
        assert table == "artifacts" and params["revision_id"] == f"eq.{RUN['base_revision_id']}"
        return [
            {
                "name": "plate.step",
                "component_id": "plate",
                "storage_path": f"{RUN['owner_id']}/{RUN['project_id']}/accepted/plate.step",
                "bytes": 4,
            }
        ]

    async def content(path, maximum):
        return b"STEP"

    async def event(*args, **kwargs):
        pass

    async def validate(run, cp, snapshot, metadata, files, limits, key):
        captured.update(cp=cp, snapshot=snapshot, metadata=metadata, files=files, key=key)
        return {"drawings": {"sheets": []}, "identity": {}}

    monkeypatch.setattr(drawings.repo, "owned_project", owned)
    monkeypatch.setattr(drawings.repo, "load_snapshot", load)
    monkeypatch.setattr(drawings.repo, "event", event)
    monkeypatch.setattr(drawings.db, "one", one)
    monkeypatch.setattr(drawings.db, "rest", artifacts)
    monkeypatch.setattr(drawings.db, "storage_bytes", content)
    monkeypatch.setattr(drawings, "validate_step_candidate", validate)
    return captured


@pytest.mark.asyncio
async def test_drawing_uses_accepted_step_and_no_source_in_validator(setup):
    snapshot = await drawings.drawing_candidate(RUN, [{"id": "sheet", "componentId": "plate"}])
    assert snapshot["files"] == BASE["files"]
    cp = {"snapshot": snapshot, "requirements": []}
    await drawings.build_drawing_candidate(RUN, cp, Limits())
    assert setup["files"] == {"plate.step": b"STEP"}
    assert set(setup["metadata"]) == {"manifest.json", "requirements.json", "identity.json"}
    assert cp["attempts"] == 1 and cp["validator"].startswith("forma-drawing-")


@pytest.mark.asyncio
async def test_drawing_candidate_cannot_change_original_geometry_or_source(setup):
    snapshot = deepcopy(BASE)
    snapshot["files"]["parts/plate.py"] = "def build(): pass"
    with pytest.raises(ExecutionFailure, match="cannot change"):
        await drawings.build_drawing_candidate(
            RUN, {"snapshot": snapshot, "requirements": []}, Limits()
        )
    assert not setup


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "owner", "size"])
async def test_invalid_accepted_step_blocks_before_validation(setup, monkeypatch, failure):
    async def artifacts(*args, **kwargs):
        if failure == "missing":
            return []
        return [
            {
                "name": "plate.step",
                "component_id": "plate",
                "storage_path": (
                    "other-owner/plate.step"
                    if failure == "owner"
                    else f"{RUN['owner_id']}/{RUN['project_id']}/plate.step"
                ),
                "bytes": 5 if failure == "size" else 4,
            }
        ]

    monkeypatch.setattr(drawings.db, "rest", artifacts)
    with pytest.raises(ExecutionFailure):
        await drawings.build_drawing_candidate(
            RUN, {"snapshot": deepcopy(BASE), "requirements": []}, Limits()
        )
    assert not setup


@pytest.mark.asyncio
async def test_owner_denial_stops_before_snapshot_load(setup, monkeypatch):
    async def deny(*args):
        raise HTTPException(404, "Project not found")

    monkeypatch.setattr(drawings.repo, "owned_project", deny)
    with pytest.raises(HTTPException):
        await drawings.drawing_candidate(RUN, [{"id": "sheet", "componentId": "plate"}])
    assert not setup


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [None, "missing_sheets", "wrong_identity", "missing_exports", "unresolved"]
)
async def test_drawing_graph_publishes_only_complete_revision_bound_outputs(
    setup, monkeypatch, failure
):
    from forma_api.contracts import AppSettings
    from forma_api.graphs import design

    snapshot = await drawings.drawing_candidate(RUN, [{"id": "sheet", "componentId": "plate"}])
    report = {
        "identity": {"candidate": "validated"},
        "drawings": {
            "identity": {"candidate": "validated"},
            "sheets": [{"id": "sheet", "componentId": "plate", "issues": []}],
        },
        "artifacts": [
            {"name": "drawing-sheet." + ext, "kind": "drawing"} for ext in ("svg", "pdf", "dxf")
        ],
    }
    if failure == "missing_sheets":
        report["drawings"]["sheets"] = []
    if failure == "wrong_identity":
        report["drawings"]["identity"] = {"candidate": "other"}
    if failure == "missing_exports":
        report["artifacts"].pop()
    if failure == "unresolved":
        report["drawings"]["sheets"][0]["issues"] = [
            {"id": "hole", "message": "Referenced geometry is missing"}
        ]

    async def run_row(state):
        return RUN

    async def load(run_id):
        return snapshot

    async def operation(run, key, kind, callback):
        assert kind == "drawing_build"
        return await callback()

    async def generate(run, cp, limits):
        return report

    async def settings():
        return AppSettings()

    monkeypatch.setattr(design, "run_row", run_row)
    monkeypatch.setattr(design.run_service, "load_candidate", load)
    monkeypatch.setattr(design, "operation", operation)
    monkeypatch.setattr(design, "app_settings", settings)
    monkeypatch.setattr(drawings, "build_drawing_candidate", generate)
    result = await design.drawing_build({"run_id": RUN["id"], "requirements": [], "model_calls": 0})
    assert result["phase"] == ("publish" if failure is None else "final")
    if failure:
        assert result["terminal_status"] == "failed"


@pytest.mark.asyncio
async def test_drawing_retry_requires_explicit_continue_and_completed_call_is_reused(monkeypatch):
    from forma_api import engine

    item = {"status": "ambiguous", "result": None}
    calls = []

    async def one(*args, **kwargs):
        return item.copy()

    async def update(table, changes, **kwargs):
        item.update(changes)

    async def rest(*args, **kwargs):
        assert "drawing_build" in kwargs["params"]["kind"]
        return [{**item, "operation_key": "drawing"}]

    async def callback():
        calls.append(1)
        return {"drawings": "complete"}

    monkeypatch.setattr(engine.db, "one", one)
    monkeypatch.setattr(engine.db, "update", update)
    monkeypatch.setattr(engine.db, "rest", rest)
    with pytest.raises(engine.Pause):
        await engine.operation(RUN, "drawing", "drawing_build", callback)
    assert not calls
    await engine.authorize_ambiguous_retry(RUN["id"])
    assert await engine.operation(RUN, "drawing", "drawing_build", callback) == {
        "drawings": "complete"
    }
    assert await engine.operation(RUN, "drawing", "drawing_build", callback) == {
        "drawings": "complete"
    }
    assert calls == [1]
