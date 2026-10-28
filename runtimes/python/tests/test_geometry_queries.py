import sys
from pathlib import Path

import cadquery as cq
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from geometry_queries import measure, resolve
from geometry_inspection import candidate_pairs, _box_gap
from forma_runtime import trusted_support


def test_analytic_axis_measurements_are_derived_from_shapes():
    shapes = {
        "a": cq.Workplane("XY").circle(8).extrude(20).val(),
        "b": cq.Workplane("XY").circle(8).extrude(20).val().translate((34, 0, 0)),
    }
    request = {
        "operation": "axis_separation",
        "a": {"componentId": "a", "entity": {"kind": "cylinder", "radiusMm": 8}},
        "b": {"componentId": "b", "entity": {"kind": "cylinder", "radiusMm": 8}},
    }
    result = measure(shapes, {"instances": []}, request, trusted_support)
    assert result["value"] == pytest.approx(34)
    assert result["axisAngleDegrees"] == pytest.approx(0)
    request["operation"] = "distance"
    assert measure(shapes, {"instances": []}, request, trusted_support)[
        "value"
    ] == pytest.approx(18)


def test_reference_ambiguity_and_absence_do_not_guess():
    shape = cq.Workplane("XY").box(20, 20, 10).val()
    with pytest.raises(ValueError, match="6 faces"):
        resolve(shape, {"kind": "plane"})
    with pytest.raises(ValueError, match="0 faces"):
        resolve(shape, {"kind": "cylinder"})


def test_selected_pair_interference():
    shapes = {
        "a": cq.Workplane("XY").box(10, 10, 10).val(),
        "b": cq.Workplane("XY").box(10, 10, 10).val().translate((8, 0, 0)),
    }
    result = measure(
        shapes,
        {"instances": []},
        {
            "operation": "interference",
            "a": {"componentId": "a"},
            "b": {"componentId": "b"},
        },
        trusted_support,
    )
    assert result["value"] == pytest.approx(200)


def test_spatial_candidates_match_exhaustive_bounds():
    import random

    rng = random.Random(17)
    physical = []
    for _ in range(300):
        point = [rng.uniform(-100, 100) for _ in range(3)]
        physical.append({"boundsMm": point + [p + rng.uniform(0.1, 20) for p in point]})
    expected = [
        (a, b)
        for a in range(len(physical))
        for b in range(a + 1, len(physical))
        if _box_gap(physical[a]["boundsMm"], physical[b]["boundsMm"]) <= 5
    ]
    assert candidate_pairs(physical) == expected
    assert len(expected) < 300 * 299 // 2
