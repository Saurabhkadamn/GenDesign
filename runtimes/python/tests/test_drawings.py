import importlib.util
import math
from pathlib import Path

import cadquery as cq
import numpy as np
import pytest

spec = importlib.util.spec_from_file_location(
    "forma_test_drawings", Path(__file__).parents[1] / "drawings.py"
)
drawings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drawings)


def sheet_spec(**changes):
    return {
        "id": "plate",
        "componentId": "plate",
        "title": "Plate",
        "paper": "A3",
        "projection": "third_angle",
        "standard": "ISO",
        "scale": None,
        "views": [{"id": k, "kind": k} for k in ("front", "top", "right", "isometric")],
        "autoDimensions": True,
        "dimensions": [],
        "datums": [],
        "controls": [],
        "notes": [],
        "includeBom": False,
        **changes,
    }


def plate(thickness=4, hole=True):
    model = cq.Workplane("XY").box(32, 20, thickness)
    return (model.faces(">Z").workplane().hole(5) if hole else model).val()


def cylinder_ref():
    return {"kind": "cylinder", "origin": [0, 0, 0], "direction": [0, 0, 1]}


def test_native_projection_and_measures_are_analytic():
    sheet = sheet_spec()
    result = drawings.generate_sheet(
        plate(), {"id": "plate"}, sheet, {"candidate": "a"}
    )
    assert not result["issues"]
    by_id = {v["id"]: v for v in result["views"]}
    assert by_id["front"]["bounds"] == pytest.approx([-16, -2, 16, 2], abs=1e-6)
    assert by_id["top"]["bounds"] == pytest.approx([-16, -10, 16, 10], abs=1e-6)
    assert by_id["right"]["bounds"] == pytest.approx([-10, -2, 10, 2], abs=1e-6)
    diameter = [d for d in result["dimensions"] if d["kind"] == "diameter"]
    assert len(diameter) == 1 and diameter[0]["value"] == pytest.approx(5, abs=1e-9)
    assert result["manufacturingApproved"] is False


def test_dimension_survives_thickness_regeneration_and_missing_hole_fails():
    sheet = sheet_spec(
        autoDimensions=False,
        dimensions=[
            {
                "id": "bore",
                "viewId": "top",
                "kind": "diameter",
                "reference": cylinder_ref(),
            }
        ],
    )
    for thickness in (2, 4, 7):
        result = drawings.generate_sheet(plate(thickness), {"id": "plate"}, sheet, {})
        assert result["dimensions"][0]["value"] == pytest.approx(5, abs=1e-9)
    missing = drawings.generate_sheet(plate(hole=False), {"id": "plate"}, sheet, {})
    assert missing["status"] == "unresolved" and not missing["releaseEligible"]
    assert not missing["dimensions"] and "missing" in missing["issues"][0]["message"]


def test_coaxial_stepped_bore_is_ambiguous_not_first_face():
    model = (
        cq.Workplane("XY")
        .box(32, 20, 10)
        .faces(">Z")
        .workplane()
        .cboreHole(5, 9, 3)
        .val()
    )
    with pytest.raises(ValueError, match="ambiguous"):
        drawings.resolve_reference(drawings.feature_inventory(model), cylinder_ref())


def test_native_section_and_outside_cut():
    shape = cq.Workplane("XY").sphere(10).val()
    section = drawings.clipped_section(
        shape, {"sectionAxis": "Z", "sectionOffsetMm": 0}
    )
    assert section.Volume() == pytest.approx(2 / 3 * math.pi * 1000, abs=1e-6)
    with pytest.raises(ValueError, match="does not cross"):
        drawings.clipped_section(shape, {"sectionAxis": "Z", "sectionOffsetMm": 20})


def test_geometric_tolerance_is_intent_not_manufacturing_evidence():
    sheet = sheet_spec(
        datums=[
            {
                "label": "A",
                "viewId": "front",
                "reference": {
                    "kind": "plane",
                    "origin": [0, 0, -2],
                    "direction": [0, 0, 1],
                },
            }
        ],
        controls=[
            {
                "id": "position",
                "viewId": "top",
                "characteristic": "position",
                "reference": cylinder_ref(),
                "toleranceMm": 0.1,
                "datums": ["A"],
                "zone": "diameter",
            }
        ],
    )
    result = drawings.generate_sheet(plate(), {"id": "plate"}, sheet, {})
    assert not result["issues"]
    assert result["controls"][0]["conformance"] == "not_measured"
    assert result["controls"][0]["text"] == "⌖ | Ø0.1 | A"


def test_section_hatching_keeps_hole_empty():
    view = drawings.projected_edges(
        plate(),
        {"id": "cut", "kind": "section", "sectionAxis": "Z", "sectionOffsetMm": 0},
    )
    assert view["hatchLines"]
    for start, end in view["hatchLines"]:
        for t in np.linspace(0.001, 0.999, 30):
            p = np.array(start) * (1 - t) + np.array(end) * t
            assert np.linalg.norm(p) >= 2.5 - 1e-6


def test_automatic_hole_location_dimensions_are_native_distances():
    result = drawings.generate_sheet(plate(), {"id": "plate"}, sheet_spec(), {})
    positions = {
        d["axis"]: d["value"] for d in result["dimensions"] if d["kind"] == "distance"
    }
    assert positions == pytest.approx({"X": 16, "Y": 10})


def test_distance_along_cylinder_axis_is_rejected():
    with pytest.raises(ValueError, match="perpendicular"):
        drawings.measure_dimension(
            plate(),
            drawings.feature_inventory(plate()),
            {
                "kind": "distance",
                "axis": "Z",
                "reference": cylinder_ref(),
                "secondReference": {
                    "kind": "plane",
                    "origin": [0, 0, -2],
                    "direction": [0, 0, 1],
                },
            },
        )


def test_hole_diameter_edit_resolves_existing_reference_without_using_face_index():
    model=cq.Workplane('XY').box(32,20,4).faces('>Z').workplane().hole(7).val()
    result=drawings.generate_sheet(model,{'id':'plate'},sheet_spec(autoDimensions=False,
        dimensions=[{'id':'bore','viewId':'top','kind':'diameter','reference':cylinder_ref()}]),{})
    assert not result['issues'] and result['dimensions'][0]['value']==pytest.approx(7,abs=1e-8)
