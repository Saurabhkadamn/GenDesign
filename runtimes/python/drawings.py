"""Trusted drawings from independently reopened STEP, with native OCCT geometry.

All measurements use B-reps. Display approximation applies only to non-linear
projected curves, never to dimension values or tolerance intent.
"""

from __future__ import annotations

import hashlib
import json
import math

import cadquery as cq
import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Cylinder, GeomAbs_Plane
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape

MAX_EDGES = 20000
MAX_POINTS = 150000
CURVE_DEFLECTION_MM = 0.01
SYMBOLS = {
    "flatness": "⏥",
    "straightness": "⏤",
    "circularity": "○",
    "cylindricity": "⌭",
    "parallelism": "∥",
    "perpendicularity": "⊥",
    "angularity": "∠",
    "position": "⌖",
    "profile_surface": "⌓",
    "profile_line": "⌒",
    "circular_runout": "↗",
    "total_runout": "⇗",
}
BASES = {
    "front": ([1, 0, 0], [0, 0, 1], [0, -1, 0]),
    "top": ([1, 0, 0], [0, 1, 0], [0, 0, 1]),
    "right": ([0, 1, 0], [0, 0, 1], [1, 0, 0]),
}


def unit(value):
    vector = np.array(value, dtype=float)
    norm = np.linalg.norm(vector)
    if vector.shape != (3,) or not np.isfinite(vector).all() or norm < 1e-12:
        raise ValueError("Invalid geometric direction")
    return vector / norm


def vec(point):
    return np.array([point.X(), point.Y(), point.Z()])


def feature_inventory(shape):
    features = []
    for face in shape.Faces():
        surface = BRepAdaptor_Surface(face.wrapped, True)
        kind = surface.GetType()
        if kind == GeomAbs_Plane:
            geometry = surface.Plane()
            direction = unit(vec(geometry.Axis().Direction()))
            origin = vec(geometry.Location())
            # The point closest to the part origin is independent of surface UV.
            origin = direction * np.dot(origin, direction)
            feature = {
                "kind": "plane",
                "origin": origin.tolist(),
                "direction": direction.tolist(),
            }
        elif kind == GeomAbs_Cylinder:
            geometry = surface.Cylinder()
            direction = unit(vec(geometry.Axis().Direction()))
            origin = vec(geometry.Location())
            origin -= direction * np.dot(origin, direction)
            feature = {
                "kind": "cylinder",
                "origin": origin.tolist(),
                "direction": direction.tolist(),
                "radiusMm": geometry.Radius(),
                "fullCircle": abs(surface.LastUParameter() - surface.FirstUParameter())
                >= 2 * math.pi - 1e-6,
            }
        else:
            continue
        # A real point on the trimmed face is used for annotation attachment.
        # The canonical origin above describes the infinite support geometry;
        # it can lie far outside the actual part and is not a leader endpoint.
        feature["surfacePoint"] = list(face.Edges()[0].startPoint().toTuple())
        feature["center"] = list(face.Center().toTuple())
        # Identity for selection within this revision. Regeneration resolves the
        # geometry query again; hashes/face indices are never persistent anchors.
        key = {
            k: ([round(n, 8) for n in v] if isinstance(v, list) else v)
            for k, v in feature.items()
        }
        feature["id"] = (
            "f_"
            + hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]
        )
        features.append(feature)
    for feature in features:
        try:
            resolve_reference(features, feature)
            feature["referenceStatus"] = "unique"
        except ValueError:
            feature["referenceStatus"] = "ambiguous"
    return features


def resolve_reference(features, reference):
    direction = unit(reference["direction"])
    origin = np.array(reference["origin"], dtype=float)
    tolerance = reference.get("toleranceMm", 0.001)
    candidates = []
    for feature in features:
        if feature["kind"] != reference["kind"]:
            continue
        normal = unit(feature["direction"])
        if abs(np.dot(direction, normal)) < 1 - 1e-8:
            continue
        delta = origin - np.array(feature["origin"])
        distance = (
            abs(np.dot(delta, normal))
            if feature["kind"] == "plane"
            else np.linalg.norm(delta - normal * np.dot(delta, normal))
        )
        if distance <= tolerance:
            candidates.append(feature)
    if len(candidates) != 1:
        raise ValueError(
            "Referenced geometry is missing"
            if not candidates
            else "Referenced geometry is ambiguous"
        )
    return candidates[0]


