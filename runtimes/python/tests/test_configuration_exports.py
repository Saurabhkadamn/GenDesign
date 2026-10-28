import json
import sys
from pathlib import Path

import cadquery as cq
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from forma_runtime import validate


def test_every_requested_configuration_is_exported_and_reopened(tmp_path):
    root, output = tmp_path / "input", tmp_path / "output"
    root.mkdir()
    output.mkdir()
    shape = cq.Workplane("XY").box(10, 4, 2).val()
    cq.exporters.export(shape, str(root / "part.step"))
    assembly = cq.Assembly(name="assembly")
    assembly.add(shape, name="one")
    assembly.export(str(root / "assembly.step"), exportType="STEP")
    manifest = {
        "schemaVersion": 1,
        "units": "mm",
        "rootComponentId": "assembly",
        "components": [
            {
                "id": "part",
                "name": "Part",
                "kind": "solid",
                "color": "#aabbcc",
                "parameters": {},
                "dependencies": [],
            },
            {
                "id": "assembly",
                "name": "Assembly",
                "kind": "assembly",
                "color": "#aabbcc",
                "parameters": {},
                "dependencies": ["part"],
            },
        ],
        "instances": [
            {
                "id": "one",
                "definitionId": "part",
                "parentId": None,
                "frame": {"position": [0, 0, 0], "rotation": [0, 0, 0]},
            }
        ],
        "configurations": [
            {
                "id": f"state_{i}",
                "name": str(angle),
                "frames": [
                    {
                        "instanceId": "one",
                        "frame": {"position": [0, 0, 0], "rotation": [0, 0, angle]},
                    }
                ],
            }
            for i, angle in enumerate([0, 5.29, 10.59])
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    validate(root, output)
    report = json.loads((output / "report.json").read_text())
    assert len(report["configurationExports"]) == 3
    for exported in report["configurationExports"]:
        assert exported["reopened"]
        assert exported["placement"]["validatedOccurrences"] == ["one"]
        reopened = cq.importers.importStep(str(output / exported["artifact"])).val()
        assert reopened.Volume() == pytest.approx(80)
    assert (
        cq.importers.importStep(str(output / "configuration-state_2.step"))
        .val()
        .BoundingBox()
        .ylen
        > 4
    )
