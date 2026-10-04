from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from forma_api import engine
from forma_api.assembly_requirements import check_assembly_preservation, normalize_assembly_requirements
from forma_api.contracts import Requirement, Snapshot
from forma_api.execution import identity
from forma_api.graphs import design


def fixture():
    frame = {"position": [0, 0, 0], "rotation": [0, 0, 0]}
    return Snapshot.model_validate({"files": {"parts/plate.py": "def build(p, d): pass", "assemblies/root.py": "def build(p, d): pass"},
        "manifest": {"rootComponentId": "root", "components": [
            {"id": "plate", "name": "Plate", "source": "parts/plate.py", "kind": "solid", "parameters": {"thickness": 4},
             "partMetadata": {"partNumber": "PLATE", "revision": "A"}},
            {"id": "root", "name": "Assembly", "source": "assemblies/root.py", "kind": "assembly", "dependencies": ["plate"]}],
            "instances": [{"id": iid, "definitionId": "plate", "name": iid, "frame": deepcopy(frame)} for iid in ["base", "lid"]],
            "references": [{"id": "mate", "componentId": "plate", "kind": "datum", "frame": frame}],
            "joints": [{"id": "join", "kind": "fixed", "referenceA": "mate", "referenceB": "mate", "occurrenceA": "base", "occurrenceB": "lid"}],
            "nativeAssembly": {"groundedInstances": ["base"], "allowedDof": 0}}}).model_dump()


def requirement():
    return Requirement(id="preserve", description="Preserve native assembly structure", kind="assembly_preservation").model_dump()


def report(snapshot, requirements):
    return {"identity": identity(snapshot, requirements), "nativeAssembly": {"engine": "OndselSolver", "solvedFrames": 1},
            "requirements": [{"id": "size", "status": "passed", "kind": "dimensions", "evidence": {}},
                             {"id": "preserve", "status": "unverified", "kind": "assembly_preservation", "evidence": {}}]}


def test_parameter_edit_preserves_owned_structure_but_not_geometric_measurements():
    base = fixture()
    snapshot = deepcopy(base)
    snapshot["manifest"]["components"][0]["parameters"]["thickness"] = 3.0
    requirements = [requirement()]
    value = report(snapshot, requirements)
    value["requirements"][0]["status"] = "failed"
    checked = check_assembly_preservation(value, snapshot, requirements, base, "base-revision")
    assert checked["requirements"][1]["status"] == "passed"
    assert checked["requirements"][1]["evidence"]["baseRevisionId"] == "base-revision"
    assert checked["requirements"][0]["status"] == "failed"
    assert not checked["allRequirementsVerified"]
    assert value["requirements"][1]["status"] == "unverified"


@pytest.mark.parametrize("section", ["grounding", "joints", "frames", "references", "metadata", "root"])
def test_structural_changes_fail(section):
    base = fixture()
    snapshot = deepcopy(base)
    m = snapshot["manifest"]
    if section == "grounding": m["nativeAssembly"]["groundedInstances"] = ["lid"]
    if section == "joints": m["joints"][0]["kind"] = "revolute"
    if section == "frames": m["instances"][0]["frame"]["position"] = (1, 0, 0)
    if section == "references": m["references"][0]["frame"]["position"] = (1, 0, 0)
    if section == "metadata": m["components"][0]["partMetadata"]["revision"] = "B"
    if section == "root": m["rootComponentId"] = "plate"
    requirements = [requirement()]
    checked = check_assembly_preservation(report(snapshot, requirements), snapshot, requirements, base, "base-revision")
    assert checked["requirements"][1]["status"] == "failed"
    assert checked["requirements"][1]["evidence"]["changedSections"]


@pytest.mark.parametrize("missing", ["base", "native", "identity"])
def test_missing_or_stale_evidence_never_passes(missing):
    snapshot = fixture()
    requirements = [requirement()]
    value = report(snapshot, requirements)
    base = deepcopy(snapshot)
    if missing == "base": base = None
    if missing == "native": value["nativeAssembly"] = {}
    if missing == "identity": value["identity"]["candidate"] = "stale"
    checked = check_assembly_preservation(value, snapshot, requirements, base, "base-revision")
    assert checked["requirements"][1]["status"] == "unverified"


