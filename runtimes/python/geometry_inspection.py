"""Generic OCCT-backed inspection for built Forma geometry.

The module deliberately reports measurements and topology rather than deciding
which mechanical requirements matter.  CAD and reviewer agents compose those
facts for the current request.
"""

from __future__ import annotations

import math
from typing import Any

import cadquery as cq
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import (
    GeomAbs_BSplineSurface,
    GeomAbs_BezierSurface,
    GeomAbs_Cone,
    GeomAbs_Cylinder,
    GeomAbs_OffsetSurface,
    GeomAbs_OtherSurface,
    GeomAbs_Plane,
    GeomAbs_Sphere,
    GeomAbs_SurfaceOfExtrusion,
    GeomAbs_SurfaceOfRevolution,
    GeomAbs_Torus,
)


SURFACE_NAMES = {
    GeomAbs_Plane: "plane",
    GeomAbs_Cylinder: "cylinder",
    GeomAbs_Cone: "cone",
    GeomAbs_Sphere: "sphere",
    GeomAbs_Torus: "torus",
    GeomAbs_BezierSurface: "bezier",
    GeomAbs_BSplineSurface: "bspline",
    GeomAbs_SurfaceOfRevolution: "revolution",
    GeomAbs_SurfaceOfExtrusion: "extrusion",
    GeomAbs_OffsetSurface: "offset",
    GeomAbs_OtherSurface: "other",
}


def _finite(value: float) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Geometry inspection produced a non-finite value")
    return number


def _bounds(shape: cq.Shape) -> list[float]:
    box = shape.BoundingBox()
    return [_finite(value) for value in (
        box.xmin, box.ymin, box.zmin, box.xmax, box.ymax, box.zmax
    )]


def surface_inventory(shape: cq.Shape) -> dict[str, int]:
    """Count exact OCCT surface types in a shape."""
    result: dict[str, int] = {}
    for face in shape.Faces():
        kind = SURFACE_NAMES.get(BRepAdaptor_Surface(face.wrapped).GetType(), "unknown")
        result[kind] = result.get(kind, 0) + 1
    return dict(sorted(result.items()))


def component_facts(shape: cq.Shape, definition: dict[str, Any]) -> dict[str, Any]:
    solids = shape.Solids()
    volume = _finite(sum(item.Volume() for item in solids))
    material = definition.get("material") or {}
    density = material.get("densityKgM3")
    result = {
        "boundsMm": _bounds(shape),
        "solidCount": len(solids),
        "connectedSolidCount": len(solids),
        "faceCount": len(shape.Faces()),
        "edgeCount": len(shape.Edges()),
        "vertexCount": len(shape.Vertices()),
        "volumeMm3": volume,
        "surfaceTypes": surface_inventory(shape),
        "validBrep": bool(shape.isValid()),
    }
    if density is not None:
        result["material"] = material.get("name", "assigned material")
        result["densityKgM3"] = _finite(density)
        result["massKg"] = _finite(volume * float(density) / 1_000_000_000)
    else:
        result["massKg"] = None
    return result


def _frame_location(frame: dict[str, Any]) -> cq.Location:
    return cq.Location(tuple(frame["position"]), tuple(frame["rotation"]))


def _world_locations(manifest: dict[str, Any], overrides: dict[str, dict[str, Any]]) -> dict[str, cq.Location]:
    instances = {item["id"]: item for item in manifest.get("instances", [])}
    result: dict[str, cq.Location] = {}
    visiting: set[str] = set()

    def locate(instance_id: str) -> cq.Location:
        if instance_id in result:
            return result[instance_id]
        if instance_id in visiting:
            raise ValueError("Cyclic assembly placement during inspection")
        visiting.add(instance_id)
        instance = instances[instance_id]
        local = _frame_location(overrides.get(instance_id, instance["frame"]))
        parent_id = instance.get("parentId")
        world = locate(parent_id) * local if parent_id else local
        visiting.remove(instance_id)
        result[instance_id] = world
        return world

    for identifier in instances:
        locate(identifier)
    return result


def _box_gap(a: list[float], b: list[float]) -> float:
    gaps = [max(0.0, a[index] - b[index + 3], b[index] - a[index + 3]) for index in range(3)]
    return math.sqrt(sum(value * value for value in gaps))


def _boxes_overlap(a: list[float], b: list[float], tolerance: float = 1e-7) -> bool:
    return all(min(a[index + 3], b[index + 3]) - max(a[index], b[index]) > tolerance for index in range(3))


