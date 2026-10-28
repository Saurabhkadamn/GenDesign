"""Scoped measurements of independently imported STEP; never imports generated source."""

import json
import math
from pathlib import Path

import cadquery as cq
import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Surface


def point(value):
    return [float(value.X()), float(value.Y()), float(value.Z())]


def entities(shape):
    result = []
    for index, face in enumerate(shape.Faces()):
        adaptor = BRepAdaptor_Surface(face.wrapped, True)
        kind = face.geomType().lower()
        item = {
            "faceIndex": index,
            "kind": kind,
            "areaMm2": face.Area(),
            "centerMm": face.Center().toTuple(),
        }
        if kind == "cylinder":
            surface = adaptor.Cylinder()
            item.update(
                radiusMm=surface.Radius(),
                originMm=point(surface.Location()),
                direction=point(surface.Axis().Direction()),
            )
        elif kind == "plane":
            surface = adaptor.Plane()
            item.update(
                originMm=point(surface.Location()),
                direction=point(surface.Axis().Direction()),
            )
        result.append(item)
    return result


def resolve(shape, selector):
    candidates = entities(shape)
    if selector.get("faceIndex") is not None:
        candidates = [e for e in candidates if e["faceIndex"] == selector["faceIndex"]]
    if selector.get("kind"):
        candidates = [e for e in candidates if e["kind"] == selector["kind"]]
    if selector.get("radiusMm") is not None:
        tolerance = selector.get("toleranceMm", 1e-5)
        candidates = [
            e
            for e in candidates
            if abs(e.get("radiusMm", float("inf")) - selector["radiusMm"]) <= tolerance
        ]
    if len(candidates) != 1:
        raise ValueError(
            f"Reference resolves to {len(candidates)} faces; inspect inventory and select exactly one current face"
        )
    entry = candidates[0]
    return shape.Faces()[entry["faceIndex"]], entry


def measure(shapes, manifest, request, support):
    state = support("assembly_state")
    configuration = request.get("configurationId", "as_built")
    posed = json.loads(json.dumps(manifest))
    if configuration != "as_built":
        configs = [
            c for c in manifest.get("configurations", []) if c["id"] == configuration
        ]
        if len(configs) != 1:
            raise ValueError("Unknown configuration")
        overrides = {f["instanceId"]: f["frame"] for f in configs[0].get("frames", [])}
        for instance in posed.get("instances", []):
            instance["frame"] = overrides.get(instance["id"], instance["frame"])
    worlds = state.occurrence_transforms(posed)
    instances = {i["id"]: i for i in posed.get("instances", [])}

    def selected(spec):
        if spec.get("instanceId"):
            instance = instances[spec["instanceId"]]
            shape = state.placed_shape(
                shapes[instance["definitionId"]], worlds[instance["id"]]
            )
        else:
            shape = shapes[spec["componentId"]]
        if spec.get("entity"):
            return resolve(shape, spec["entity"])
        return shape, None

    a, first = selected(request["a"])
    operation = request["operation"]
    if operation == "inventory":
        all_entities = entities(a)
        offset, limit = request.get("offset", 0), request.get("limit", 50)
        return {
            "entities": all_entities[offset : offset + limit],
            "total": len(all_entities),
            "nextOffset": offset + limit
            if offset + limit < len(all_entities)
            else None,
        }
    if operation == "cylinder_diameter":
        if not first or first["kind"] != "cylinder":
            raise ValueError("Select exactly one cylindrical face")
        return {"value": 2 * first["radiusMm"], "unit": "mm", "reference": first}
    if not request.get("b"):
        raise ValueError("This operation needs two references")
    b, second = selected(request["b"])
    if operation == "distance":
        return {
            "value": a.distance(b),
            "unit": "mm",
            "method": "OCCT minimum shape distance",
        }
    if operation == "interference":
        if first or second:
            raise ValueError("Interference requires whole components or occurrences")
        common = a.intersect(b, tol=1e-7)
        return {
            "value": sum(s.Volume(tol=1e-9) for s in common.Solids()),
            "unit": "mm3",
            "method": "OCCT Boolean intersection",
            "numericalToleranceMm": 1e-7,
        }
    if not first or not second or "direction" not in first or "direction" not in second:
        raise ValueError("Select cylindrical axes or planar normals")
    u, v = np.array(first["direction"]), np.array(second["direction"])
    angle = math.degrees(math.acos(float(np.clip(abs(np.dot(u, v)), -1, 1))))
    if operation == "angle":
        return {
            "value": angle,
            "unit": "deg",
            "method": "Unoriented axes/normals, range 0 to 90 degrees",
        }
    if operation == "axis_separation":
        if first["kind"] != "cylinder" or second["kind"] != "cylinder":
            raise ValueError("Axis separation requires two cylindrical surfaces")
        delta = np.array(second["originMm"]) - np.array(first["originMm"])
        cross = np.cross(u, v)
        distance = (
            np.linalg.norm(np.cross(delta, u))
            if np.linalg.norm(cross) < 1e-9
            else abs(np.dot(delta, cross)) / np.linalg.norm(cross)
        )
        return {
            "value": float(distance),
            "unit": "mm",
            "axisAngleDegrees": angle,
            "method": "Distance between infinite supporting axes; not finite shaft clearance",
        }
    raise ValueError("Unsupported measurement operation")


def execute(root: Path, output: Path, support):
    manifest = json.loads((root / "manifest.json").read_text())
    request = json.loads((root / "query.json").read_text())
    shapes = {
        c["id"]: cq.importers.importStep(str(root / (c["id"] + ".step"))).val()
        for c in manifest["components"]
    }
    if any(s.wrapped.IsNull() or not s.isValid() for s in shapes.values()):
        raise ValueError("Measurement input is not valid STEP geometry")
    result = measure(shapes, manifest, request, support)
    result.update(
        identity=json.loads((root / "identity.json").read_text()), request=request
    )
    (output / "measurement.json").write_text(json.dumps(result, allow_nan=False))
