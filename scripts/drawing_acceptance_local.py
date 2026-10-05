"""Generate native drawing acceptance evidence through the fresh STEP validator."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtimes/python"))
sys.path.insert(0, str(ROOT / "apps/api"))


def main(folder):
    import cadquery as cq
    from forma_api.contracts import Snapshot
    from forma_api.drawing_contracts import DrawingSheetSpec
    from forma_runtime import validate

    inputs = folder / "accepted-step"
    outputs = folder / "verified"
    inputs.mkdir(parents=True, exist_ok=True)
    outputs.mkdir(parents=True, exist_ok=True)
    cylinder = {"kind": "cylinder", "origin": [0, 0, 0], "direction": [0, 0, 1]}
    plane = {"kind": "plane", "origin": [0, 0, -2], "direction": [0, 0, 1]}
    sheet = DrawingSheetSpec(
        id="plate",
        componentId="plate",
        title="Plate - native drawing acceptance",
        datums=[{"label": "A", "viewId": "front", "reference": plane}],
        controls=[
            {
                "id": "position",
                "viewId": "top",
                "characteristic": "position",
                "reference": cylinder,
                "toleranceMm": 0.1,
                "zone": "diameter",
                "materialCondition": "MMC",
                "datums": ["A"],
            }
        ],
        notes=[
            "Acceptance fixture only. Tolerance intent requires engineering review."
        ],
    ).model_dump()
    section = DrawingSheetSpec(
        id="section",
        componentId="plate",
        title="Plate - native section",
        autoDimensions=False,
        views=[
            {
                "id": "section",
                "kind": "section",
                "sectionAxis": "Z",
                "sectionOffsetMm": 0,
                "hiddenLines": False,
            }
        ],
    ).model_dump()
    snapshot = Snapshot.model_validate(
        {
            "files": {
                "parts/plate.py": 'raise RuntimeError("Drawing generation must not execute source")'
            },
            "manifest": {
                "components": [
                    {
                        "id": "plate",
                        "name": "Acceptance plate",
                        "source": "parts/plate.py",
                        "kind": "solid",
                        "partMetadata": {
                            "partNumber": "QUAL-PLATE",
                            "revision": "TEST",
                        },
                    }
                ],
                "rootComponentId": "plate",
                "drawings": [sheet, section],
            },
        }
    ).model_dump()
    digest = lambda value: hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    expected = {
        "candidate": digest(snapshot),
        "requirements": digest([]),
        "runtime": "local-native-drawing-qualification",
    }
    shape = cq.Workplane("XY").box(32, 20, 4).faces(">Z").workplane().hole(5)
    cq.exporters.export(shape, str(inputs / "plate.step"))
    for name, value in [
        ("manifest", snapshot["manifest"]),
        ("requirements", []),
        ("identity", expected),
    ]:
        (inputs / (name + ".json")).write_text(json.dumps(value), encoding="utf-8")
    validate(inputs, outputs)
    report = json.loads((outputs / "report.json").read_text(encoding="utf-8"))
    assert report["identity"] == expected
    assert report["drawings"]["status"] == "draft", report["drawings"]["sheets"]
    assert len([a for a in report["artifacts"] if a["kind"] == "drawing"]) == 7
    print(
        json.dumps(
            {
                "status": "passed",
                "nativeDimensionsMm": report["components"]["plate"]["dimensions"],
                "sheets": len(report["drawings"]["sheets"]),
                "drawingArtifacts": 7,
                "output": str(outputs),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", type=Path, required=True)
    main(parser.parse_args().report_dir.resolve())
