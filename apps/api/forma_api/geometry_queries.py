"""Transport scoped queries to a fresh source-free native worker."""

from copy import deepcopy
import json
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, SafeId
from .execution import digest, executor, identity
from . import db
from .config import settings


class EntitySelector(Contract):
    faceIndex: int | None = Field(default=None, ge=0)
    kind: Literal["plane", "cylinder"] | None = None
    radiusMm: float | None = Field(default=None, gt=0)
    toleranceMm: float = Field(default=1e-5, gt=0, le=0.1)


class ShapeReference(Contract):
    componentId: SafeId | None = None
    instanceId: SafeId | None = None
    entity: EntitySelector | None = None

    @model_validator(mode="after")
    def single_reference(self):
        if bool(self.componentId) == bool(self.instanceId):
            raise ValueError("Select one componentId or instanceId")
        return self


class GeometryQuery(Contract):
    candidateHash: str = Field(min_length=1, max_length=128)
    operation: Literal[
        "inventory", "distance", "angle", "axis_separation", "cylinder_diameter", "interference"
    ]
    a: ShapeReference
    b: ShapeReference | None = None
    configurationId: SafeId = "as_built"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)


async def measure_geometry(run: dict, snapshot: dict, state: dict, query: GeometryQuery):
    from .maintenance import release_sandbox

    expected = identity(snapshot, state.get("requirements") or [])
    validation = state.get("validation") or {}
    if query.candidateHash != expected["candidate"] or validation.get("identity") != expected:
        raise ValueError(
            "Build and independently validate the current candidate before querying its geometry"
        )
    manifest = deepcopy(snapshot["manifest"])
    frames = (validation.get("report", {}).get("inspection") or {}).get("resolvedFrames")
    if manifest.get("nativeAssembly") and not frames:
        raise ValueError("Revalidate with a runtime that records solved geometry placements")
    if frames:
        for item in manifest["instances"]:
            item["frame"] = frames[item["id"]]
    data = {
        "identity.json": json.dumps(expected).encode(),
        "manifest.json": json.dumps(manifest).encode(),
        "query.json": query.model_dump_json(exclude_none=True).encode(),
    }
    prefix = f"{run['owner_id']}/{run['project_id']}/{run['id']}/{expected['candidate']}/"
    for artifact in validation.get("artifacts") or []:
        if artifact["kind"] != "step" or not artifact.get("componentId"):
            continue
        path = artifact["storagePath"]
        if not path.startswith(prefix):
            raise ValueError("Measurement artifact does not belong to this candidate")
        response = await db.client().get(
            settings().supabase_url + "/storage/v1/object/cad-private/" + db.object_path(path),
            headers=db.headers(),
        )
        if not response.is_success or len(response.content) != artifact["bytes"]:
            raise ValueError("Unable to retrieve the verified geometry artifact")
        from hashlib import sha256

        if (
            artifact.get("contentHash")
            and sha256(response.content).hexdigest() != artifact["contentHash"]
        ):
            raise ValueError("Measurement artifact checksum mismatch")
        data[artifact["name"]] = response.content
    for definition in manifest["components"]:
        if definition["id"] + ".step" not in data:
            raise ValueError("Missing independently validated component STEP")
    from .engine import operation

    key = "geometry-query:" + digest({"identity": expected, "query": query.model_dump()})
    name = "forma-query-" + digest({"run": run["id"], "key": key})[:24]

    async def execute():
        await executor().create(name, lifetime=360)
        try:
            await executor().stage(name, data)
            receipt = await executor().execute(name, "measure", 300)
            if receipt.get("identity") != expected or not receipt.get("clean"):
                raise ValueError("Measurement worker identity/cleanup mismatch")
            if receipt.get("exitCode") or receipt.get("timedOut"):
                return {"ok": False, "diagnostic": receipt.get("diagnostic", "Measurement failed")}
            result = json.loads(await executor().read(name, "measurement.json"))
            if result.get("identity") != expected:
                raise ValueError("Measurement result identity mismatch")
            return {"ok": True, "measurement": result}
        finally:
            await release_sandbox(name)

    return await operation(run, key, "measure", execute, idempotent=True)