def basis(spec):
    kind = spec["kind"]
    if kind == "isometric":
        normal = unit([1, -1, 1])
        horizontal = unit([1, 1, 0])
        vertical = np.cross(normal, horizontal)
        return horizontal, vertical, normal
    if kind == "section":
        kind = {"X": "right", "Y": "front", "Z": "top"}[spec.get("sectionAxis", "Y")]
    return tuple(np.array(value, dtype=float) for value in BASES[kind])


def clipped_section(shape, spec):
    """Keep the half behind the cutting plane as viewed, using native Boolean."""
    box = shape.BoundingBox()
    low = np.array([box.xmin, box.ymin, box.zmin]) - 1
    high = np.array([box.xmax, box.ymax, box.zmax]) + 1
    index = "XYZ".index(spec.get("sectionAxis", "Y"))
    offset = spec.get("sectionOffsetMm", 0)
    if not low[index] + 1 < offset < high[index] - 1:
        raise ValueError("Section plane does not cross the component interior")
    if index == 1:
        low[index] = offset
    else:
        high[index] = offset
    clipped = shape.intersect(
        cq.Solid.makeBox(*map(float, high - low), cq.Vector(*low))
    )
    if clipped.wrapped.IsNull() or not clipped.Faces() or not clipped.isValid():
        raise ValueError("Section cut did not produce valid geometry")
    return clipped


def section_hatching(model, spec):
    """Clip hatch lines against native cut faces, including their inner wires.

    Native edge/face Common keeps holes empty. Bounding-box or polygon filling
    would hatch through bores and misrepresent the section's material.
    """
    horizontal, vertical, _normal = basis(spec)
    axis = "XYZ".index(spec.get("sectionAxis", "Y"))
    offset = spec.get("sectionOffsetMm", 0)
    cut_faces = []
    for face in model.Faces():
        surface = BRepAdaptor_Surface(face.wrapped, True)
        if surface.GetType() != GeomAbs_Plane:
            continue
        plane = surface.Plane()
        if (
            abs(vec(plane.Location())[axis] - offset) < 1e-7
            and abs(vec(plane.Axis().Direction())[axis]) > 1 - 1e-8
        ):
            cut_faces.append(face)
    lines = []
    for face in cut_faces:
        vertices = [np.array(v.Center().toTuple()) for v in face.Vertices()]
        # Curved cut faces may have only one vertex: use native face bounds.
        bounds = face.BoundingBox()
        corners = [
            np.array([x, y, z])
            for x in (bounds.xmin, bounds.xmax)
            for y in (bounds.ymin, bounds.ymax)
            for z in (bounds.zmin, bounds.zmax)
        ]
        points = np.array(
            [
                [np.dot(p, horizontal), np.dot(p, vertical)]
                for p in [*vertices, *corners]
            ]
        )
        low, high = points.min(axis=0), points.max(axis=0)
        span = max(float(np.linalg.norm(high - low)), 1)
        spacing = max(1, span / 60)
        origin = np.zeros(3)
        origin[axis] = offset
        for intercept in np.arange(
            low[1] - high[0] - spacing, high[1] - low[0] + spacing, spacing
        ):
            p1 = (
                origin
                + horizontal * (low[0] - span)
                + vertical * (low[0] - span + intercept)
            )
            p2 = (
                origin
                + horizontal * (high[0] + span)
                + vertical * (high[0] + span + intercept)
            )
            common = cq.Edge.makeLine(cq.Vector(*p1), cq.Vector(*p2)).intersect(face)
            for edge in common.Edges():
                if edge.Length() <= 1e-7:
                    continue
                pair = [
                    np.array(p.toTuple()) for p in (edge.startPoint(), edge.endPoint())
                ]
                lines.append(
                    [
                        [float(np.dot(p, horizontal)), float(np.dot(p, vertical))]
                        for p in pair
                    ]
                )
                if len(lines) > 2000:
                    raise ValueError("Section hatching exceeds bounded line count")
    return lines


