"""Evaluate saved, sourced check definitions against the exact current candidate."""

from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, SafeId
from .engineering_math import IntervalCalculation, calculate_intervals
from .execution import digest, identity
from .geometry_queries import ShapeReference, GeometryQuery, measure_geometry
from .project_record import append_record


class IntervalCheck(Contract):
    type: Literal["interval"]
    calculation: IntervalCalculation


class GeometryCheck(Contract):
    type: Literal["geometry"]
    operation: Literal["distance", "angle", "axis_separation", "cylinder_diameter", "interference"]
    a: ShapeReference
    b: ShapeReference | None = None
    configurationId: SafeId = "as_built"
    unit: Literal["mm", "deg", "mm3"]
    requiredMinimum: float | None = Field(default=None, allow_inf_nan=False)
    requiredMaximum: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def bounds(self):
        if self.requiredMinimum is None and self.requiredMaximum is None:
            raise ValueError("A check needs at least one explicit required bound")
        if (
            self.requiredMinimum is not None
            and self.requiredMaximum is not None
            and self.requiredMinimum > self.requiredMaximum
        ):
            raise ValueError("Required bounds are inverted")
        return self


class EvaluateRequirement(Contract):
    requirementId: SafeId
    acceptancePlanVersion: str


async def evaluate_requirement(run, snapshot, state, value):
    plan = state.get("acceptance_plan") or {}
    if plan.get("version") != value.acceptancePlanVersion:
        raise ValueError(
            "Acceptance plan changed; retrieve its current interpretation before evaluating"
        )
    binding = next(
        (
            item
            for item in plan.get("requirements", [])
            if item["requirement"]["id"] == value.requirementId
        ),
        None,
    )
    if not binding or not binding.get("check"):
        raise ValueError(
            "No qualified check definition is recorded for this requirement; it remains unverified"
        )
    expected = identity(snapshot, state.get("requirements") or [])
    if (state.get("validation") or {}).get("identity") != expected:
        raise ValueError("Validate the current candidate before binding engineering checks to it")
    spec = binding["check"]
    if spec["type"] == "interval":
        calculation = IntervalCheck.model_validate(spec).calculation
        if not {v.sourceRef for v in calculation.inputs.values()} <= set(binding["sourceIds"]):
            raise ValueError(
                "Every tolerance input must refer to one of this requirement’s original sources"
            )
        result = calculate_intervals(calculation)
        status = result["status"]
    elif spec["type"] == "geometry":
        check = GeometryCheck.model_validate(spec)
        query = GeometryQuery(
            candidateHash=expected["candidate"],
            **check.model_dump(
                exclude={"type", "unit", "requiredMinimum", "requiredMaximum"}, exclude_none=True
            ),
        )
        receipt = await measure_geometry(run, snapshot, state, query)
        if not receipt.get("ok"):
            return receipt
        result = receipt["measurement"]
        if result.get("identity") != expected or result.get("unit") != check.unit:
            raise ValueError("Geometry receipt identity or units do not match this check")
        import math

        measured = result["value"]
        if (
            isinstance(measured, bool)
            or not isinstance(measured, (int, float))
            or not math.isfinite(measured)
        ):
            raise ValueError("Geometry check requires a finite scalar measurement")
        passed = (check.requiredMinimum is None or measured >= check.requiredMinimum) and (
            check.requiredMaximum is None or measured <= check.requiredMaximum
        )
        status = "passed" if passed else "failed"
    else:
        raise ValueError("Unsupported engineering method; requirement remains unverified")
    record = await append_record(
        run,
        "evidence",
        "requirement:"
        + digest({"identity": expected, "plan": plan["version"], "id": value.requirementId}),
        {
            "identity": expected,
            "status": status,
            "definition": spec,
            "result": result,
            "planVersion": plan["version"],
        },
        candidate_hash=expected["candidate"],
        requirement_id=value.requirementId,
        source_ids=binding["sourceIds"],
    )
    return {
        "ok": True,
        "requirementIdentity": expected,
        "recordId": record["id"],
        "requirementCheck": {
            "id": value.requirementId,
            "description": binding["requirement"]["description"],
            "kind": spec["type"],
            "status": status,
            "evidence": {
                "recordId": record["id"],
                "method": spec["type"],
                "sourceIds": binding["sourceIds"],
                "planVersion": plan["version"],
                "result": result,
            },
        },
    }
