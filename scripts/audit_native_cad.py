"""Manual, bounded local capability audit; does not change Forma product behavior.

Run with the locked CAD venv: python scripts/audit_native_cad.py
Each reviewed fixture runs in a separate child process. This is process isolation,
not an OS sandbox; do not use this harness to run untrusted customer code.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import traceback

REPO = Path(__file__).resolve().parents[1]
ARTIFACTS = REPO / "tmp" / "forma-local-cad-audit"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def json_write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")


def versions():
    return {name: importlib.metadata.version(name) for name in
            ["cadquery", "cadquery-ocp", "cadquery-ocp-proxy", "casadi", "nlopt", "vtk", "psutil"]}


def solver_stats(assembly):
    return {k: assembly._solve_result.get(k) for k in ["success", "return_status", "iter_count"]}


def spline_surface(directory):
    import cadquery as cq
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_BSplineSurface
    heights = []
    max_area_error = 0.0
    for scale in [0.5, 1, 2, 3]:
        grid = [[cq.Vector(x, y, scale * 0.002 * x * y)
                 for y in range(0, 41, 10)] for x in range(0, 41, 10)]
        face = cq.Face.makeSplineApprox(grid, tol=1e-6, minDeg=3, maxDeg=3)
        require(face.isValid(), "Invalid fitted surface")
        adaptor = BRepAdaptor_Surface(face.wrapped)
        require(adaptor.GetType() == GeomAbs_BSplineSurface, "Not a B-spline")
        path = directory / f"surface-{scale}.step"
        cq.exporters.export(face, str(path))
        imported = cq.importers.importStep(str(path)).val()
        require(imported.isValid(), "Invalid imported surface")
        require(any(BRepAdaptor_Surface(f.wrapped).GetType() == GeomAbs_BSplineSurface
                    for f in imported.Faces()), "Spline lost in exchange")
        error = abs(imported.Area() - face.Area())
        require(error < 1e-4, "STEP surface area changed")
        max_area_error = max(max_area_error, error)
        heights.append(face.BoundingBox().zlen)
    require(all(a < b for a, b in zip(heights, heights[1:])), "Edit did not alter surface")
    return {"parameterVariants": 4, "heightMm": heights,
            "maxStepAreaErrorMm2": max_area_error,
            "limit": "One smooth patch; no seam continuity or Class-A quality claim"}


def rational_nurbs(directory):
    import cadquery as cq
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_BSplineSurface
    original = cq.Solid.makeSphere(10, angleDegrees1=-90, angleDegrees2=90)
    shape = original.toNURBS()
    require(shape.isValid(), "Invalid NURBS conversion")
    rational = []
    for face in shape.Faces():
        adaptor = BRepAdaptor_Surface(face.wrapped)
        if adaptor.GetType() == GeomAbs_BSplineSurface:
            spline = adaptor.BSpline()
            rational.append(bool(spline.IsURational() or spline.IsVRational()))
    require(any(rational), "No rational patch found")
    path = directory / "rational-sphere.step"
    cq.exporters.export(shape, str(path))
    imported = cq.importers.importStep(str(path)).val()
    expected = 4 * math.pi * 10**3 / 3
    # Default non-adaptive volume integration can lose accuracy on spline faces.
    # Measure both defaults and explicit adaptive integration, then check geometry
    # independently against the analytic sphere rather than relaxing the threshold.
    accurate_volume = imported.Volume(tol=1e-10)
    error = abs(accurate_volume - expected)
    radial_error = 0.0
    for face in imported.Faces():
        adaptor = BRepAdaptor_Surface(face.wrapped)
        for i in range(21):
            u = adaptor.FirstUParameter() + (adaptor.LastUParameter() - adaptor.FirstUParameter()) * i / 20
            for j in range(21):
                v = adaptor.FirstVParameter() + (adaptor.LastVParameter() - adaptor.FirstVParameter()) * j / 20
                p = adaptor.Value(u, v)
                radial_error = max(radial_error, abs(math.sqrt(p.X()**2 + p.Y()**2 + p.Z()**2) - 10))
    require(imported.isValid() and error < 1e-3 and radial_error < 1e-7,
            f"NURBS precision mismatch: volume={error}, radial={radial_error}")
    return {"rationalPatchFound": True, "accurateVolumeMm3": accurate_volume,
            "analyticVolumeErrorMm3": error, "maxSampledRadiusErrorMm": radial_error,
            "radialSamples": 441 * len(imported.Faces()),
            "defaultVolumeMm3": imported.Volume(),
            "defaultVolumeErrorPercent": abs(imported.Volume() - expected) / expected * 100,
            "defaultStepRoundtripVolumeErrorMm3": abs(imported.Volume() - shape.Volume()),
            "finding": "Explicit property integration tolerance is needed; valid spline geometry alone does not guarantee accurate default mass properties"}


def make_stack(thickness=2.0, conflict=False):
    import cadquery as cq
    plate = cq.Workplane("XY").box(10, 10, thickness)
    assembly = cq.Assembly(name="stack").add(plate, name="base").add(
        plate, name="lid", loc=cq.Location(cq.Vector(0, 0, 10)))
    assembly.constrain("base", "Fixed")
    if conflict:
        assembly.constrain("lid", "Fixed")
    assembly.constrain("base@faces@>Z", "lid@faces@<Z", "Plane")
    return assembly, plate


def stack_residual(assembly, thickness):
    return abs(assembly.objects["lid"].loc.toTuple()[0][2] - thickness)


def static_mate(directory):
    assembly, _ = make_stack()
    assembly.solve()
    error = stack_residual(assembly, 2)
    require(assembly.toCompound().isValid() and error < 1e-4, "Mate residual too high")
    return {"positionResidualMm": error, "solver": solver_stats(assembly)}


def conflicting_mates(directory):
    assembly, _ = make_stack(conflict=True)
    try:
        assembly.solve()
    except Exception as exc:
        return {"solverRejected": True, "exceptionType": type(exc).__name__,
                "diagnostic": str(exc)[-500:]}
    residual = stack_residual(assembly, 2)
    fixed_error = abs(assembly.objects["lid"].loc.toTuple()[0][2] - 10)
    require(residual > 1e-4 or fixed_error > 1e-4, "Conflicting fixture wrongly constructed")
    return {"solverRejected": False, "solver": solver_stats(assembly),
            "mateResidualMm": residual, "fixedPositionResidualMm": fixed_error,
            "finding": "Solver returning is insufficient; an independent residual gate is required"}


def underconstrained_mate(directory):
    import cadquery as cq
    plate = cq.Workplane("XY").box(10, 6, 2)
    solutions = []
    for angle in [15, 45]:
        assembly = cq.Assembly(name="partial").add(plate, name="base").add(
            plate, name="lid", loc=cq.Location((0, 0, 10), (0, 0, angle)))
        assembly.constrain("base", "Fixed")
        assembly.constrain("lid", "FixedPoint", (0, 0, 10))
        assembly.solve()
        position, rotation = assembly.objects["lid"].loc.toTuple()
        require(math.dist(position, (0, 0, 10)) < 1e-4, "Position constraint missed")
        solutions.append({"startAngleDeg": angle, "positionMm": position,
                          "rotationDeg": rotation, "solver": solver_stats(assembly)})
    difference = abs(solutions[1]["rotationDeg"][2] - solutions[0]["rotationDeg"][2])
    require(difference > 20, "Probe did not demonstrate orientation freedom")
    return {"solutions": solutions, "rotationDifferenceDeg": difference,
            "finding": "Unspecified rotation remains free; Forma must distinguish intended DOF from missing mates"}


def edits_and_references(directory):
    import cadquery as cq
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    max_residual = 0.0
    for n in range(50):
        thickness = 1 + n * 0.08
        assembly, _ = make_stack(thickness)
        assembly.solve()
        require(assembly.toCompound().isValid(), "Invalid edited assembly")
        max_residual = max(max_residual, stack_residual(assembly, thickness))
    require(max_residual < 1e-4, "Parameter edit broke the defined mate")
    box = cq.Workplane("XY").box(20, 20, 5)
    cut = box.faces(">Z").workplane().hole(4)
    before = [str(BRepAdaptor_Surface(f.wrapped).GetType()) for f in box.val().Faces()]
    after = [str(BRepAdaptor_Surface(f.wrapped).GetType()) for f in cut.val().Faces()]
    indexed_reference_changed = before[-1] != after[-1]
    named_top = cut.faces(">Z").val()
    require(abs(named_top.Center().z - 2.5) < 1e-6, "Directional top selection failed")
    try:
        cq.Workplane("XY").box(10, 10, 2).edges().fillet(10)
        invalid_fillet_rejected = False
    except Exception:
        invalid_fillet_rejected = True
    require(invalid_fillet_rejected, "Oversized fillet unexpectedly accepted")
    return {"dimensionEdits": 50, "maxMateResidualMm": max_residual,
            "lastFaceTypeBefore": before[-1], "lastFaceTypeAfter": after[-1],
            "numericLastFaceReferenceChangedMeaning": indexed_reference_changed,
            "directionalTopSelectorWorksInThisFixture": True,
            "oversizedFilletRejected": invalid_fillet_rejected,
            "limit": "Reconstructed code recipes; not persistent topological naming or Forma automatic edit propagation"}


def assembly_60(directory):
    import cadquery as cq
    prototypes = [cq.Workplane("XY").box(10, 6, 2),
                  cq.Workplane("XY").circle(3).extrude(4).translate((0, 0, -2)),
                  cq.Workplane("XY").box(8, 5, 3).faces(">Z").workplane().hole(2)]
    assembly = cq.Assembly(name="fixture60")
    targets = []
    for i in range(60):
        target = (20.0 * (i % 10), 20.0 * (i // 10), 0.0)
        targets.append(target)
        start = target if i == 0 else (target[0] + 0.5, target[1] - 0.2, 0.1)
        assembly.add(prototypes[i % 3], name=f"part{i}", loc=cq.Location(start))
        if i == 0:
            assembly.constrain("part0", "Fixed")
        else:
            # FixedPoint targets the local shape center; compensate asymmetric holes.
            center = prototypes[i % 3].val().Center()
            global_center = (target[0] + center.x, target[1] + center.y, target[2] + center.z)
            assembly.constrain(f"part{i}", "FixedPoint", global_center)
            assembly.constrain(f"part{i}", "FixedRotation", (0, 0, 0))
    start = time.perf_counter()
    assembly.solve()
    solve_seconds = time.perf_counter() - start
    errors, angles = [], []
    for i, target in enumerate(targets):
        position, rotation = assembly.objects[f"part{i}"].loc.toTuple()
        errors.append(math.dist(position, target))
        angles.append(max(abs(a) for a in rotation))
    require(max(errors) < 1e-3 and max(angles) < 1e-3, "60-part pose residual failed")
    path = directory / "fixture60.step"
    start = time.perf_counter()
    assembly.export(str(path))
    imported = cq.importers.importStep(str(path)).val()
    exchange_seconds = time.perf_counter() - start
    require(imported.isValid() and len(imported.Solids()) == 60, "60-part STEP exchange failed")
    expected_centers = [prototypes[i % 3].val().moved(assembly.objects[f"part{i}"].loc).Center()
                        for i in range(60)]
    actual_centers = [s.Center() for s in imported.Solids()]
    unmatched = list(actual_centers)
    center_errors = []
    for center in expected_centers:
        nearest = min(range(len(unmatched)), key=lambda j: (unmatched[j] - center).Length)
        center_errors.append((unmatched.pop(nearest) - center).Length)
    require(max(center_errors) < 1e-4, "STEP occurrence centers disagree")
    return {"occurrences": 60, "uniquePartDefinitions": 3, "constraints": len(assembly.constraints),
            "solveSeconds": solve_seconds, "stepExportImportSeconds": exchange_seconds,
            "stepBytes": path.stat().st_size, "maxPositionResidualMm": max(errors),
            "maxRotationResidualDeg": max(angles), "maxStepCenterResidualMm": max(center_errors),
            "solver": solver_stats(assembly),
            "limit": "Synthetic static poses with unary constraints; no closed-loop motion, contact, or large imported production geometry"}


def forma_nested_60(directory):
    import cadquery as cq
    import numpy as np
    import trimesh
    sys.path.insert(0, str(REPO / "runtimes" / "python"))
    from forma_runtime import build, validate
    workspace, output, verified = [directory / name for name in ["workspace", "build", "verified"]]
    for folder in [workspace, output, verified, workspace / "parts"]:
        folder.mkdir(exist_ok=True)
    sources = {
        "plate": "import cadquery as cq\ndef build(p,d):\n return cq.Workplane('XY').box(p['width'],6,2)\n",
        "row": "import cadquery as cq\ndef build(p,d):\n a=cq.Assembly(name='row')\n"
               " for i in range(10): a.add(d['plate'],name=f'p{i}',loc=cq.Location((20*i,0,0)))\n return a\n",
        "root": "import cadquery as cq\ndef build(p,d):\n a=cq.Assembly(name='root')\n"
                " for g in range(6): a.add(d['row'],name=f'r{g}',loc=cq.Location((0,200*g,0),(0,0,90*(g%2))))\n return a\n"}
    definitions = []
    for cid, source in sources.items():
        (workspace / "parts" / f"{cid}.py").write_text(source)
        definitions.append({"id": cid, "name": cid, "source": f"parts/{cid}.py",
                            "kind": "solid" if cid == "plate" else "assembly",
                            "dependencies": {"plate": [], "row": ["plate"], "root": ["row"]}[cid],
                            "parameters": {"width": 10} if cid == "plate" else {}, "color": "#b8c9a5"})
    instances = [{"id": "root-instance", "definitionId": "root", "name": "Root", "parentId": None,
                  "frame": {"position": [0, 0, 0], "rotation": [0, 0, 0]}}]
    expected_origins = {}
    for g in range(6):
        frame = {"position": [0, 200*g, 0], "rotation": [0, 0, 90*(g % 2)]}
        instances.append({"id": f"r{g}", "definitionId": "row", "name": f"Row {g}",
                          "parentId": "root-instance", "frame": frame})
        for i in range(10):
            iid = f"r{g}-p{i}"
            instances.append({"id": iid, "definitionId": "plate", "name": iid, "parentId": f"r{g}",
                              "frame": {"position": [20*i, 0, 0], "rotation": [0, 0, 0]}})
            expected_origins[iid] = [20*i if g % 2 == 0 else 0,
                                     200*g + (20*i if g % 2 else 0), 0]
    manifest = {"schemaVersion": 1, "units": "mm", "rootComponentId": "root",
                "components": definitions, "instances": instances}
    json_write(workspace / "manifest.json", manifest)
    started = time.perf_counter()
    build(workspace, output)
    build_seconds = time.perf_counter() - started
    json_write(output / "manifest.json", manifest)
    started = time.perf_counter()
    validate(output, verified)
    validate_seconds = time.perf_counter() - started
    report = json.loads((verified / "report.json").read_text())
    inspection = report["inspection"]["configurations"][0]
    require(inspection["instanceCount"] == 60 and not inspection["inspectionErrors"],
            "Nested inspection incomplete")
    require(not inspection["interferences"], "Unexpected interference in separated rows")
    scene = trimesh.load(verified / "preview.glb", force="scene")
    require(set(scene.graph.nodes_geometry) == set(expected_origins), "Preview occurrence IDs missing")
    preview_errors = [float(np.linalg.norm(scene.graph[iid][0][:3, 3] - origin))
                      for iid, origin in expected_origins.items()]
    imported = cq.importers.importStep(str(verified / "root.step")).val()
    require(imported.isValid() and len(imported.Solids()) == 60, "Nested STEP invalid")
    unmatched = [s.Center() for s in imported.Solids()]
    step_errors = []
    for origin in expected_origins.values():
        center = cq.Vector(*origin)
        nearest = min(range(len(unmatched)), key=lambda j: (unmatched[j] - center).Length)
        step_errors.append((unmatched.pop(nearest) - center).Length)
    require(max(step_errors) < 1e-4 and max(preview_errors) < 1e-4, "Occurrence transform disagreement")
    return {"occurrences": 60, "subassemblies": 6, "uniquePartDefinitions": 1,
            "definitionCountIncludingAssemblies": 3, "manifestInstanceCountIncludingGroups": len(instances),
            "buildSeconds": build_seconds, "validateMeshAndInspectSeconds": validate_seconds,
            "pairEnumerationCount": 60 * 59 // 2, "inspectionErrors": len(inspection["inspectionErrors"]),
            "interferences": len(inspection["interferences"]), "maxStepCenterResidualMm": max(step_errors),
            "maxPreviewPositionResidualMm": max(preview_errors),
            "previewBytes": (verified / "preview.glb").stat().st_size,
            "rootStepBytes": (verified / "root.step").stat().st_size,
            "limit": "Actual Forma runtime build/validation with nested placements; source explicitly places parts, no manifest-joint solve"}


def forma_integration_negative(directory):
    import cadquery as cq
    sys.path.insert(0, str(REPO / "runtimes" / "python"))
    from forma_runtime import build, validate
    workspace, output = directory / "workspace", directory / "build"
    workspace.mkdir(exist_ok=True)
    output.mkdir(exist_ok=True)
    (workspace / "parts").mkdir(exist_ok=True)
    (workspace / "parts" / "plate.py").write_text(
        "import cadquery as cq\ndef build(p,d):\n return cq.Workplane('XY').box(10,10,2)\n")
    (workspace / "parts" / "group.py").write_text(
        "import cadquery as cq\ndef build(p,d):\n"
        " a=cq.Assembly(name='group')\n"
        " for i,x in enumerate([0,20,40]): a.add(d['plate'],name=f'p{i}',loc=cq.Location((x,0,0)))\n"
        " return a\n")
    definitions = [{"id": "plate", "name": "Plate", "source": "parts/plate.py", "kind": "solid",
                    "dependencies": [], "parameters": {}, "color": "#b8c9a5"},
                   {"id": "group", "name": "Group", "source": "parts/group.py", "kind": "assembly",
                    "dependencies": ["plate"], "parameters": {}, "color": "#b8c9a5"}]
    instances = [{"id": f"p{i}", "definitionId": "plate", "name": f"P{i}", "parentId": None,
                  "frame": {"position": [x, 0, 0], "rotation": [0, 0, 0]}}
                 for i, x in enumerate([0, 20, 40])]
    manifest = {"schemaVersion": 1, "units": "mm", "rootComponentId": "group",
                "components": definitions, "instances": instances,
                "joints": [{"id": "unsupported", "kind": "totally_unsupported_joint",
                            "referenceA": "a", "referenceB": "b"}]}
    json_write(workspace / "manifest.json", manifest)
    build(workspace, output)
    imported = cq.importers.importStep(str(output / "group.step")).val()
    require(len(imported.Solids()) == 3, "Fixture assembly did not build")
    json_write(output / "manifest.json", manifest)
    baseline = directory / "baseline-validation"
    baseline.mkdir(exist_ok=True)
    validate(output, baseline)
    # Keep outer parts and total bounds unchanged; move only the middle occurrence.
    manifest["instances"][1]["frame"]["position"][0] = 25
    json_write(output / "manifest.json", manifest)
    mismatch = directory / "mismatch-validation"
    mismatch.mkdir(exist_ok=True)
    try:
        validate(output, mismatch)
        mismatch_accepted = True
    except ValueError:
        mismatch_accepted = False
    # External-extent disagreement is the positive control of the existing check.
    manifest["instances"][2]["frame"]["position"][0] = 60
    json_write(output / "manifest.json", manifest)
    control = directory / "outer-mismatch-validation"
    control.mkdir(exist_ok=True)
    try:
        validate(output, control)
        outer_mismatch_rejected = False
    except ValueError as exc:
        outer_mismatch_rejected = "placements" in str(exc)
    require(outer_mismatch_rejected, "Existing extent guard failed positive control")
    return {"unsupportedJointDidNotBlockRuntimeBuild": True,
            "internalInstanceDisplacementMm": 5,
            "internalStepPreviewMismatchAccepted": mismatch_accepted,
            "outerExtentMismatchRejected": outer_mismatch_rejected,
            "scope": "Direct runtime entrypoints, not API contract/authentication test",
            "finding": "Manifest joint metadata is not centrally solved; bounds agreement does not prove every occurrence agrees"}


CASES = {"bspline_surface_and_step": spline_surface, "rational_nurbs_and_step": rational_nurbs,
         "static_two_part_mate": static_mate, "conflicting_mates": conflicting_mates,
         "underconstrained_mate": underconstrained_mate, "dimension_edits_and_references": edits_and_references,
         "static_60_occurrences": assembly_60, "forma_nested_60_occurrences": forma_nested_60,
         "forma_runtime_negative_controls": forma_integration_negative}


def worker(name):
    directory = ARTIFACTS / name
    directory.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    result = {"case": name, "versions": versions()}
    try:
        result.update(status="passed", measurements=CASES[name](directory))
    except Exception as exc:
        result.update(status="failed", errorType=type(exc).__name__, error=str(exc)[:1500],
                      traceback=traceback.format_exc()[-3000:])
    result["operationSecondsIncludingImports"] = time.perf_counter() - started
    try:
        import psutil
        memory = psutil.Process().memory_info()
        result["peakWorkingSetMiB"] = getattr(memory, "peak_wset", memory.rss) / 1024**2
    except Exception:
        result["peakWorkingSetMiB"] = None
    json_write(directory / "result.json", result)
    print(json.dumps({"case": name, "status": result["status"]}), flush=True)


def coordinator(selected):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    report_path = REPO / "docs" / "FORMA_LOCAL_ENGINE_TEST_RESULTS.json"
    previous = json.loads(report_path.read_text()) if report_path.exists() else {}
    report = {"reviewDate": "2026-10-01", "platform": platform.platform(), "python": sys.version,
              "pythonExecutable": sys.executable, "versions": versions(),
              "scope": "Reviewed synthetic local fixtures; no hosted deployment or production certification",
              "cases": {}}
    report["hashes"] = {path.relative_to(REPO).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in [Path(__file__), REPO / "runtimes/python/uv.lock",
                                     REPO / "runtimes/python/forma_runtime.py",
                                     REPO / "runtimes/python/geometry_inspection.py"]}
    report["cases"] = previous.get("cases", {})
    report["priorAttempts"] = previous.get("priorAttempts", [])
    if previous:
        report["priorAttempts"].append({"hashes": previous.get("hashes"),
                                        "cases": {k: v for k, v in previous.get("cases", {}).items() if k in selected},
                                        "note": "Preserved before test-harness refinement; initial 60-part invocation used unsupported unary-shape argument dispatch; NURBS refined to distinguish property integration from geometry"})
        for measured in report["cases"].values():
            measured.setdefault("harnessSha256", previous.get("hashes", {}).get("scripts/audit_native_cad.py")
                                or previous.get("hashes", {}).get("scripts\\audit_native_cad.py"))
    # Explicit allowlist avoids copying provider credentials to reviewed child tests.
    environment = {k: os.environ[k] for k in ["SystemRoot", "WINDIR", "PATH", "COMSPEC"] if k in os.environ}
    environment.update(TEMP=str(ARTIFACTS), TMP=str(ARTIFACTS), USERPROFILE=str(ARTIFACTS),
                       MPLBACKEND="Agg", OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
    for name in selected:
        directory = ARTIFACTS / name
        directory.mkdir(parents=True, exist_ok=True)
        result_file = directory / "result.json"
        # A separate result file on each run prevents old success masking a crash.
        if result_file.exists():
            result_file.unlink()
        started = time.perf_counter()
        timeout = 150 if name in ["static_60_occurrences", "forma_nested_60_occurrences", "dimension_edits_and_references"] else 90
        try:
            completed = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), "--worker", name],
                                       env=environment, cwd=directory, capture_output=True, text=True,
                                       timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            process = {"exitCode": completed.returncode, "timedOut": False,
                       "stdout": completed.stdout[-1200:], "stderr": completed.stderr[-2000:]}
        except subprocess.TimeoutExpired as exc:
            process = {"exitCode": None, "timedOut": True,
                       "stdout": (exc.stdout or b"").decode(errors="replace")[-1200:],
                       "stderr": (exc.stderr or b"").decode(errors="replace")[-2000:]}
        measured = json.loads(result_file.read_text()) if result_file.exists() else {"status": "no_result"}
        measured["harnessSha256"] = report["hashes"]["scripts/audit_native_cad.py"]
        measured["process"] = {**process, "elapsedSeconds": time.perf_counter() - started,
                               "timeoutSeconds": timeout}
        if process["timedOut"] or process["exitCode"] != 0:
            measured["processFailure"] = True
        report["cases"][name] = measured
        json_write(report_path, report)
        print(json.dumps({"case": name, "status": measured["status"], "process": measured["process"],
                          "measurements": measured.get("measurements"), "error": measured.get("error")}), flush=True)
    return report


def runtime_suite():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    test_paths = [REPO / "runtimes/python/tests" / name for name in
                  ["test_runtime.py", "test_geometry_inspection.py", "test_requirements.py"]]
    command = [sys.executable, "-m", "pytest", *map(str, test_paths), "-q",
               f"--junitxml={ARTIFACTS / 'existing-runtime-tests.xml'}"]
    environment = {k: os.environ[k] for k in ["SystemRoot", "WINDIR", "PATH", "COMSPEC"] if k in os.environ}
    environment.update(TEMP=str(ARTIFACTS), TMP=str(ARTIFACTS), USERPROFILE=str(ARTIFACTS), MPLBACKEND="Agg",
                       OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    started = time.perf_counter()
    try:
        result = subprocess.run(command, cwd=REPO, env=environment, capture_output=True, text=True,
                                timeout=180, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        summary = {"exitCode": result.returncode, "timedOut": False,
                   "stdout": result.stdout[-4000:], "stderr": result.stderr[-2000:]}
    except subprocess.TimeoutExpired as exc:
        summary = {"exitCode": None, "timedOut": True,
                   "stdout": (exc.stdout or b"").decode(errors="replace")[-4000:]}
    summary.update(elapsedSeconds=time.perf_counter() - started, timeoutSeconds=180,
                   testSourceHashes={p.relative_to(REPO).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in test_paths})
    report_path = REPO / "docs" / "FORMA_LOCAL_ENGINE_TEST_RESULTS.json"
    report = json.loads(report_path.read_text())
    if "existingRuntimeTests" in report:
        report.setdefault("priorRuntimeSuiteAttempts", []).append(report["existingRuntimeTests"])
    report["existingRuntimeTests"] = summary
    json_write(report_path, report)
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", choices=CASES)
    parser.add_argument("--cases", nargs="+", choices=CASES)
    parser.add_argument("--runtime-tests", action="store_true")
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
    elif args.runtime_tests:
        runtime_suite()
    else:
        coordinator(args.cases or list(CASES))