def projected_edges(shape, spec):
    horizontal, vertical, normal = basis(spec)
    model = clipped_section(shape, spec) if spec["kind"] == "section" else shape
    algo = HLRBRep_Algo()
    algo.Add(model.wrapped)
    algo.Projector(
        HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*normal), gp_Dir(*horizontal)))
    )
    algo.Update()
    algo.Hide()
    result = HLRBRep_HLRToShape(algo)
    curves = []
    seen = set()
    points_count = 0
    groups = [(False, result.VCompound()), (False, result.OutLineVCompound())]
    if spec.get("hiddenLines", True):
        groups += [(True, result.HCompound()), (True, result.OutLineHCompound())]
    for hidden, wrapped in groups:
        if wrapped.IsNull():
            continue
        for edge in cq.Shape.cast(wrapped).Edges():
            if len(curves) >= MAX_EDGES:
                raise ValueError("Drawing exceeds bounded edge count")
            if edge.Length() < 1e-8:
                continue
            adaptor = BRepAdaptor_Curve(edge.wrapped)
            points = (
                [edge.startPoint(), edge.endPoint()]
                if edge.geomType() == "LINE"
                else edge.sample(CURVE_DEFLECTION_MM)[0]
            )
            points = [[p.x, p.y] for p in points]
            if len(points) < 2:
                continue
            points_count += len(points)
            if points_count > MAX_POINTS:
                raise ValueError(
                    "Drawing curve approximation exceeds bounded point count"
                )
            key = (hidden, tuple(tuple(round(n, 6) for n in p) for p in points))
            reverse = (hidden, tuple(reversed(key[1])))
            if key in seen or reverse in seen:
                continue
            seen.add(key)
            curve = {
                "kind": edge.geomType().lower(),
                "points": points,
                "hidden": hidden,
            }
            if adaptor.GetType() == GeomAbs_Circle:
                circle = adaptor.Circle()
                curve.update(
                    center=[circle.Location().X(), circle.Location().Y()],
                    radius=circle.Radius(),
                    closed=edge.IsClosed(),
                )
            curves.append(curve)
    visible = [p for c in curves if not c["hidden"] for p in c["points"]]
    if not visible:
        raise ValueError("Projection contains no visible geometry")
    # Native projected bounds, separate from approximate display polylines.
    bounds_model = cq.Compound.makeCompound(
        [cq.Shape.cast(s) for hidden, s in groups if not hidden and not s.IsNull()]
    )
    bounds = bounds_model.BoundingBox()
    return {
        "id": spec["id"],
        "kind": spec["kind"],
        "curves": curves,
        "bounds": [bounds.xmin, bounds.ymin, bounds.xmax, bounds.ymax],
        "basis": {
            "horizontal": horizontal.tolist(),
            "vertical": vertical.tolist(),
            "normal": normal.tolist(),
        },
        "hatchLines": section_hatching(model, spec)
        if spec["kind"] == "section"
        else [],
        "sectionAxis": spec.get("sectionAxis") if spec["kind"] == "section" else None,
        "sectionOffsetMm": spec.get("sectionOffsetMm")
        if spec["kind"] == "section"
        else None,
    }


