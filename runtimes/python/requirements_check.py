"""Measure explicit requirements against imported STEP geometry, never generated assertions."""
import math


def cylinders(shape, axis_name="Z"):
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    result = []
    for face in shape.Faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType() != GeomAbs_Cylinder:
            continue
        cylinder = surface.Cylinder()
        axis = cylinder.Axis().Direction()
        axis_index = {"X": 0, "Y": 1, "Z": 2}.get(axis_name, 2)
        axis_vector = (axis.X(), axis.Y(), axis.Z())
        if abs(abs(axis_vector[axis_index]) - 1) > 1e-7:
            continue
        center = cylinder.Location()
        radius = cylinder.Radius()
        u0, u1 = surface.FirstUParameter(), surface.LastUParameter()
        point = surface.Value((u0 + u1) / 2, (surface.FirstVParameter() + surface.LastVParameter()) / 2)
        coordinates = [point.X(), point.Y(), point.Z()]
        center_coordinates = [center.X(), center.Y(), center.Z()]
        plane = [i for i in range(3) if i != axis_index]
        delta = [coordinates[i] - center_coordinates[i] for i in plane]
        epsilon = min(0.005, radius * 0.001)
        radial_out_coordinates = list(coordinates)
        radial_in_coordinates = list(coordinates)
        for i, d in zip(plane, delta):
            radial_out_coordinates[i] += epsilon * d / radius
            radial_in_coordinates[i] -= epsilon * d / radius
        radial_out = tuple(radial_out_coordinates)
        radial_in = tuple(radial_in_coordinates)
        outside_solid = shape.isInside(radial_out, 1e-7)
        inside_solid = shape.isInside(radial_in, 1e-7)
        bounds = face.BoundingBox()
        axis_bounds = [
            (bounds.xmin, bounds.xmax),
            (bounds.ymin, bounds.ymax),
            (bounds.zmin, bounds.zmax),
        ][axis_index]
        # A through-hole's cylindrical wall opens into free space at both
        # ends.  Comparing it with the whole component bounding box is wrong
        # for a connected L bracket, where a local shelf/back plate is only a
        # fraction of the component envelope.
        probe = list(center_coordinates)
        low, high = axis_bounds
        eps_axis = 1e-3
        probe[axis_index] = low - eps_axis
        low_inside = shape.isInside(tuple(probe), 1e-7)
        probe[axis_index] = high + eps_axis
        high_inside = shape.isInside(tuple(probe), 1e-7)
        result.append({"center": [center_coordinates[i] for i in plane], "radius": radius,
                       "angle": abs(u1 - u0), "z": [bounds.zmin, bounds.zmax],
                       "concave": outside_solid and not inside_solid,
                       "convex": inside_solid and not outside_solid,
                       "axis": axis_name, "plane": plane,
                       "axis_bounds": axis_bounds,
                       "through": not low_inside and not high_inside})
    return result


def check_requirements(shapes, manifest, requirements):
    checks = []
    for requirement in requirements:
        cid = requirement.get("componentId") or manifest.get("rootComponentId")
        item = {"id": requirement["id"], "description": requirement["description"],
                "kind": requirement["kind"], "componentId": cid, "status": "unverified", "evidence": {}}
        shape = shapes.get(cid)
        if shape is None:
            root = manifest.get("rootComponentId")
            shape = shapes.get(root)
            if shape is not None:
                cid = root
        if shape is None or requirement["kind"] == "unverified":
            item["detail"] = "No deterministic check is available for this requirement."
            checks.append(item)
            continue
        tolerance = requirement.get("tolerance", 0.02)
        near = lambda a, b: abs(a - b) <= tolerance
        box = shape.BoundingBox()
        kind = requirement["kind"]
        passed = False
        if kind == "dimensions":
            actual = [box.xlen, box.ylen, box.zlen]
            expected = requirement["dimensions"]
            passed = all(near(a, b) for a, b in zip(actual, expected))
            item["evidence"] = {"expectedMm": expected, "measuredMm": actual}
        elif kind == "center":
            actual = [(box.xmin + box.xmax) / 2, (box.ymin + box.ymax) / 2, (box.zmin + box.zmax) / 2]
            expected = requirement["center"]
            passed = all(near(a, b) for a, b in zip(actual, expected))
            item["evidence"] = {"expectedMm": expected, "measuredMm": actual, "method": "bounding-box center"}
        elif kind == "solid_count":
            actual = len(shape.Solids())
            passed = actual == requirement["count"]
            item["evidence"] = {"expected": requirement["count"], "measured": actual}
        elif kind in ("through_holes", "corner_radius"):
            axis = requirement.get("axis", "Z")
            axis_index = {"X": 0, "Y": 1, "Z": 2}.get(axis, 2)
            bounds = [(box.xmin, box.xmax), (box.ymin, box.ymax), (box.zmin, box.zmax)]
            faces = cylinders(shape, axis)
            full_depth = lambda f: f.get("through", False)
            if kind == "through_holes":
                actual = [f for f in faces if f["concave"] and full_depth(f) and abs(f["angle"] - 2 * math.pi) < 1e-5]
                expected = requirement["positions"]
                radius = requirement["diameter"] / 2
            else:
                radius = requirement["radius"]
                actual = [f for f in faces if f["convex"] and full_depth(f) and abs(f["angle"] - math.pi / 2) < 1e-5]
                expected = [[x, y] for x in (box.xmin + radius, box.xmax - radius)
                            for y in (box.ymin + radius, box.ymax - radius)]
            # A one-to-one match rejects duplicated positions and extra holes/corners.
            unmatched = list(actual)
            matched = []
            for position in expected:
                match = next((f for f in unmatched if near(f["radius"], radius)
                              and all(near(a, b) for a, b in zip(f["center"], position))), None)
                if match is not None:
                    matched.append(match)
                    unmatched.remove(match)
            # A part may contain additional, separately documented holes (for
            # example a shaft bore alongside six mounting holes).  Require a
            # one-to-one match for every requested hole, but do not reject
            # those independent features as an accidental duplicate.
            passed = len(matched) == len(expected) == requirement["count"]
            item["evidence"] = {"expectedCount": requirement["count"], "expectedPositionsMm": expected,
                                "expectedRadiusMm": radius, "measuredCylindricalFaces": actual,
                                 "throughDepthMm": bounds[axis_index][1] - bounds[axis_index][0], "axis": axis}
        else:
            item["detail"] = "Unsupported deterministic check."
            checks.append(item)
            continue
        item["status"] = "passed" if passed else "failed"
        item["toleranceMm"] = tolerance
        checks.append(item)
    return checks
