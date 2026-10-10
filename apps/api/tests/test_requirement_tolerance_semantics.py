import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError

from forma_api.contracts import Requirement
from forma_api.tools import parse_tool


def test_missing_hole_count_feedback_names_the_field_without_inferring_it():
    with pytest.raises(ValidationError, match="through_holes requires explicit values for: count"):
        Requirement(id="bores", description="Two bores", kind="through_holes",
                    diameter=38.1, positions=[[-17, 0], [17, 0]])


@pytest.mark.parametrize("kind, values", [
    ("dimensions", {"dimensions": [10, 20, 30]}),
    ("max_dimensions", {"dimensions": [10, 20, 30]}),
    ("center", {"center": [0, 0, 0]}),
    ("through_holes", {"count": 1, "diameter": 4, "positions": [[0, 0]]}),
    ("corner_radius", {"count": 4, "radius": 2}),
])
@pytest.mark.parametrize("tolerance", [0, .5])
def test_measured_geometry_keeps_original_precision_limits(kind, values, tolerance):
    with pytest.raises(ValidationError, match="positive millimetre tolerance of at most 0.1"):
        Requirement(id="geometry", description="Owned geometry check", kind=kind,
                    tolerance=tolerance, **values)


def test_captured_pump_delegation_accepts_non_metric_metadata_without_loosening_count():
    parsed = parse_tool("coordinator", "delegate", {"role": "cad", "task": "Build the pump.",
        "requirements": [
            {"id": "solid_count", "description": "Exactly 27 pieces", "kind": "solid_count",
             "count": 27, "tolerance": 2},
            {"id": "draft", "description": "Draft >=1 degree", "kind": "unverified", "tolerance": 1},
            {"id": "fillets", "description": "Fillets >=2 mm", "kind": "unverified", "tolerance": 2},
            {"id": "wall", "description": "Wall >=4 mm", "kind": "unverified", "tolerance": 4}]})
    assert parsed.requirements[0].count == 27
    assert parsed.requirements[0].tolerance == 0
    assert all(r.kind == "unverified" for r in parsed.requirements[1:])
    runtime = Path(__file__).resolve().parents[3] / "runtimes/python/requirements_check.py"
    spec = importlib.util.spec_from_file_location("tolerance_runtime_check", runtime)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Shape:
        def BoundingBox(self):
            return type("Bounds", (), {"xlen": 10, "ylen": 10, "zlen": 10})()

        def Solids(self):
            return [object()] * 26

    report = module.check_requirements({"pump": Shape()}, {"rootComponentId": "pump"},
                                      [r.model_dump() for r in parsed.requirements])
    assert report[0]["status"] == "failed"  # 26 never passes an exact 27, even with supplied tolerance=2
    assert all(r["status"] == "unverified" for r in report[1:])


@pytest.mark.parametrize("tolerance", [-1, float("inf"), float("nan")])
def test_unverified_metadata_still_rejects_invalid_numbers(tolerance):
    with pytest.raises(ValidationError):
        Requirement(id="note", description="Unverified check", kind="unverified", tolerance=tolerance)