def measure_dimension(shape, features, spec):
    kind = spec["kind"]
    if kind == "extent":
        bounds = shape.BoundingBox()
        return {
            "value": getattr(bounds, spec.get("axis", "X").lower() + "len"),
            "unit": "mm",
        }
    first = resolve_reference(features, spec["reference"])
    if kind in {"diameter", "radius"}:
        return {
            "value": first["radiusMm"] * (2 if kind == "diameter" else 1),
            "unit": "mm",
            "feature": first,
        }
    second = resolve_reference(features, spec["secondReference"])
    if kind == "angle":
        if first["kind"] != "plane" or second["kind"] != "plane":
            raise ValueError("Angular dimensions currently require two planar features")
        angle = math.degrees(
            math.acos(
                float(
                    np.clip(
                        abs(
                            np.dot(unit(first["direction"]), unit(second["direction"]))
                        ),
                        0,
                        1,
                    )
                )
            )
        )
        if angle < 1e-7:
            raise ValueError("Angular dimension requires intersecting support planes")
        return {
            "value": angle,
            "unit": "deg",
            "feature": first,
            "secondFeature": second,
            "angleConvention": "acute_support_plane",
        }
    # Distance is along the explicit component axis between resolved support
    # planes/axes. It is not an arbitrary projected silhouette distance.
    axis = "XYZ".index(spec.get("axis", "X"))
    if any(
        f["kind"] == "plane" and abs(unit(f["direction"])[axis]) < 1 - 1e-8
        for f in (first, second)
    ):
        raise ValueError("Distance planes must be normal to the measurement axis")
    if any(
        f["kind"] == "cylinder" and abs(unit(f["direction"])[axis]) > 1e-8
        for f in (first, second)
    ):
        raise ValueError(
            "Distance cylinder axes must be perpendicular to the measurement axis"
        )
    return {
        "value": abs(first["origin"][axis] - second["origin"][axis]),
        "unit": "mm",
        "feature": first,
        "secondFeature": second,
    }


def display_value(value):
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


def dimension_text(spec, measured):
    prefix = {"diameter": "Ø", "radius": "R"}.get(spec["kind"], "")
    text = (
        prefix
        + display_value(measured["value"])
        + ("°" if measured["unit"] == "deg" else "")
    )
    if spec.get("basic"):
        return "[" + text + "]"
    upper, lower = spec.get("upperTolerance"), spec.get("lowerTolerance")
    if upper is not None:
        text += (
            " ±" + display_value(upper)
            if upper == lower
            else " +" + display_value(upper) + " / −" + display_value(lower)
        )
    return text


def automatic_specs(sheet, views, features):
    specs = []
    for view in views:
        if view["kind"] not in BASES:
            continue
        for row, basis_axis in enumerate(("horizontal", "vertical")):
            axis = "XYZ"[int(np.argmax(np.abs(view["basis"][basis_axis])))]
            specs.append(
                {
                    "id": f"auto_{view['id']}_{axis}",
                    "viewId": view["id"],
                    "kind": "extent",
                    "axis": axis,
                    "offsetMm": 10 + row * 6,
                    "automatic": True,
                }
            )
        normal = unit(view["basis"]["normal"])
        for feature in features:
            if (
                feature["kind"] == "cylinder"
                and feature["fullCircle"]
                and abs(np.dot(normal, unit(feature["direction"]))) > 1 - 1e-8
            ):
                specs.append(
                    {
                        "id": f"auto_{view['id']}_{feature['id']}",
                        "viewId": view["id"],
                        "kind": "diameter",
                        "reference": {
                            k: feature[k] for k in ("kind", "origin", "direction")
                        },
                        "offsetMm": 10,
                        "automatic": True,
                    }
                )
                # Locate the hole/shaft axis from actual planar support features.
                # These are native plane-to-axis distances, not mesh coordinates.
                for basis_axis in ("horizontal", "vertical"):
                    axis_index = int(np.argmax(np.abs(view["basis"][basis_axis])))
                    planes = [
                        p
                        for p in features
                        if p["kind"] == "plane"
                        and abs(unit(p["direction"])[axis_index]) > 1 - 1e-8
                    ]
                    if not planes:
                        continue
                    plane = min(planes, key=lambda p: p["origin"][axis_index])
                    specs.append(
                        {
                            "id": f"pos_{view['id']}_{feature['id']}_{'XYZ'[axis_index]}",
                            "viewId": view["id"],
                            "kind": "distance",
                            "axis": "XYZ"[axis_index],
                            "reference": {
                                k: plane[k] for k in ("kind", "origin", "direction")
                            },
                            "secondReference": {
                                k: feature[k] for k in ("kind", "origin", "direction")
                            },
                            "offsetMm": 20,
                            "automatic": True,
                        }
                    )
    if len(specs) > 100:
        raise ValueError(
            "Automatic dimensions exceed 100; choose explicit dimensions or additional sheets"
        )
    return specs


