"""Independent STEP/BOM oracle for the reviewed 60-plate release fixture.

Run in the CAD audit environment. This reads downloaded artifacts, constructs
the analytic plate/grid independently, and does not invoke Forma or its solver.
"""
import argparse
import csv
import json
import math
from pathlib import Path

import cadquery as cq


def check(folder, thickness):
    plate = cq.importers.importStep(str(folder / "plate.step")).val()
    assembly = cq.importers.importStep(str(folder / "assembly.step")).val()
    assert plate.isValid() and assembly.isValid()
    solids = assembly.Solids()
    assert len(solids) == 60
    ideal = cq.Workplane("XY").box(8, 8, thickness).faces(">Z").workplane().hole(2).val()
    expected = sorted(((i % 10) * 12, (i // 10) * 12, 0) for i in range(60))
    solids.sort(key=lambda s: (round(s.Center().x, 6), round(s.Center().y, 6), round(s.Center().z, 6)))
    worst_difference = 0.0
    for solid, center in zip(solids, expected):
        measured = solid.Center()
        assert max(abs(a - b) for a, b in zip([measured.x, measured.y, measured.z], center)) < 1e-6
        placed = ideal.moved(cq.Location(cq.Vector(*center)))
        difference = solid.cut(placed).Volume(tol=1e-9) + placed.cut(solid).Volume(tol=1e-9)
        assert difference < 1e-4
        worst_difference = max(worst_difference, difference)
    bounds = plate.BoundingBox()
    assert max(abs(a - b) for a, b in zip([bounds.xlen, bounds.ylen, bounds.zlen], [8, 8, thickness])) < 1e-6
    assert plate.cut(ideal).Volume(tol=1e-9) + ideal.cut(plate).Volume(tol=1e-9) < 1e-4
    volume_error = abs(assembly.Volume(tol=1e-9) - 60 * (64 - math.pi) * thickness)
    assert volume_error < 1e-3
    bom = json.loads((folder / "bom.json").read_text(encoding="utf-8"))
    assert len(bom["flat"]) == 1 and bom["flat"][0]["quantity"] == 60
    assert bom["flat"][0]["partNumber"] == "QUAL-PLATE" and bom["flat"][0]["revision"] == "TEST"
    rows = list(csv.DictReader((folder / "bom-flat.csv").read_text(encoding="utf-8-sig").splitlines()))
    assert len(rows) == 1 and int(rows[0]["quantity"]) == 60
    return {"passed": True, "solidChecks": 60, "plateDimensionsMm": [8, 8, thickness],
            "holeDiameterMm": 2, "occurrenceCentersMatch": True,
            "maxSymmetricDifferenceMm3": worst_difference, "downloadedStepVolumeErrorMm3": volume_error}


def main(args):
    state_path = args.report_dir / "application-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    for key, case in state["cases"].items():
        if case.get("status") != "passed": continue
        thickness = int(key.split("-")[-1].removesuffix("mm"))
        case["independentGeometry"] = check(args.report_dir / key, thickness)
        print(key + " independent STEP geometry, 60 placements and BOM passed")
    if args.chat:
        assert state.get("chatWorkflow") == "passed", "The chat workflow must pass before its oracle"
        state["chatIndependentGeometry"] = check(args.report_dir / "chat-3mm", 3)
        print("Chat STEP independently passed all 60 geometry/placement and BOM checks")
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--chat", action="store_true")
    main(parser.parse_args())
