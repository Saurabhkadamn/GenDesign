"""Versioned drawing intent. Tolerance values express engineer input, not measured error."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Id = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]
Vector = tuple[float, float, float]
SheetOffset = tuple[Annotated[float, Field(ge=-60, le=60)], Annotated[float, Field(ge=-60, le=60)]]


class DrawingContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, json_schema_serialization_defaults_required=True
    )


class DrawingReference(DrawingContract):
    """Resolve by geometry, never a transient face/edge index."""

    kind: Literal["plane", "cylinder"]
    origin: Vector
    direction: Vector
    toleranceMm: float = Field(default=0.001, gt=0, le=0.01)

    @model_validator(mode="after")
    def direction_nonzero(self):
        if sum(x * x for x in self.direction) < 1e-16:
            raise ValueError("A reference direction cannot be zero.")
        return self


class DrawingView(DrawingContract):
    id: Id
    kind: Literal["front", "top", "right", "isometric", "section"]
    sectionAxis: Literal["X", "Y", "Z"] = "Y"
    sectionOffsetMm: float = 0
    hiddenLines: bool = True


class DimensionSpec(DrawingContract):
    id: Id
    viewId: Id
    kind: Literal["extent", "diameter", "radius", "distance", "angle"]
    axis: Literal["X", "Y", "Z"] = "X"
    reference: DrawingReference | None = None
    secondReference: DrawingReference | None = None
    upperTolerance: float | None = Field(default=None, ge=0, le=100)
    lowerTolerance: float | None = Field(default=None, ge=0, le=100)
    basic: bool = False
    offsetMm: float = Field(default=10, ge=5, le=60)

    @model_validator(mode="after")
    def references_required(self):
        if self.kind in {"diameter", "radius"} and (
            self.reference is None or self.reference.kind != "cylinder"
        ):
            raise ValueError("Diameter/radius requires a cylinder reference.")
        if self.kind in {"distance", "angle"} and (
            self.reference is None or self.secondReference is None
        ):
            raise ValueError("Distance/angle requires two references.")
        if self.kind == "angle" and (
            self.reference.kind != "plane" or self.secondReference.kind != "plane"
        ):
            raise ValueError("Angular dimensions require two planar references.")
        if self.basic and (self.upperTolerance is not None or self.lowerTolerance is not None):
            raise ValueError("A basic dimension cannot have size tolerances.")
        if (self.upperTolerance is None) != (self.lowerTolerance is None):
            raise ValueError("Provide both upper and lower tolerance values.")
        return self


class DatumSpec(DrawingContract):
    label: str = Field(pattern=r"^[A-Z]{1,2}$")
    viewId: Id
    reference: DrawingReference
    offset: SheetOffset = (-14, 12)


class FeatureControlFrame(DrawingContract):
    id: Id
    viewId: Id
    characteristic: Literal[
        "flatness",
        "straightness",
        "circularity",
        "cylindricity",
        "parallelism",
        "perpendicularity",
        "angularity",
        "position",
        "profile_surface",
        "profile_line",
        "circular_runout",
        "total_runout",
    ]
    reference: DrawingReference
    toleranceMm: float = Field(gt=0, le=100)
    datums: list[str] = Field(default_factory=list, max_length=3)
    zone: Literal["diameter", "linear"] = "linear"
    materialCondition: Literal["none", "MMC", "LMC"] = "none"
    offset: SheetOffset = (12, -12)

    @model_validator(mode="after")
    def supported_combinations(self):
        form = self.characteristic in {"flatness", "straightness", "circularity", "cylindricity"}
        if form and self.datums:
            raise ValueError("Form controls do not reference datums.")
        if (
            self.characteristic
            in {
                "parallelism",
                "perpendicularity",
                "angularity",
                "position",
                "circular_runout",
                "total_runout",
            }
            and not self.datums
        ):
            raise ValueError("This control requires engineer-defined datums.")
        if len(set(self.datums)) != len(self.datums):
            raise ValueError("A datum cannot repeat in a control frame.")
        if self.materialCondition != "none" and not (
            self.characteristic == "position"
            and self.reference.kind == "cylinder"
            and self.zone == "diameter"
        ):
            raise ValueError(
                "Material modifiers are currently supported only for cylindrical position controls."
            )
        if self.characteristic == "flatness" and self.reference.kind != "plane":
            raise ValueError("Flatness must reference a planar feature.")
        if (
            self.characteristic
            in {"circularity", "cylindricity", "circular_runout", "total_runout"}
            and self.reference.kind != "cylinder"
        ):
            raise ValueError("This supported control requires a cylindrical feature.")
        return self


def default_views():
    return [DrawingView(id=k, kind=k) for k in ("front", "top", "right", "isometric")]


class DrawingSheetSpec(DrawingContract):
    id: Id
    componentId: Id
    title: str = Field(default="Engineering drawing", min_length=1, max_length=160)
    paper: Literal["A4", "A3"] = "A3"
    projection: Literal["first_angle", "third_angle"] = "third_angle"
    standard: Literal["ASME", "ISO"] = "ISO"
    scale: float | None = Field(default=None, ge=0.001, le=100)
    views: list[DrawingView] = Field(default_factory=default_views, min_length=1, max_length=8)
    autoDimensions: bool = True
    dimensions: list[DimensionSpec] = Field(default_factory=list, max_length=100)
    datums: list[DatumSpec] = Field(default_factory=list, max_length=20)
    controls: list[FeatureControlFrame] = Field(default_factory=list, max_length=100)
    notes: list[Annotated[str, Field(max_length=300)]] = Field(default_factory=list, max_length=20)
    includeBom: bool = False

    @model_validator(mode="after")
    def unique_references(self):
        views = {v.id for v in self.views}
        if len(views) != len(self.views):
            raise ValueError("Duplicate drawing view ID.")
        for name, items in (("dimension", self.dimensions), ("control", self.controls)):
            if len({item.id for item in items}) != len(items):
                raise ValueError(f"Duplicate {name} ID.")
        for item in [*self.dimensions, *self.datums, *self.controls]:
            if item.viewId not in views:
                raise ValueError("Annotation references an unknown drawing view.")
        labels = {d.label for d in self.datums}
        if len(labels) != len(self.datums):
            raise ValueError("Duplicate drawing datum label.")
        for item in self.controls:
            if not set(item.datums).issubset(labels):
                raise ValueError("Control frame references an undefined datum.")
        return self


class DrawingRequest(DrawingContract):
    baseRevisionId: UUID
    idempotencyKey: UUID
    sheets: list[DrawingSheetSpec] = Field(min_length=1, max_length=50)


class DrawingSubmission(DrawingContract):
    runId: UUID
