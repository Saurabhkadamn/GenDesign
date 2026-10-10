from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from forma_api.contracts import Snapshot
from forma_api.workspace_diagnostics import contract_repair_attempt, workspace_contract_feedback


def rejected_pump():
    return {"components": [
        {"id": "housing", "name": "Housing", "source": "parts/housing.py", "kind": "solid"},
        {"id": "cover", "name": "Cover", "source": "parts/cover.py", "kind": "solid"}],
        "rootComponentId": "pump", "instances": [
            {"id": "housing_inst", "definitionId": "housing", "name": "Housing",
             "frame": {"position": [0, 0, 0], "rotation": [0, 0, 0]}},
            {"id": "cover_inst", "definitionId": "cover", "name": "Cover",
             "parentId": "housing_inst",
             "frame": {"position": [0, 0, 16.03], "rotation": [0, 0, 0]}}],
        "joints": [], "nativeAssembly": {"solver": "ondsel", "allowedDof": 0,
            "groundedInstances": ["housing_inst"],
            "motion": {"jointId": "drive_rotation", "start": 0, "end": .185, "steps": 10}}}


def feedback(manifest, files):
    with pytest.raises(ValidationError) as caught:
        Snapshot.model_validate({"manifest": manifest, "files": files})
    return workspace_contract_feedback(manifest, files, caught.value)


def test_rejected_pump_exposes_related_blockers_without_private_source():
    manifest = rejected_pump()
    original = deepcopy(manifest)
    files = {"parts/housing.py": "PRIVATE_SOURCE_MARKER", "parts/cover.py": "SECRET_SOURCE"}
    result = feedback(manifest, files)
    assert {item["code"] for item in result["issues"]} == {
        "unknown_root", "invalid_ground", "invalid_motion_joint", "motion_dof"}
    assert "PRIVATE_SOURCE_MARKER" not in json.dumps(result)
    assert "SECRET_SOURCE" not in json.dumps(result)
    assert manifest == original
    manifest["components"].append({"id": "pump", "name": "Pump", "kind": "assembly",
                                   "source": "assemblies/pump.py"})
    next_result = feedback(manifest, files)
    assert "missing_source" in {item["code"] for item in next_result["issues"]}
    assert contract_repair_attempt({**result, "stalledAttempts": 2}, next_result) == 1


def test_stalled_repair_stops_but_blocker_resolution_respects_progress():
    result = feedback(rejected_pump(), {"parts/housing.py": "x", "parts/cover.py": "x"})
    assert contract_repair_attempt({}, result) == 1
    assert contract_repair_attempt({**result, "stalledAttempts": 2}, result) == 3
    # Different wording or source content is not resolution of a blocker.
    altered = deepcopy(result)
    altered["message"] = "different wording"
    assert contract_repair_attempt({**result, "stalledAttempts": 2}, altered) == 3


def test_uncovered_contract_error_is_sanitized_and_still_rejected():
    manifest = {"components": [
        {"id": "part", "name": "Part", "source": "parts/part.py", "kind": "solid"},
        {"id": "part", "name": "Duplicate", "source": "parts/part.py", "kind": "solid"}]}
    result = feedback(manifest, {"parts/part.py": "PRIVATE_SOURCE_MARKER"})
    assert result["issues"][0]["code"] == "contract"
    assert "Duplicate component ID" in result["message"]
    assert "PRIVATE_SOURCE_MARKER" not in json.dumps(result)


def test_null_native_root_is_listed_alongside_motion_errors():
    manifest = rejected_pump()
    manifest["rootComponentId"] = None
    manifest["instances"][1]["parentId"] = None
    result = feedback(manifest, {"parts/housing.py": "x", "parts/cover.py": "x"})
    assert {item["code"] for item in result["issues"]} == {
        "native_root", "invalid_motion_joint", "motion_dof"}
    assert result["issues"][0]["path"] == "rootComponentId"
    assert "Leaving the root null is invalid" in result["issues"][0]["repair"]


def test_authoritative_error_is_not_hidden_by_unrelated_known_issues():
    manifest = rejected_pump()
    manifest["components"].append(deepcopy(manifest["components"][0]))
    result = feedback(manifest, {"parts/housing.py": "x", "parts/cover.py": "x"})
    assert "unknown_root" in {item["code"] for item in result["issues"]}
    assert any(item["code"] == "contract" and "Duplicate component ID" in item["message"]
               for item in result["issues"])