def _combined_bounds(items: list[dict[str, Any]]) -> list[float] | None:
    if not items:
        return None
    return [
        min(item["boundsMm"][0] for item in items),
        min(item["boundsMm"][1] for item in items),
        min(item["boundsMm"][2] for item in items),
        max(item["boundsMm"][3] for item in items),
        max(item["boundsMm"][4] for item in items),
        max(item["boundsMm"][5] for item in items),
    ]


def _configuration_facts(
    shapes: dict[str, cq.Shape], manifest: dict[str, Any], configuration: dict[str, Any]
) -> dict[str, Any]:
    definitions = {item["id"]: item for item in manifest.get("components", [])}
    overrides = {item["instanceId"]: item["frame"] for item in configuration.get("frames", [])}
    locations = _world_locations(manifest, overrides)
    physical: list[dict[str, Any]] = []
    for instance in manifest.get("instances", []):
        definition = definitions[instance["definitionId"]]
        if definition["kind"] == "assembly":
            continue
        moved = shapes[definition["id"]].moved(locations[instance["id"]])
        physical.append({
            "instanceId": instance["id"],
            "definitionId": definition["id"],
            "shape": moved,
            "boundsMm": _bounds(moved),
            "volumeMm3": _finite(sum(solid.Volume() for solid in moved.Solids())),
            "massKg": component_facts(shapes[definition["id"]], definition)["massKg"],
        })

    # A project containing a single part may have no explicit instance list.
    if not physical and manifest.get("rootComponentId"):
        definition = definitions[manifest["rootComponentId"]]
        if definition["kind"] != "assembly":
            shape = shapes[definition["id"]]
            physical.append({
                "instanceId": definition["id"],
                "definitionId": definition["id"],
                "shape": shape,
                "boundsMm": _bounds(shape),
                "volumeMm3": _finite(sum(solid.Volume() for solid in shape.Solids())),
                "massKg": component_facts(shape, definition)["massKg"],
            })

    interferences: list[dict[str, Any]] = []
    near_pairs: list[dict[str, Any]] = []
    inspection_errors: list[dict[str, str]] = []
    for index, first in enumerate(physical):
        for second in physical[index + 1:]:
            pair = [first["instanceId"], second["instanceId"]]
            try:
                if _boxes_overlap(first["boundsMm"], second["boundsMm"]):
                    common = first["shape"].intersect(second["shape"], tol=1e-7)
                    volume = _finite(sum(solid.Volume() for solid in common.Solids()))
                    if volume > 1e-5:
                        smaller = min(first["volumeMm3"], second["volumeMm3"])
                        interferences.append({
                            "instances": pair,
                            "volumeMm3": volume,
                            "percentOfSmaller": _finite(100 * volume / smaller) if smaller > 0 else None,
                        })
                        continue
                # Only invoke the more expensive exact-distance operation for
                # bounding boxes that are already close to each other.
                if _box_gap(first["boundsMm"], second["boundsMm"]) <= 5.0:
                    distance = _finite(first["shape"].distance(second["shape"]))
                    if distance <= 5.0:
                        near_pairs.append({"instances": pair, "distanceMm": distance})
            except Exception as exc:  # OCCT reports the individual pair; the rest of the audit continues.
                inspection_errors.append({"instances": "/".join(pair), "message": str(exc)[:500]})

    public_instances = [{key: value for key, value in item.items() if key != "shape"} for item in physical]
    known_mass = [item["massKg"] for item in physical if item["massKg"] is not None]
    return {
        "id": configuration["id"],
        "name": configuration["name"],
        "instances": public_instances,
        "instanceCount": len(public_instances),
        "boundsMm": _combined_bounds(public_instances),
        "massKg": _finite(sum(known_mass)) if len(known_mass) == len(physical) and physical else None,
        "interferences": interferences,
        "nearPairs": near_pairs,
        "inspectionErrors": inspection_errors,
    }


def inspect_project(shapes: dict[str, cq.Shape], manifest: dict[str, Any]) -> dict[str, Any]:
    """Return a broad, request-independent evidence inventory."""
    components = {
        definition["id"]: component_facts(shapes[definition["id"]], definition)
        for definition in manifest.get("components", [])
    }
    configurations = [{"id": "as_built", "name": "As built", "frames": []}]
    configurations.extend(manifest.get("configurations", []))
    return {
        "schemaVersion": 1,
        "components": components,
        "references": manifest.get("references", []),
        "joints": manifest.get("joints", []),
        "featureOperations": manifest.get("featureOperations", []),
        "configurations": [
            _configuration_facts(shapes, manifest, configuration)
            for configuration in configurations
        ],
    }
