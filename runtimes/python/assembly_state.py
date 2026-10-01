"""Occurrence transforms and independent STEP population checks."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation


def support(name):
    path = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location("forma_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def frame_matrix(frame):
    result = np.eye(4)
    result[:3, :3] = Rotation.from_euler("xyz", frame["rotation"], degrees=True).as_matrix()
    result[:3, 3] = frame["position"]
    if not np.isfinite(result).all():
        raise ValueError("Non-finite occurrence transform")
    return result


def pose(transform):
    return {"position": transform[:3, 3].tolist(), "rotation": transform[:3, :3].tolist()}


def occurrence_transforms(manifest):
    instances = {i["id"]: i for i in manifest.get("instances", [])}
    if len(instances) != len(manifest.get("instances", [])):
        raise ValueError("Duplicate occurrence ID")
    worlds, visiting = {}, set()

    def world(iid):
        if iid in visiting:
            raise ValueError("Cyclic assembly occurrence hierarchy")
        if iid not in worlds:
            visiting.add(iid)
            item = instances[iid]
            local = frame_matrix(item["frame"])
            parent = item.get("parentId")
            worlds[iid] = world(parent) @ local if parent is not None else local
            visiting.remove(iid)
        return worlds[iid]
    for iid in instances:
        world(iid)
    return worlds


def native_request(manifest):
    native = manifest["nativeAssembly"]
    worlds = occurrence_transforms(manifest)
    definitions = {c["id"]: c for c in manifest["components"]}
    parents = {i.get("parentId") for i in manifest["instances"]}
    parts = [{"id": i["id"], "grounded": i["id"] in native["groundedInstances"], "pose": pose(worlds[i["id"]])}
             for i in manifest["instances"] if i["id"] not in parents
             and definitions[i["definitionId"]]["kind"] != "surface"]
    refs = {r["id"]: r for r in manifest["references"]}
    joints = []
    for joint in manifest["joints"]:
        if joint.get("lowerLimit") is not None or joint.get("upperLimit") is not None:
            raise ValueError("Native limits have not been qualified")
        joints.append({"id": joint["id"], "kind": joint["kind"],
            "a": {"occurrence": joint["occurrenceA"], "frame": pose(frame_matrix(refs[joint["referenceA"]]["frame"]))},
            "b": {"occurrence": joint["occurrenceB"], "frame": pose(frame_matrix(refs[joint["referenceB"]]["frame"]))}})
    motion = native.get("motion")
    return {"protocol": 1, "parts": parts, "joints": joints, "allowedDof": native["allowedDof"],
            "motion": {"joint": motion["jointId"], "start": motion["start"], "end": motion["end"],
                       "duration": motion["durationSeconds"], "steps": motion["steps"]} if motion else None}


def accepted_state(manifest, output, *, executable=None):
    worlds = occurrence_transforms(manifest)
    solution = None
    if manifest.get("nativeAssembly"):
        solver = support("native_assembly")
        solution = solver.solve(native_request(manifest), output, executable=executable)
        for iid, value in solution["frames"][0]["poses"].items():
            worlds[iid] = solver.matrix(value)
    return worlds, solution


def location(transform):
    import cadquery as cq
    from OCP.gp import gp_Trsf
    trsf = gp_Trsf()
    trsf.SetValues(*transform[:3, :].flatten().tolist())
    return cq.Location(trsf)


def placed_shape(shape, transform):
    return shape.moved(location(transform))


def mass_center(shape):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape.wrapped, props, 1e-9)
    point = props.CentreOfMass()
    return np.array([point.X(), point.Y(), point.Z()])


def canonical_assembly(manifest, shapes, worlds):
    import cadquery as cq
    instances = {i["id"]: i for i in manifest["instances"]}
    children = {iid: [] for iid in instances}
    roots = []
    for iid, instance in instances.items():
        parent = instance.get("parentId")
        (children[parent] if parent is not None else roots).append(iid)

    def node(iid):
        if children[iid]:
            assembly = cq.Assembly(name=iid)
            for child in sorted(children[iid]):
                local = np.linalg.inv(worlds[iid]) @ worlds[child]
                assembly.add(node(child), name=child, loc=location(local))
            return assembly
        return shapes[instances[iid]["definitionId"]]

    root = cq.Assembly(name=manifest["rootComponentId"])
    for iid in sorted(roots):
        root.add(node(iid), name=iid, loc=location(worlds[iid]))
    return root


def step_named_occurrences(path, expected_ids):
    import cadquery as cq
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDF import TDF_Label, TDF_LabelSequence
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool

    document = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    if reader.ReadFile(str(path)) != IFSelect_RetDone or not reader.Transfer(document):
        raise ValueError("Cannot reopen STEP occurrence hierarchy")
    tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    roots = TDF_LabelSequence()
    tool.GetFreeShapes(roots)
    found, count = {}, 0

    def transform(location):
        trsf = location.Transformation()
        result = np.eye(4)
        for row in range(3):
            for column in range(4):
                result[row, column] = trsf.Value(row + 1, column + 1)
        return result

    def visit(label, parent, depth=0):
        nonlocal count
        count += 1
        if count > 10_000 or depth > 64:
            raise ValueError("STEP occurrence hierarchy exceeds validation limits")
        referred = TDF_Label()
        definition = referred if XCAFDoc_ShapeTool.GetReferredShape_s(label, referred) else label
        world = parent @ transform(XCAFDoc_ShapeTool.GetLocation_s(label))
        name_attribute = TDataStd_Name()
        if label.FindAttribute(TDataStd_Name.GetID_s(), name_attribute):
            name = name_attribute.Get().ToExtString()
            if name in expected_ids:
                if name in found:
                    raise ValueError("Duplicate STEP occurrence identity")
                shape = cq.Shape.cast(XCAFDoc_ShapeTool.GetShape_s(definition))
                found[name] = placed_shape(shape, world)
        child_labels = TDF_LabelSequence()
        if XCAFDoc_ShapeTool.GetComponents_s(definition, child_labels):
            for index in range(1, child_labels.Length() + 1):
                visit(child_labels.Value(index), world, depth + 1)
    for index in range(1, roots.Length() + 1):
        visit(roots.Value(index), np.eye(4))
    if set(found) != set(expected_ids):
        raise ValueError("STEP labels do not preserve physical occurrence identity")
    return found


def verify_step_occurrences(shapes, manifest, worlds, *, step_path=None):
    """Match every physical solid after placement, not merely aggregate bounds.

    This gate checks the geometry/placement multiset. XDE occurrence labels are
    a separate identity check; this function never invents label correspondence.
    """
    definitions = {c["id"]: c for c in manifest["components"]}
    instances = {i["id"]: i for i in manifest.get("instances", [])}
    root = manifest.get("rootComponentId")
    if not instances:
        return {"validatedOccurrences": [root] if root and definitions[root]["kind"] != "surface" else [],
                "solidChecks": 0, "mode": "single_component"}
    if root is None or definitions[root]["kind"] != "assembly":
        raise ValueError("Multiple occurrences require a real exported assembly root")
    parents = {i.get("parentId") for i in instances.values()}
    physical = [iid for iid, i in instances.items() if iid not in parents
                and definitions[i["definitionId"]]["kind"] != "surface"]
    expected = [(iid, solid) for iid in physical for solid in
                placed_shape(shapes[instances[iid]["definitionId"]], worlds[iid]).Solids()]
    named = step_named_occurrences(step_path, physical) if manifest.get("nativeAssembly") and step_path else None
    if named is not None:
        for iid in physical:
            target = placed_shape(shapes[instances[iid]["definitionId"]], worlds[iid])
            actual_named = named[iid]
            tolerance = max(1e-4, target.Volume(tol=1e-9) * 1e-8)
            difference = (target.cut(actual_named).Volume(tol=1e-9)
                          + actual_named.cut(target).Volume(tol=1e-9))
            if difference > tolerance:
                raise ValueError(f"Named STEP occurrence {iid} has different geometry or placement")
    actual = list(shapes[root].Solids())
    if not expected or len(expected) != len(actual):
        raise ValueError("Assembly STEP solid inventory does not match physical occurrences")
    used = set()
    max_center = max_difference = 0.0
    for iid, target in expected:
        target_volume = target.Volume(tol=1e-9)
        target_center = mass_center(target)
        matched = False
        for index, candidate in enumerate(actual):
            if index in used:
                continue
            center_error = float(np.linalg.norm(mass_center(candidate) - target_center))
            if center_error > 1e-4:
                continue
            candidate_volume = candidate.Volume(tol=1e-9)
            tolerance = max(1e-4, target_volume * 1e-8)
            if abs(candidate_volume - target_volume) > tolerance:
                continue
            # Both directions are required; equal volume/centre alone can hide
            # a different feature or an orientation error in a symmetric part.
            difference = (target.cut(candidate).Volume(tol=1e-9)
                          + candidate.cut(target).Volume(tol=1e-9))
            if difference > tolerance:
                continue
            used.add(index)
            max_center, max_difference = max(max_center, center_error), max(max_difference, difference)
            matched = True
            break
        if not matched:
            raise ValueError(f"Assembly placements or geometry disagree with STEP at occurrence {iid}")
    return {"validatedOccurrences": sorted(physical), "solidChecks": len(expected),
            "maxCenterErrorMm": max_center, "maxSymmetricDifferenceMm3": max_difference,
            "mode": "xde_identity_and_solid_multiset" if named is not None else "independent_step_solid_multiset"}
