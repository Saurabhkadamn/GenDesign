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
