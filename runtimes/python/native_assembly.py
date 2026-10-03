"""Bounded native solve and independent joint equations; no generated imports."""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

import numpy as np
from scipy.spatial.transform import Rotation

ENGINE_COMMIT = "4be80eef02a3486cda0d78f3ccbb308d207a9639"
KINDS = {"fixed", "revolute", "slider", "spherical", "cylindrical"}
POSITION_TOLERANCE_MM = 1e-4
ANGLE_TOLERANCE_RAD = 1e-7


def matrix(pose):
    result = np.eye(4)
    p, r = np.asarray(pose["position"], dtype=float), np.asarray(pose["rotation"], dtype=float)
    if p.shape != (3,) or r.shape != (3, 3) or not np.isfinite(p).all() or not np.isfinite(r).all():
        raise ValueError("Non-finite or malformed native pose")
    if np.max(np.abs(p)) > 1e7:
        raise ValueError("Unsupported native pose extent")
    if not np.allclose(r.T @ r, np.eye(3), atol=1e-9, rtol=0) or abs(np.linalg.det(r) - 1) > 1e-9:
        raise ValueError("Native pose does not contain a proper rotation")
    result[:3, :3], result[:3, 3] = r, p
    return result


def residual_vector(request, poses, *, length_scale=1.0, time=None):
    values = []
    for joint in request["joints"]:
        a = matrix(poses[joint["a"]["occurrence"]]) @ matrix(joint["a"]["frame"])
        b = matrix(poses[joint["b"]["occurrence"]]) @ matrix(joint["b"]["frame"])
        local = np.linalg.inv(a) @ b
        delta = local[:3, 3] / length_scale
        relative = local[:3, :3]
        kind = joint["kind"]
        if kind in {"fixed", "revolute", "spherical"}:
            values.extend(delta)
        elif kind in {"slider", "cylindrical"}:
            values.extend(delta[:2])
        if kind in {"fixed", "slider"}:
            values.extend(Rotation.from_matrix(relative).as_rotvec())
        elif kind in {"revolute", "cylindrical"}:
            # Same directed Z axis, with the X/Y twist free.
            values.extend(relative[:2, 2])
            values.append(relative[2, 2] - 1)
        motion = request.get("motion")
        if time is not None and motion and joint["id"] == motion["joint"]:
            expected = motion["start"] + (motion["end"] - motion["start"]) * time / motion["duration"]
            if kind == "revolute":
                actual = math.atan2(relative[1, 0], relative[0, 0])
                values.append(math.atan2(math.sin(actual - expected), math.cos(actual - expected)))
            else:
                values.append((local[2, 3] - expected) / length_scale)
    return np.array(values)