def generate_sheet(shape, definition, spec, identity, bom=None):
    features = feature_inventory(shape)
    views = []
    issues = []
    dimensions = []
    datums = []
    controls = []
    for view in spec["views"]:
        try:
            views.append(projected_edges(shape, view))
        except (ValueError, RuntimeError) as error:
            issues.append({"id": view["id"], "kind": "view", "message": str(error)})
    view_ids = {v["id"] for v in views}
    specs = (
        automatic_specs(spec, views, features)
        if spec.get("autoDimensions", True)
        else []
    )
    # Explicit extent dimensions replace their automatic counterpart to avoid
    # contradictory toleranced and untoleranced duplicate callouts.
    explicit = spec.get("dimensions", [])
    specs = [
        s
        for s in specs
        if not any(
            e["kind"] == s["kind"]
            and e["viewId"] == s["viewId"]
            and (
                e.get("axis", "X") == s.get("axis", "X")
                if s["kind"] == "extent"
                else all(
                    (e.get("reference") or {}).get(k)
                    == (s.get("reference") or {}).get(k)
                    for k in ("kind", "origin", "direction")
                )
                and (
                    s["kind"] != "distance"
                    or e.get("axis", "X") == s.get("axis", "X")
                    and all(
                        (e.get("secondReference") or {}).get(k)
                        == (s.get("secondReference") or {}).get(k)
                        for k in ("kind", "origin", "direction")
                    )
                )
            )
            for e in explicit
        )
    ]
    for annotation in [*specs, *explicit]:
        try:
            if annotation["viewId"] not in view_ids:
                raise ValueError("Drawing view is unresolved")
            measured = measure_dimension(shape, features, annotation)
            if not math.isfinite(measured["value"]):
                raise ValueError("Non-finite dimension")
            dimensions.append(
                {
                    **annotation,
                    **measured,
                    "text": dimension_text(annotation, measured),
                    "status": "resolved",
                }
            )
        except (ValueError, RuntimeError) as error:
            issues.append(
                {"id": annotation["id"], "kind": "dimension", "message": str(error)}
            )
    for datum in spec.get("datums", []):
        try:
            if datum["viewId"] not in view_ids:
                raise ValueError("Datum view is unresolved")
            datums.append(
                {
                    **datum,
                    "feature": resolve_reference(features, datum["reference"]),
                    "status": "resolved",
                }
            )
        except ValueError as error:
            issues.append(
                {"id": datum["label"], "kind": "datum", "message": str(error)}
            )
    resolved_labels = {d["label"] for d in datums}
    for control in spec.get("controls", []):
        try:
            if control["viewId"] not in view_ids:
                raise ValueError("Control view is unresolved")
            if not set(control.get("datums", [])).issubset(resolved_labels):
                raise ValueError("Referenced datum is unresolved")
            feature = resolve_reference(features, control["reference"])
            text = " | ".join(
                [
                    SYMBOLS[control["characteristic"]],
                    ("Ø" if control.get("zone") == "diameter" else "")
                    + display_value(control["toleranceMm"])
                    + (
                        {"MMC": " Ⓜ", "LMC": " Ⓛ"}.get(
                            control.get("materialCondition"), ""
                        )
                    ),
                    *control.get("datums", []),
                ]
            )
            controls.append(
                {
                    **control,
                    "feature": feature,
                    "text": text,
                    "status": "resolved",
                    "conformance": "not_measured",
                    "intent": "engineer_specified",
                }
            )
        except ValueError as error:
            issues.append(
                {"id": control["id"], "kind": "control", "message": str(error)}
            )
    return {
        "schemaVersion": 1,
        "id": spec["id"],
        "componentId": definition["id"],
        "title": spec["title"],
        "partMetadata": definition.get("partMetadata") or {},
        "units": "mm",
        "paper": spec.get("paper", "A3"),
        "projection": spec.get("projection", "third_angle"),
        "standard": spec.get("standard", "ISO"),
        "requestedScale": spec.get("scale"),
        "status": "unresolved" if issues else "draft",
        "identity": identity,
        "views": views,
        "features": features,
        "dimensions": dimensions,
        "datums": datums,
        "controls": controls,
        "issues": issues,
        "notes": spec.get("notes", []),
        "curveDeflectionMm": CURVE_DEFLECTION_MM,
        "bom": bom if spec.get("includeBom") else None,
        "releaseEligible": not issues,
        "manufacturingApproved": False,
        "standardsCoverage": "Supported annotation subset; engineer review required",
    }


