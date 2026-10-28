"""Exact interval arithmetic over declared, sourced quantities; no design recipes."""

import ast
from fractions import Fraction
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract

# SI scale and (length, mass, time) dimensions. Unsupported units fail explicitly.
UNITS = {
    "1": ("1", (0, 0, 0)),
    "mm": ("0.001", (1, 0, 0)),
    "cm": ("0.01", (1, 0, 0)),
    "m": ("1", (1, 0, 0)),
    "in": ("0.0254", (1, 0, 0)),
    "kg": ("1", (0, 1, 0)),
    "g": ("0.001", (0, 1, 0)),
    "s": ("1", (0, 0, 1)),
    "mm2": ("0.000001", (2, 0, 0)),
    "m2": ("1", (2, 0, 0)),
    "mm3": ("0.000000001", (3, 0, 0)),
    "cc": ("0.000001", (3, 0, 0)),
    "m3": ("1", (3, 0, 0)),
    "N": ("1", (1, 1, -2)),
    "Pa": ("1", (-1, 1, -2)),
    "MPa": ("1000000", (-1, 1, -2)),
    "bar": ("100000", (-1, 1, -2)),
    "N*m": ("1", (2, 1, -2)),
}


class IntervalInput(Contract):
    nominal: float
    minimum: float
    maximum: float
    unit: str
    sourceRef: str = Field(min_length=1, max_length=500)
    nominalBasis: Literal["basic", "modeled", "specified"] = "specified"

    @model_validator(mode="after")
    def valid_interval(self):
        if self.minimum > self.maximum:
            raise ValueError("minimum must not exceed maximum")
        if self.nominalBasis == "modeled" and not self.minimum <= self.nominal <= self.maximum:
            raise ValueError("A modeled nominal must be inside its declared interval")
        return self


class IntervalCalculation(Contract):
    formula: str = Field(min_length=1, max_length=4000)
    inputs: dict[str, IntervalInput] = Field(min_length=1, max_length=100)
    outputUnit: str
    requiredMinimum: float | None = None
    requiredMaximum: float | None = None


def quantity(value):
    if value.unit not in UNITS:
        raise ValueError(f"Unsupported unit: {value.unit}")
    scale, dimension = UNITS[value.unit]
    return tuple(
        Fraction(str(x)) * Fraction(scale) for x in (value.nominal, value.minimum, value.maximum)
    ) + (dimension,)


def calculate_intervals(value: IntervalCalculation) -> dict:
    """Conservative interval enclosure; repeated symbols retain interval dependency limits."""
    values = {key: quantity(item) for key, item in value.inputs.items()}
    tree = ast.parse(value.formula, mode="eval")
    if len(list(ast.walk(tree))) > 500:
        raise ValueError("Formula is too large for this arithmetic operation")

    def evaluate(node):
        if isinstance(node, ast.Name) and node.id in values:
            return values[node.id]
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            f = Fraction(str(node.value))
            return f, f, f, (0, 0, 0)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            n, lo, hi, dim = evaluate(node.operand)
            return (-n, -hi, -lo, dim) if isinstance(node.op, ast.USub) else (n, lo, hi, dim)
        if not isinstance(node, ast.BinOp):
            raise ValueError("Use named inputs, numeric constants and +, -, *, / only")
        a, alo, ahi, ad = evaluate(node.left)
        b, blo, bhi, bd = evaluate(node.right)
        if isinstance(node.op, (ast.Add, ast.Sub)):
            if ad != bd:
                raise ValueError("Cannot add or subtract incompatible units")
            if isinstance(node.op, ast.Add):
                return a + b, alo + blo, ahi + bhi, ad
            return a - b, alo - bhi, ahi - blo, ad
        if isinstance(node.op, ast.Mult):
            corners = [x * y for x in (alo, ahi) for y in (blo, bhi)]
            return a * b, min(corners), max(corners), tuple(x + y for x, y in zip(ad, bd))
        if isinstance(node.op, ast.Div):
            if blo <= 0 <= bhi or b == 0:
                raise ValueError("Divisor interval or nominal includes zero")
            corners = [x / y for x in (alo, ahi) for y in (blo, bhi)]
            return a / b, min(corners), max(corners), tuple(x - y for x, y in zip(ad, bd))
        raise ValueError("Unsupported arithmetic operator")

    nominal, minimum, maximum, dim = evaluate(tree.body)
    if value.outputUnit not in UNITS or UNITS[value.outputUnit][1] != dim:
        raise ValueError("Output unit is unsupported or dimensionally incompatible")
    scale = Fraction(UNITS[value.outputUnit][0])
    nominal, minimum, maximum = (x / scale for x in (nominal, minimum, maximum))
    lower = Fraction(str(value.requiredMinimum)) if value.requiredMinimum is not None else None
    upper = Fraction(str(value.requiredMaximum)) if value.requiredMaximum is not None else None
    if lower is not None and upper is not None and lower > upper:
        raise ValueError("Required interval is inverted")
    passed = (lower is None or minimum >= lower) and (upper is None or maximum <= upper)
    return {
        "formula": value.formula,
        "inputs": value.model_dump()["inputs"],
        "unit": value.outputUnit,
        "nominal": float(nominal),
        "minimum": float(minimum),
        "maximum": float(maximum),
        "exact": {"nominal": str(nominal), "minimum": str(minimum), "maximum": str(maximum)},
        "requiredMinimum": value.requiredMinimum,
        "requiredMaximum": value.requiredMaximum,
        "status": ("passed" if passed else "failed")
        if lower is not None or upper is not None
        else "unverified",
        "basis": "Calculated from supplied intervals; not a geometry measurement.",
        "method": "Exact rational interval enclosure; repeated variables may overestimate the range.",
    }
