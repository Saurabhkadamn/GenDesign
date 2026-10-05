import pytest
from forma_api.drawing_contracts import FeatureControlFrame, DrawingSheetSpec, DimensionSpec

PLANE = {"kind": "plane", "origin": [0, 0, 0], "direction": [0, 0, 1]}
CYLINDER = {**PLANE, "kind": "cylinder"}


def test_form_control_rejects_datums_and_material_modifier():
    with pytest.raises(ValueError, match="Form controls"):
        FeatureControlFrame(
            id="flat",
            viewId="front",
            characteristic="flatness",
            toleranceMm=0.1,
            reference=PLANE,
            datums=["A"],
        )
    with pytest.raises(ValueError, match="Material modifiers"):
        FeatureControlFrame(
            id="circular",
            viewId="front",
            characteristic="circularity",
            toleranceMm=0.1,
            reference=CYLINDER,
            materialCondition="MMC",
        )


def test_unknown_views_datums_and_basic_tolerances_rejected():
    with pytest.raises(ValueError, match="unknown drawing view"):
        DrawingSheetSpec(
            id="sheet",
            componentId="plate",
            dimensions=[{"id": "d", "kind": "extent", "viewId": "missing"}],
        )
    with pytest.raises(ValueError, match="undefined datum"):
        DrawingSheetSpec(
            id="sheet",
            componentId="plate",
            controls=[
                {
                    "id": "c",
                    "viewId": "top",
                    "characteristic": "position",
                    "reference": CYLINDER,
                    "toleranceMm": 0.1,
                    "datums": ["A"],
                }
            ],
        )
    with pytest.raises(ValueError, match="basic dimension"):
        DimensionSpec(
            id="d", kind="extent", viewId="top", basic=True, upperTolerance=0.1, lowerTolerance=0.1
        )