def generate_drawings(
    shapes,
    manifest,
    identity,
    bom=None,
    *,
    occurrence_shapes=None,
    validated_occurrences=None,
):
    definitions = {d["id"]: d for d in manifest["components"]}
    sheets = [
        generate_sheet(
            shapes[s["componentId"]], definitions[s["componentId"]], s, identity, bom
        )
        for s in manifest.get("drawings", [])
    ]
    instances = {i["id"]: i for i in manifest.get("instances", [])}
    accepted = set(validated_occurrences or [])
    for sheet in sheets:
        sheet["balloons"] = []
        if not sheet.get("bom"):
            continue
        try:
            if sheet["componentId"] != manifest["rootComponentId"]:
                raise ValueError(
                    "This parts list describes the accepted root assembly; use a root assembly sheet."
                )
            if bom.get("identity") != identity or not occurrence_shapes or not accepted:
                raise ValueError(
                    "Drawing BOM requires revision-bound, independently validated occurrences"
                )
            view = next(
                (v for v in sheet["views"] if v["kind"] == "isometric"),
                sheet["views"][0],
            )
            normal = np.array(view["basis"]["normal"])

            def descendants(iid):
                result = []
                for leaf in sorted(accepted):
                    current = leaf
                    while current:
                        if current == iid:
                            result.append(leaf)
                            break
                        current = instances.get(current, {}).get("parentId")
                return result

            for index, row in enumerate(bom["flat"], 1):
                if len(row["occurrenceIds"]) != row["quantity"]:
                    raise ValueError(
                        "Drawing BOM quantity disagrees with its accepted occurrence links"
                    )
                iid = row["occurrenceIds"][0]
                leaves = descendants(iid)
                if not leaves:
                    raise ValueError(
                        "Drawing BOM item has no independently validated geometry"
                    )
                leaf = leaves[0]
                placed = occurrence_shapes[leaf]
                point = max(
                    (np.array(v.Center().toTuple()) for v in placed.Vertices()),
                    key=lambda p: np.dot(p, normal),
                )
                sheet["balloons"].append(
                    {
                        "item": str(index),
                        "viewId": view["id"],
                        "definitionId": row["definitionId"],
                        "bomOccurrenceId": iid,
                        "anchorOccurrenceId": leaf,
                        "occurrenceIds": row["occurrenceIds"],
                        "quantity": row["quantity"],
                        "anchor": point.tolist(),
                        "status": "resolved",
                    }
                )
        except (ValueError, KeyError, IndexError) as error:
            sheet["issues"].append(
                {"id": sheet["id"], "kind": "bom", "message": str(error)}
            )
            sheet["status"] = "unresolved"
            sheet["releaseEligible"] = False
    return {
        "schemaVersion": 1,
        "identity": identity,
        "status": "unresolved" if any(s["issues"] for s in sheets) else "draft",
        "sheets": sheets,
        "manufacturingApproved": False,
    }
