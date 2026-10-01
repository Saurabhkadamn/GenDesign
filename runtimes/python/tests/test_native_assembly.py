import copy
import json
import os
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from native_assembly import ENGINE_COMMIT, solve, validate_solution


def pose(position=(0, 0, 0)):
    return {"position": np.asarray(position, dtype=float).tolist(), "rotation": np.eye(3).tolist()}


def pair(kind="fixed", allowed=0):
    return {"protocol": 1, "allowedDof": allowed, "parts": [
        {"id": "base", "grounded": True, "pose": pose()},
        {"id": "lid", "grounded": False, "pose": pose((8, 3, 30))}],
        "joints": [{"id": "join", "kind": kind,
            "a": {"occurrence": "base", "frame": pose((0, 0, 5))},
            "b": {"occurrence": "lid", "frame": pose((0, 0, -5))}}], "motion": None}


@pytest.fixture
def binary():
    configured = os.getenv("FORMA_NATIVE_TEST_BINARY")
    if not configured:
        pytest.skip("Native qualification requires FORMA_NATIVE_TEST_BINARY; release checks must set it")
    path = Path(configured)
    assert path.is_file(), "Configured native test binary is missing"
    return path


@pytest.mark.parametrize("kind,allowed", [("fixed", 0), ("revolute", 1), ("slider", 1),
                                           ("spherical", 3), ("cylindrical", 2)])
def test_native_supported_joint(kind, allowed, binary, tmp_path):
    result = solve(pair(kind, allowed), tmp_path, executable=binary)
    assert result["validation"]["degreesOfFreedom"] == allowed
    assert result["validation"]["maxPositionResidualMm"] < 1e-4
    assert result["validation"]["solvedFrames"] == 1


def test_native_rejects_contradictory_grounded_fixed_mate(binary, tmp_path):
    request = pair()
    request["parts"][1]["grounded"] = True
    with pytest.raises(ValueError, match="residual|worker failed|grounded occurrence"):
        solve(request, tmp_path, executable=binary)


def test_native_rejects_undeclared_hinge_freedom(binary, tmp_path):
    with pytest.raises(ValueError, match="DOF"):
        solve(pair("revolute", 0), tmp_path, executable=binary)


@pytest.mark.parametrize("kind,start,end", [("revolute", 0, 2 * np.pi), ("slider", 0, 40)])
def test_native_numeric_driver(kind, start, end, binary, tmp_path):
    request = pair(kind, 1)
    request["motion"] = {"joint": "join", "start": start, "end": end, "duration": 1, "steps": 20}
    result = solve(request, tmp_path, executable=binary)
    assert result["validation"]["solvedFrames"] == 21


def test_native_sixty_occurrence_fixed_assembly(binary, tmp_path):
    request = {"protocol": 1, "allowedDof": 0, "parts": [
        {"id": "base", "grounded": True, "pose": pose()}], "joints": [], "motion": None}
    for i in range(59):
        iid = f"part_{i}"
        location = [(i % 10) * 20, (i // 10) * 20, 10]
        request["parts"].append({"id": iid, "grounded": False,
                                 "pose": pose(np.array(location) + [1, 2, 3])})
        request["joints"].append({"id": f"mate_{i}", "kind": "fixed",
            "a": {"occurrence": "base", "frame": pose(location)},
            "b": {"occurrence": iid, "frame": pose()}})
    result = solve(request, tmp_path, executable=binary, timeout=90)
    assert len(result["frames"][0]["poses"]) == 60
    assert result["validation"]["degreesOfFreedom"] == 0


def test_native_fourbar_closed_loop(binary, tmp_path):
    fixture = Path(__file__).parents[3] / "fixtures/native-fourbar.json"
    request = json.loads(fixture.read_text(encoding="utf-8"))
    result = solve(request, tmp_path, executable=binary)
    assert result["validation"]["solvedFrames"] == 101
    assert result["validation"]["degreesOfFreedom"] == 1


def test_independent_validator_rejects_engine_success_with_bad_poses():
    request = pair()
    result = {"protocol": 1, "commit": ENGINE_COMMIT,
              "frames": [{"time": 0, "poses": {p["id"]: p["pose"] for p in request["parts"]}}]}
    with pytest.raises(ValueError, match="residual"):
        validate_solution(request, result)


def test_independent_validator_rejects_partial_motion():
    request = pair("revolute", 1)
    request["motion"] = {"joint": "join", "start": 0, "end": 1, "duration": 1, "steps": 20}
    result = {"protocol": 1, "commit": ENGINE_COMMIT, "frames": [{"time": 0, "poses": {}}]}
    with pytest.raises(ValueError, match="stopped early"):
        validate_solution(request, result)


def test_independent_validator_rejects_reflected_rotation():
    request = pair()
    bad = copy.deepcopy(request["parts"][0]["pose"])
    bad["rotation"][0][0] = -1
    result = {"protocol": 1, "commit": ENGINE_COMMIT,
              "frames": [{"time": 0, "poses": {"base": bad, "lid": pose((0, 0, 10))}}]}
    with pytest.raises(ValueError, match="proper rotation"):
        validate_solution(request, result)
