"""Deterministic drawing jobs: owned immutable STEP input, no generated-code build."""

import json
from copy import deepcopy
from uuid import uuid4

from .. import db
from .. import repository as repo
from ..contracts import Snapshot
from ..engine import validate_step_candidate
from ..execution import ExecutionFailure, digest, identity


async def drawing_candidate(run, sheets):
    await repo.owned_project(run["project_id"], run["owner_id"])
    await db.one(
        "revisions",
        {"id": f"eq.{run['base_revision_id']}", "project_id": f"eq.{run['project_id']}"},
    )
    snapshot = await repo.load_snapshot(run["base_revision_id"])
    snapshot["manifest"]["drawings"] = sheets
    return Snapshot.model_validate(snapshot).model_dump()


async def build_drawing_candidate(run, cp, limits):
    await repo.owned_project(run["project_id"], run["owner_id"])
    revision = await db.one(
        "revisions",
        {"id": f"eq.{run['base_revision_id']}", "project_id": f"eq.{run['project_id']}"},
    )
    base = Snapshot.model_validate(await repo.load_snapshot(run["base_revision_id"])).model_dump()
    geometry = deepcopy(cp["snapshot"])
    geometry["manifest"]["drawings"] = base["manifest"]["drawings"]
    if digest(geometry) != digest(base):
        raise ExecutionFailure(
            "A drawing job cannot change the accepted component definitions or source."
        )
    if not (revision.get("validation") or {}).get("identity"):
        raise ExecutionFailure("Drawings require an independently validated base revision.")
    rows = await db.rest(
        "artifacts",
        params={
            "project_id": f"eq.{run['project_id']}",
            "revision_id": f"eq.{revision['id']}",
            "kind": "eq.step",
        },
    )
    inputs = {row["name"]: row for row in rows}
    step_files = {}
    total = 0
    for definition in cp["snapshot"]["manifest"]["components"]:
        name = definition["id"] + ".step"
        artifact = inputs.get(name)
        if not artifact or artifact["component_id"] != definition["id"]:
            raise ExecutionFailure("An accepted component STEP is missing from the base revision.")
        if not artifact["storage_path"].startswith(f"{run['owner_id']}/{run['project_id']}/"):
            raise ExecutionFailure("Accepted artifact ownership mismatch.")
        content = await db.storage_bytes(artifact["storage_path"], limits.maxArtifactBytes)
        if len(content) != artifact["bytes"]:
            raise ExecutionFailure("Accepted artifact size does not match its revision.")
        total += len(content)
        if total > 120 * 1024 * 1024:
            raise ExecutionFailure("Drawing STEP transfer exceeds 120 MB.")
        step_files[name] = content
    cp["attempts"] = cp.get("attempts", 0) + 1
    cp["validator"] = f"forma-drawing-{run['id'].replace('-', '')}-{uuid4().hex[:8]}"
    expected = identity(cp["snapshot"], cp["requirements"])
    metadata = {
        "manifest.json": json.dumps(cp["snapshot"]["manifest"]).encode(),
        "requirements.json": json.dumps(cp["requirements"]).encode(),
        "identity.json": json.dumps(expected).encode(),
    }
    await repo.event(
        run["id"],
        "Generating drawing views and dimensions from the accepted STEP revision.",
        stage="execution",
    )
    return await validate_step_candidate(
        run, cp, cp["snapshot"], metadata, step_files, limits, "drawing:build"
    )