def test_only_semantically_mistaken_preservation_bindings_are_changed():
    m = fixture()["manifest"]
    items = [Requirement(id="ground", description="Preserve base as grounded reference frame", kind="center", center=[0, 0, 0]).model_dump(),
             Requirement(id="centre", description="Assembly centred at the origin", kind="center", center=[0, 0, 0]).model_dump(),
             Requirement(id="mates", description="Keep existing fixed joints", kind="through_holes", count=0, diameter=4).model_dump()]
    bound = normalize_assembly_requirements(items, m)
    assert [item["kind"] for item in bound] == ["assembly_preservation", "center", "assembly_preservation"]
    assert bound[0]["id"] == items[0]["id"] and bound[0]["description"] == items[0]["description"]
    assert items[0]["kind"] == "center"
    m["nativeAssembly"] = None
    assert normalize_assembly_requirements(items, m) == items


def test_real_geometric_constraints_that_mention_grounding_or_mates_are_not_rebound():
    items = [Requirement(id="centre", description="Keep assembly centered at origin with base grounded", kind="center", center=[0, 0, 0]).model_dump(),
             Requirement(id="holes", description="Preserve through-holes aligned with mates", kind="through_holes", count=0, diameter=4).model_dump()]
    assert normalize_assembly_requirements(items, fixture()["manifest"]) == items


@pytest.mark.asyncio
async def test_saved_bad_binding_requires_fresh_build_without_geometry_or_model_edits(monkeypatch):
    snapshot = fixture()
    before = deepcopy(snapshot)
    observed = {}
    async def load(_rid): return snapshot
    async def event(*args, **kwargs): pass
    async def build(value):
        observed.update(value)
        return {"phase": "validate"}
    monkeypatch.setattr(design.run_service, "load_candidate", load)
    monkeypatch.setattr(design.repo, "event", event)
    monkeypatch.setattr(design, "build", build)
    old = Requirement(id="ground", description="Preserve base grounding", kind="center", center=[0, 0, 0]).model_dump()
    result = await design.cad_session({"run_id": "run", "original_request": "Change thickness", "requirements": [old],
                                       "validation": {"identity": "old"}, "review": {"action": "repair"}})
    assert result["phase"] == "validate"
    assert observed["requirements"][0]["kind"] == "assembly_preservation"
    assert observed["validation"] == {} and observed["review"] == {}
    assert result["review"] == {} and result["review_history"] == []
    assert result["review_fingerprint"] == "" and result["reviewed_candidate_hash"] == ""
    assert snapshot == before


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_project", [False, True])
async def test_build_loads_only_the_owned_project_base_revision(monkeypatch, wrong_project):
    import json
    from forma_api import maintenance
    from forma_api.contracts import AppSettings
    base = fixture()
    snapshot = deepcopy(base)
    snapshot["manifest"]["components"][0]["parameters"]["thickness"] = 3.0
    requirements = [requirement()]
    run = {"id": str(uuid4()), "owner_id": "owner", "project_id": "project", "base_revision_id": "base-revision"}
    observed = []
    value = report(snapshot, requirements)
    value["artifacts"] = []

    class Executor:
        async def stage(self, *_args): pass
        async def create(self, *_args, **_kwargs): pass
        async def execute(self, *_args):
            return {"identity": identity(snapshot, requirements), "exitCode": 0, "clean": True, "timedOut": False}
        async def read(self, _box, path):
            return json.dumps(value).encode() if path == "report.json" else b"step fixture"

    async def noop(*_args, **_kwargs): pass
    async def owned(project, owner): observed.append((project, owner))
    async def one(table, query):
        assert table == "revisions" and query == {"id": "eq.base-revision", "project_id": "eq.project"}
        if wrong_project:
            raise ValueError("Revision is not in the owned project")
        return {"id": "base-revision"}
    async def load(revision):
        observed.append(revision)
        return base
    monkeypatch.setattr(engine, "executor", lambda: Executor())
    monkeypatch.setattr(engine, "ensure_sandbox", noop)
    monkeypatch.setattr(engine, "settings", lambda: SimpleNamespace(executor="test", resource_budgets_enabled=False))
    monkeypatch.setattr(engine.repo, "owned_project", owned)
    monkeypatch.setattr(engine.repo, "load_snapshot", load)
    monkeypatch.setattr(engine.repo, "event", noop)
    monkeypatch.setattr(engine.db, "one", one)
    monkeypatch.setattr(engine.tracing, "record", noop)
    monkeypatch.setattr(maintenance, "release_sandbox", noop)
    cp = {"snapshot": snapshot, "requirements": requirements, "sandbox": "build", "validator": "validation", "attempts": 0}
    if wrong_project:
        with pytest.raises(ValueError, match="owned project"):
            await engine.build_candidate(run, cp, AppSettings().limits, "test")
        assert observed == [("project", "owner")]
        assert "validated" not in cp
    else:
        result = await engine.build_candidate(run, cp, AppSettings().limits, "test")
        assert result["requirements"][1]["status"] == "passed"
        assert observed == [("project", "owner"), "base-revision"]
