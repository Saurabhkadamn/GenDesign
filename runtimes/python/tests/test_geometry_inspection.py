import sys
from pathlib import Path

import cadquery as cq
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from geometry_inspection import inspect_project, surface_inventory


def component(identifier, material=None):
    value = {
        "id": identifier,
        "name": identifier,
        "source": f"parts/{identifier}.py",
        "kind": "solid",
        "dependencies": [],
        "parameters": {},
        "color": "#b9c4ad",
    }
    if material:
        value["material"] = material
    return value


def instance(identifier, definition, x=0):
    return {
        "id": identifier,
        "definitionId": definition,
        "parentId": None,
        "name": identifier,
        "frame": {"position": [x, 0, 0], "rotation": [0, 0, 0]},
    }


def test_surface_inventory_reports_exact_torus():
    shape = cq.Solid.makeTorus(15, 3)
    assert surface_inventory(shape)["torus"] == 1


def test_project_inspection_finds_interference_and_configuration_clearance():
    shape = cq.Workplane("XY").box(10, 10, 10).val()
    manifest = {
        "components": [component("block")],
        "instances": [instance("left", "block"), instance("right", "block", 5)],
        "rootComponentId": None,
        "references": [],
        "joints": [],
        "featureOperations": [],
        "configurations": [{
            "id": "separated",
            "name": "Separated",
            "description": "Move the right block away",
            "frames": [{
                "instanceId": "right",
                "frame": {"position": [20, 0, 0], "rotation": [0, 0, 0]},
            }],
        }],
    }
    report = inspect_project({"block": shape}, manifest)
    as_built, separated = report["configurations"]
    assert as_built["interferences"][0]["instances"] == ["left", "right"]
    assert as_built["interferences"][0]["volumeMm3"] == pytest.approx(500)
    assert as_built["interferences"][0]["percentOfSmaller"] == pytest.approx(50)
    assert separated["interferences"] == []
    assert separated["boundsMm"] == pytest.approx([-5, -5, -5, 25, 5, 5])


def test_project_inspection_reports_mass_only_with_density():
    shape = cq.Workplane("XY").box(100, 100, 100).val()
    material = {"name": "example", "densityKgM3": 2700}
    manifest = {
        "components": [component("block", material)],
        "instances": [instance("block_1", "block")],
        "rootComponentId": "block",
        "references": [],
        "joints": [],
        "featureOperations": [],
        "configurations": [],
    }
    report = inspect_project({"block": shape}, manifest)
    assert report["components"]["block"]["massKg"] == pytest.approx(2.7)
    assert report["configurations"][0]["massKg"] == pytest.approx(2.7)
