import math

import cadquery as cq
import pytest

from requirements_check import check_requirements


def requirements():
    return [
        {"id": "size", "description": "80 × 50 × 6 mm", "kind": "dimensions", "dimensions": [80, 50, 6]},
        {"id": "origin", "description": "Centered at origin", "kind": "center", "center": [0, 0, 0]},
        {"id": "solid", "description": "One solid", "kind": "solid_count", "count": 1},
        {"id": "holes", "description": "Four Ø6 through-holes", "kind": "through_holes", "count": 4,
         "diameter": 6, "positions": [[x, y] for x in (-30, 30) for y in (-15, 15)]},
        {"id": "corners", "description": "Four R3 outer corners", "kind": "corner_radius", "count": 4, "radius": 3},
    ]


def plate(hole=6, depth=None, fillet=3):
    return (cq.Workplane("XY").box(80, 50, 6).edges("|Z").fillet(fillet)
            .faces(">Z").workplane().pushPoints([(x, y) for x in (-30, 30) for y in (-15, 15)])
            .hole(hole, depth).val())


def check(shape, values=None):
    return check_requirements({"plate": shape}, {"rootComponentId": "plate"}, values or requirements())


def test_exact_plate_step_roundtrip(tmp_path):
    path = tmp_path / "plate.step"
    cq.exporters.export(plate(), str(path))
    shape = cq.importers.importStep(str(path)).val()
    results = check(shape)
    assert all(c["status"] == "passed" for c in results), results
    assert shape.Volume() == pytest.approx((80 * 50 - 4 * (9 - math.pi * 9 / 4) - 4 * math.pi * 9) * 6)


@pytest.mark.parametrize("shape,failed", [(lambda: plate(hole=5), "holes"),
    (lambda: plate(depth=3), "holes"), (lambda: plate(fillet=2), "corners"),
    (lambda: plate().translate((1, 0, 0)), "origin")])
def test_wrong_geometry_does_not_pass(shape, failed):
    assert next(c for c in check(shape()) if c["id"] == failed)["status"] == "failed"


def test_unsupported_is_unverified():
    assert check(plate(), [{"id": "material", "description": "Aluminum alloy", "kind": "unverified"}])[0]["status"] == "unverified"


def test_y_axis_frame_holes_are_checked_in_the_xz_plane():
    shape = (cq.Workplane("XY").box(40, 6, 30).faces(">Y").workplane()
             .pushPoints([(-10, -8), (-10, 8), (10, -8), (10, 8)]).hole(4).val())
    result = check_requirements(
        {"frame": shape}, {"rootComponentId": "frame"},
        [{"id": "frame_holes", "description": "Four frame holes", "kind": "through_holes",
          "axis": "Y", "count": 4, "diameter": 4,
          "positions": [[x, z] for x in (-10, 10) for z in (-8, 8)]}],
    )
    assert result[0]["status"] == "passed", result