def degrees_of_freedom(request, poses):
    movable = [p["id"] for p in request["parts"] if not p["grounded"]]
    variables = len(movable) * 6
    if not variables:
        return 0
    if variables > 600:
        raise ValueError("Native DOF qualification currently supports at most 100 movable occurrences")
    scale = max(1.0, max(np.linalg.norm(matrix(p)[:3, 3]) for p in poses.values()))
    base = residual_vector(request, poses, length_scale=scale)
    jacobian = np.empty((len(base), variables))
    epsilon = 1e-6
    for column in range(variables):
        iid, axis = movable[column // 6], column % 6
        modified = copy.deepcopy(poses)
        if axis < 3:
            modified[iid]["position"][axis] += epsilon * scale
        else:
            delta = np.zeros(3)
            delta[axis - 3] = epsilon
            modified[iid]["rotation"] = (Rotation.from_rotvec(delta).as_matrix() @
                                          np.array(modified[iid]["rotation"])).tolist()
        jacobian[:, column] = (residual_vector(request, modified, length_scale=scale) - base) / epsilon
    singular = np.linalg.svd(jacobian, compute_uv=False)
    threshold = max(1e-5, float(singular[0]) * 1e-6) if singular.size else 1e-5
    return variables - int(sum(singular > threshold))


def validate_solution(request, solution):
    if solution.get("protocol") != 1 or solution.get("commit") != ENGINE_COMMIT:
        raise ValueError("Native engine identity mismatch")
    frames = solution.get("frames", [])
    parts = {p["id"]: p for p in request["parts"]}
    if not frames or len(frames) > 245:
        raise ValueError("Native engine produced no bounded solved-frame evidence")
    expected_times = [0.0]
    motion = request.get("motion")
    if motion:
        expected_times = np.linspace(0, motion["duration"], motion["steps"] + 1)
    if len(frames) != len(expected_times) or not np.allclose(
        [f["time"] for f in frames], expected_times, rtol=0, atol=1e-8
    ):
        raise ValueError("Native mechanism stopped early or returned an unexpected sample sequence")
    max_position = max_angle = 0.0
    for frame in frames:
        poses = frame["poses"]
        if set(poses) != set(parts):
            raise ValueError("Native occurrence inventory changed")
        for iid, original in parts.items():
            accepted = matrix(poses[iid])
            if original["grounded"] and not np.allclose(accepted, matrix(original["pose"]), atol=1e-8, rtol=0):
                raise ValueError("Native solver moved a grounded occurrence")
        for joint in request["joints"]:
            if joint["kind"] not in KINDS:
                raise ValueError("Unsupported native joint")
            only = {**request, "joints": [joint], "motion": None}
            residual = residual_vector(only, poses)
            count = 3 if joint["kind"] in {"fixed", "revolute", "spherical"} else 2
            position = float(np.linalg.norm(residual[:count]))
            angle = float(np.linalg.norm(residual[count:]))
            max_position, max_angle = max(max_position, position), max(max_angle, angle)
            if position > POSITION_TOLERANCE_MM or angle > ANGLE_TOLERANCE_RAD:
                raise ValueError(f"Joint {joint['id']} failed independent residual check: {position:.6g} mm, {angle:.6g} rad")
        if motion:
            driven = next(j for j in request["joints"] if j["id"] == motion["joint"])
            error = residual_vector({**request, "joints": [driven]}, poses, time=frame["time"])[-1]
            tolerance = ANGLE_TOLERANCE_RAD if driven["kind"] == "revolute" else POSITION_TOLERANCE_MM
            if abs(error) > tolerance:
                raise ValueError("Native motion driver failed independent measurement")
    dof = degrees_of_freedom(request, frames[0]["poses"])
    allowed = request.get("allowedDof", 0)
    if dof != allowed:
        raise ValueError(f"Assembly has {dof} measured DOF; explicitly declared allowance is {allowed}")
    if motion and dof != 1:
        raise ValueError("A single-driver mechanism must have exactly one qualified DOF")
    return {"engine": "OndselSolver", "commit": ENGINE_COMMIT, "degreesOfFreedom": dof,
            "maxPositionResidualMm": max_position, "maxAngularResidualRad": max_angle,
            "solvedFrames": len(frames), "checkedJointFrames": len(frames) * len(request["joints"])}


def solve(request, output: Path, *, executable: Path | None = None, timeout=60):
    binary = executable or Path(__file__).resolve().parent / "native" / "forma-assembly"
    if not binary.is_file():
        raise ValueError("Qualified native assembly runtime is not installed")
    # Check marker and initial rotation matrices before entering C++.
    for part in request["parts"]:
        matrix(part["pose"])
    for joint in request["joints"]:
        if joint["kind"] not in KINDS:
            raise ValueError(f"Unsupported native joint: {joint['kind']}")
        for endpoint in (joint["a"], joint["b"]):
            matrix(endpoint["frame"])
    motion = request.get("motion")
    if motion:
        driven = next((j for j in request["joints"] if j["id"] == motion["joint"]), None)
        if not driven or driven["kind"] not in {"revolute", "slider"}:
            raise ValueError("Motion requires a qualified revolute or slider joint")
        if not 2 <= motion["steps"] <= 240 or not 0 < motion["duration"] <= 60:
            raise ValueError("Invalid motion sampling request")
        if driven["kind"] == "revolute" and abs(motion["end"] - motion["start"]) / motion["steps"] >= 1.5:
            raise ValueError("Increase motion samples to avoid ambiguous angular winding")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="native-", dir=output) as folder:
        folder = Path(folder)
        input_path, result_path, log_path = folder / "request.json", folder / "solution.json", folder / "native.log"
        input_path.write_text(json.dumps(request, allow_nan=False), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k.upper() in
               {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "HOME"}}
        with log_path.open("wb") as log:
            try:
                result = subprocess.run([str(binary), str(input_path), str(result_path)], cwd=folder,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env, timeout=min(timeout, 120),
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            except subprocess.TimeoutExpired:
                raise ValueError("Native assembly solve timed out; candidate was not accepted") from None
        if result.returncode or not result_path.is_file():
            with log_path.open("rb") as log:
                log.seek(max(0, log_path.stat().st_size - 2000))
                diagnostic = log.read().decode("utf-8", errors="replace")
            raise ValueError(f"Native assembly worker failed ({result.returncode}): {diagnostic}")
        if result_path.stat().st_size > 40 * 1024 * 1024:
            raise ValueError("Native solution exceeded evidence size limit")
        solution = json.loads(result_path.read_text(encoding="utf-8"))
    solution["validation"] = validate_solution(request, solution)
    return solution
