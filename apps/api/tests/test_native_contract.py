import copy
import pytest
from forma_api.contracts import Snapshot, ValidationReport


def candidate():
    frame = {"position": [0, 0, 0], "rotation": [0, 0, 0]}
    return {"files": {"parts/plate.py": "def build(ctx): pass", "assemblies/root.py": "def build(ctx): pass"},
        "manifest": {"rootComponentId": "root", "components": [
            {"id": "plate", "name": "Plate", "source": "parts/plate.py", "kind": "solid"},
            {"id": "root", "name": "Root", "source": "assemblies/root.py", "kind": "assembly", "dependencies": ["plate"]}],
            "instances": [{"id": iid, "definitionId": "plate", "name": iid, "frame": copy.deepcopy(frame)} for iid in ["base", "lid"]],
            "references": [{"id": "mate", "componentId": "plate", "kind": "datum", "frame": frame}],
            "joints": [{"id": "join", "kind": "fixed", "referenceA": "mate", "referenceB": "mate", "occurrenceA": "base", "occurrenceB": "lid"}],
            "nativeAssembly": {"groundedInstances": ["base"], "allowedDof": 0}}}


def test_repeated_definition_has_occurrence_specific_native_endpoints():
    accepted = Snapshot.model_validate(candidate())
    assert accepted.manifest.joints[0].occurrenceB == "lid"


@pytest.mark.parametrize("change,message", [
    ("missing_endpoint", "occurrence-specific"), ("missing_frame", "reference frame"),
    ("self_joint", "itself"), ("invalid_ground", "physical leaf"),
    ("duplicate_ground", "repeats"), ("limit", "limits have not"),
    ("unsupported", "not been qualified"), ("winding", "angular winding")])
def test_unqualified_native_contract_rejected(change, message):
    source = candidate()
    manifest = source["manifest"]
    joint = manifest["joints"][0]
    native = manifest["nativeAssembly"]
    if change == "missing_endpoint": joint.pop("occurrenceB")
    if change == "missing_frame": manifest["references"][0].pop("frame")
    if change == "self_joint": joint["occurrenceB"] = "base"
    if change == "invalid_ground": native["groundedInstances"] = ["root"]
    if change == "duplicate_ground": native["groundedInstances"] *= 2
    if change == "limit": joint["lowerLimit"] = 0
    if change == "unsupported": joint["kind"] = "gear"
    if change == "winding":
        joint["kind"] = "revolute"
        native.update(allowedDof=1, motion={"jointId": "join", "start": 0, "end": 7, "steps": 2})
    with pytest.raises(ValueError, match=message):
        Snapshot.model_validate(source)


def test_revision_response_preserves_engineering_evidence():
    report = ValidationReport(identity={}, requirements=[], allRequirementsVerified=False,
        bom={"flat": [{"quantity": 60}]}, nativeAssembly={"degreesOfFreedom": 0},
        assemblyPlacement={"solidChecks": 60})
    assert report.model_dump()["bom"]["flat"][0]["quantity"] == 60


def test_legacy_advisory_joints_remain_backward_compatible():
    source = candidate()
    source["manifest"].pop("nativeAssembly")
    source["manifest"]["joints"][0].pop("occurrenceB")
    assert Snapshot.model_validate(source).manifest.nativeAssembly is None


@pytest.mark.asyncio
async def test_parameter_edit_preserves_omitted_assembly_fields():
    from forma_api import engine
    from forma_api.contracts import AppSettings

    original = Snapshot.model_validate(candidate()).model_dump()
    components = copy.deepcopy(original["manifest"]["components"])
    components[0]["parameters"]["thickness"] = 3
    cp = {"snapshot": original, "role": "cad"}
    await engine.execute_tool({}, cp, {"id": "edit", "name": "apply_changes", "input": {
        "files": {}, "manifest": {"components": components}}}, AppSettings(), "worker")
    changed = cp["snapshot"]["manifest"]
    assert changed["components"][0]["parameters"]["thickness"] == 3
    for key in ["rootComponentId", "nativeAssembly", "instances", "references", "joints"]:
        assert changed[key] == original["manifest"][key]


@pytest.mark.asyncio
async def test_explicit_native_removal_is_distinct_from_omission():
    from forma_api import engine
    from forma_api.contracts import AppSettings

    original = Snapshot.model_validate(candidate()).model_dump()
    cp = {"snapshot": original, "role": "cad"}
    await engine.execute_tool({}, cp, {"id": "remove", "name": "apply_changes", "input": {
        "files": {}, "manifest": {"nativeAssembly": None}}}, AppSettings(), "worker")
    assert cp["snapshot"]["manifest"]["nativeAssembly"] is None
    assert cp["snapshot"]["manifest"]["joints"] == original["manifest"]["joints"]


@pytest.mark.asyncio
async def test_explicit_array_removal_still_checks_retained_constraints_atomically():
    from forma_api import engine
    from forma_api.contracts import AppSettings

    original = Snapshot.model_validate(candidate()).model_dump()
    cp = {"snapshot": original, "role": "cad"}
    with pytest.raises(ValueError, match="physical occurrences"):
        await engine.execute_tool({}, cp, {"id": "remove", "name": "apply_changes", "input": {
            "files": {}, "manifest": {"instances": []}}}, AppSettings(), "worker")
    assert cp["snapshot"] == original


@pytest.mark.asyncio
async def test_named_parameter_delta_preserves_native_assembly_and_requires_rebuild():
    from forma_api import engine
    from forma_api.contracts import AppSettings

    source = candidate()
    source["manifest"]["components"][0]["parameters"] = {"width": 8, "thickness": 4}
    original = Snapshot.model_validate(source).model_dump()
    cp = {"snapshot": copy.deepcopy(original), "role": "cad", "validated": {"old": True}}
    await engine.execute_tool({}, cp, {"id": "edit", "name": "update_parameters", "input": {
        "changes": [{"componentId": "plate", "parameter": "thickness", "value": 3}]}}, AppSettings(), "worker")
    expected = copy.deepcopy(original)
    expected["manifest"]["components"][0]["parameters"]["thickness"] = 3
    assert cp["snapshot"] == expected
    assert cp["changed"] is True and "validated" not in cp
    cp["role"] = "coordinator"
    with pytest.raises(ValueError, match="independently validated"):
        await engine.execute_tool({}, cp, {"id": "publish", "name": "publish_revision",
            "input": {"summary": "Parameter edit without rebuilding"}}, AppSettings(), "worker")


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [
    {"componentId": "missing", "parameter": "thickness", "value": 3},
    {"componentId": "plate", "parameter": "misspelled", "value": 3},
    {"componentId": "plate", "parameter": "thickness", "value": 5},
    {"componentId": "plate", "parameter": "width", "value": float("nan")},
])
async def test_invalid_parameter_batch_keeps_original_workspace_and_evidence(invalid):
    from forma_api import engine
    from forma_api.contracts import AppSettings

    source = candidate()
    source["manifest"]["components"][0]["parameters"] = {"width": 8, "thickness": 4}
    original = Snapshot.model_validate(source).model_dump()
    cp = {"snapshot": copy.deepcopy(original), "role": "cad", "validated": {"old": True}}
    before = copy.deepcopy(cp)
    with pytest.raises(ValueError):
        await engine.execute_tool({}, cp, {"id": "edit", "name": "update_parameters", "input": {
            "changes": [{"componentId": "plate", "parameter": "thickness", "value": 3}, invalid]}}, AppSettings(), "worker")
    assert cp == before
