"""Trusted CAD entrypoint. Execute generated modules only in an isolated, unprivileged VM."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import shutil
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import cadquery as cq
    import trimesh

MAX_BYTES = 40 * 1024 * 1024


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")


def module_at(root: Path, relative: str):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path.suffix != ".py":
        raise ValueError("Invalid module path")
    spec = importlib.util.spec_from_file_location(f"component_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ValueError("Module is not loadable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def shape_of(value: Any) -> cq.Shape:
    import cadquery as cq

    if isinstance(value, cq.Assembly):
        return value.toCompound()
    if isinstance(value, cq.Workplane):
        shapes = [v for v in value.vals() if isinstance(v, cq.Shape)]
        if not shapes:
            raise ValueError("Workplane contains no shape")
        return cq.Compound.makeCompound(shapes) if len(shapes) > 1 else shapes[0]
    if isinstance(value, cq.Shape):
        return value
    raise ValueError("build() must return a Shape, Workplane, or Assembly")


def build(root: Path, output: Path) -> None:
    import cadquery as cq

    manifest = read_json(root / "manifest.json")
    definitions = {c["id"]: c for c in manifest["components"]}
    built: dict[str, Any] = {}
    visiting: set[str] = set()

    def component(cid: str):
        if cid in visiting:
            raise ValueError("Cyclic component dependency")
        if cid not in built:
            visiting.add(cid)
            definition = definitions[cid]
            dependencies = {d: component(d) for d in definition["dependencies"]}
            module = module_at(root, definition["source"])
            value = module.build(dict(definition["parameters"]), dependencies)
            shape = shape_of(value)
            if shape.wrapped.IsNull():
                raise ValueError(f"Empty component {cid}")
            if isinstance(value, cq.Assembly):
                value.export(str(output / f"{cid}.step"), exportType="STEP")
            else:
                cq.exporters.export(shape, str(output / f"{cid}.step"))
            built[cid] = value
            visiting.remove(cid)
        return built[cid]

    for cid in definitions:
        component(cid)


def properties(shape: cq.Shape, kind: str) -> dict[str, Any]:
    if shape.wrapped.IsNull() or not shape.isValid():
        raise ValueError("Invalid B-rep geometry")
    box = shape.BoundingBox()
    bounds = [box.xmin, box.ymin, box.zmin, box.xmax, box.ymax, box.zmax]
    if not all(math.isfinite(n) for n in bounds) or max(abs(n) for n in bounds) > 1e7:
        raise ValueError("Non-finite or unsupported geometry bounds")
    solids = shape.Solids()
    if kind == "solid" and not solids:
        raise ValueError("Expected a closed solid")
    if kind == "solid" and any(s.Volume() <= 0 for s in solids):
        raise ValueError("Non-positive solid volume")
    if not shape.Faces():
        raise ValueError("Geometry must contain faces")
    return {
        "bounds": bounds,
        "dimensions": [box.xlen, box.ylen, box.zlen],
        "volumeMm3": sum(s.Volume() for s in solids),
        "areaMm2": shape.Area(),
        "solids": len(solids),
        "faces": len(shape.Faces()),
        "valid": True,
    }


def mesh_of(shape: cq.Shape, color: str) -> trimesh.Trimesh:
    import trimesh

    vertices, faces = shape.tessellate(0.1, 0.15)
    if len(faces) > 1_000_000:
        raise ValueError("Preview exceeds one million triangles")
    rgba = [int(color[i : i + 2], 16) for i in (1, 3, 5)] + [255]
    mesh = trimesh.Trimesh(
        vertices=[[v.x, v.y, v.z] for v in vertices], faces=faces, process=False
    )
    mesh.visual.face_colors = rgba
    return mesh


def validate(root: Path, output: Path) -> None:
    """This operation runs in a NEW VM with STEP files but no generated source."""
    import cadquery as cq
    import numpy as np
    import trimesh

    manifest = read_json(root / "manifest.json")
    shapes: dict[str, cq.Shape] = {}
    report: dict[str, Any] = {
        "schemaVersion": 1,
        "units": "mm",
        "components": {},
        "artifacts": [],
    }
    scene = trimesh.Scene()
    for definition in manifest["components"]:
        cid = definition["id"]
        path = root / f"{cid}.step"
        if not path.exists() or path.stat().st_size > MAX_BYTES:
            raise ValueError("Missing or oversized STEP artifact")
        shape = cq.importers.importStep(str(path)).val()
        shapes[cid] = shape
        report["components"][cid] = properties(shape, definition["kind"])
        # Preserve STEP assembly hierarchy and labels after independent validation.
        shutil.copyfile(path, output / f"{cid}.step")
        single = trimesh.Scene()
        single.add_geometry(
            mesh_of(shape, definition["color"]), node_name=cid, geom_name=cid
        )
        (output / f"{cid}.glb").write_bytes(single.export(file_type="glb"))
        for kind in ("step", "glb"):
            artifact = output / f"{cid}.{kind}"
            report["artifacts"].append(
                {
                    "name": artifact.name,
                    "kind": kind,
                    "componentId": cid,
                    "bytes": artifact.stat().st_size,
                }
            )
    definitions = {c["id"]: c for c in manifest["components"]}
    instances = {i["id"]: i for i in manifest["instances"]}
    transforms: dict[str, np.ndarray] = {}

    def world_transform(iid: str) -> np.ndarray:
        if iid not in transforms:
            instance = instances[iid]
            frame = instance["frame"]
            transform = trimesh.transformations.euler_matrix(
                *np.radians(frame["rotation"]), axes="sxyz"
            )
            transform[:3, 3] = frame["position"]
            transforms[iid] = (
                world_transform(instance["parentId"]) @ transform
                if instance["parentId"]
                else transform
            )
        return transforms[iid]

    if instances:
        # Non-leaf assembly nodes are groups. Individual parts retain stable instance IDs.
        parents = {i["parentId"] for i in instances.values()}
        for iid, instance in instances.items():
            if iid in parents:
                continue
            definition = definitions[instance["definitionId"]]
            scene.add_geometry(
                mesh_of(shapes[definition["id"]], definition["color"]),
                node_name=iid,
                geom_name=iid,
                transform=world_transform(iid),
            )
    elif manifest["rootComponentId"]:
        cid = manifest["rootComponentId"]
        scene.add_geometry(
            mesh_of(shapes[cid], definitions[cid]["color"]),
            node_name=cid,
            geom_name=cid,
        )
    if not scene.geometry:
        raise ValueError("Define a root component or assembly instances for preview")
    root_id = manifest["rootComponentId"]
    if root_id and instances:
        # Reject manifest placements that disagree with the actual exported assembly extents.
        expected = np.array(report["components"][root_id]["bounds"]).reshape(2, 3)
        if not np.allclose(scene.bounds, expected, atol=0.2, rtol=1e-4):
            raise ValueError(
                "Assembly placements in manifest do not match the root STEP geometry"
            )
    (output / "preview.glb").write_bytes(scene.export(file_type="glb"))
    report["artifacts"].append(
        {
            "name": "preview.glb",
            "kind": "glb",
            "componentId": None,
            "bytes": (output / "preview.glb").stat().st_size,
        }
    )
    if any(a["bytes"] > MAX_BYTES for a in report["artifacts"]):
        raise ValueError("Artifact exceeds 40 MB limit")
    # This file comes from the coordinator through the trusted staging layer.
    # The validator receives no generated Python and reopens the STEP independently.
    if (root / "requirements.json").exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location("forma_requirements", Path(__file__).with_name("requirements_check.py"))
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        report["requirements"] = checker.check_requirements(shapes, manifest, read_json(root / "requirements.json"))
        report["allRequirementsVerified"] = bool(report["requirements"]) and all(c["status"] == "passed" for c in report["requirements"])
    if (root / "identity.json").exists():
        report["identity"] = read_json(root / "identity.json")
    write_json(output / "report.json", report)


def calculate_once(root: Path, path: str, result: Path) -> None:
    value = module_at(root, path).calculate()
    write_json(result, value)


def calculate(root: Path, output: Path, path: str) -> None:
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "MPLBACKEND": "Agg",
        "HOME": str(output.resolve()),
        "USERPROFILE": str(output.resolve()),
        "TEMP": str(output.resolve()),
        "TMP": str(output.resolve()),
    }
    if os.name == "nt" and "SystemRoot" in os.environ:
        environment["SystemRoot"] = os.environ["SystemRoot"]
    results = []
    for i in range(2):
        subprocess.run(
            [
                sys.executable,
                "-I",
                __file__,
                "calculate-once",
                "--root",
                str(root),
                "--output",
                str(output),
                "--path",
                path,
                "--index",
                str(i),
            ],
            check=True,
            timeout=120,
            env=environment,
        )
        results.append(read_json(output / f"calculation-{i}.json"))
    first, second = results
    if first != second:
        raise ValueError("Calculation is not reproducible across clean-process reruns")
    write_json(output / "calculation.json", {"result": first, "reproducible": True})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "operation", choices=["build", "validate", "calculate", "calculate-once"]
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--path", default="")
    parser.add_argument("--index", default="0")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.operation == "build":
        build(args.root, args.output)
    elif args.operation == "validate":
        validate(args.root, args.output)
    elif args.operation == "calculate-once":
        calculate_once(
            args.root, args.path, args.output / f"calculation-{args.index}.json"
        )
    else:
        calculate(args.root, args.output, args.path)


if __name__ == "__main__":
    main()
