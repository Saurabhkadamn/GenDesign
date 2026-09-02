"""Conservative extraction of explicit plate constraints; no guessing omitted dimensions."""
import re

from .contracts import Requirement


def explicit_requirements(message: str):
    text = message.lower().replace("−", "-")
    result = []
    dimensions = re.search(r"(\d+(?:\.\d+)?)\s*[×x]\s*(\d+(?:\.\d+)?)\s*[×x]\s*(\d+(?:\.\d+)?)\s*mm", text)
    if dimensions:
        result.append({"id": "request_dimensions", "kind": "dimensions", "description": "Requested overall dimensions", "dimensions": [float(x) for x in dimensions.groups()]})
    if re.search(r"cent(?:er|re)(?:ed|d)?\s+(?:at\s+)?(?:the\s+)?origin", text):
        result.append({"id": "request_center", "kind": "center", "description": "Centered at the origin", "center": [0, 0, 0]})
    diameter = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*(?:diameter|dia\b)", text)
    x = re.search(r"x\s*=\s*±\s*(\d+(?:\.\d+)?)\s*mm", text)
    y = re.search(r"y\s*=\s*±\s*(\d+(?:\.\d+)?)\s*mm", text)
    if diameter and x and y and re.search(r"through[ -]?holes?", text):
        result.append({"id": "request_holes", "kind": "through_holes", "description": "Four through-holes at the requested XY centers",
                       "diameter": float(diameter[1]), "count": 4, "positions": [[a * float(x[1]), b * float(y[1])] for a in (-1, 1) for b in (-1, 1)]})
    radius = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*radius", text)
    if radius and "corner" in text and re.search(r"\b(four|4)\b", text):
        result.append({"id": "request_corners", "kind": "corner_radius", "description": "Four rounded outer corners", "radius": float(radius[1]), "count": 4})
    if "plate" in text and dimensions:
        result.append({"id": "request_solid", "kind": "solid_count", "description": "One solid mounting plate", "count": 1})
    return [Requirement.model_validate(r).model_dump() for r in result]


def design_work_requested(message: str) -> bool:
    """Identify requests that require a CAD/calculation tool cycle.

    This deliberately stays conservative: ordinary greetings and conceptual
    questions may receive a direct coordinator answer, while an actionable
    part/assembly request may not be marked successful without delegation.
    """
    text = message.lower()
    action = re.search(r"\b(design|build|create|model|modify|make|add|round|drill)\b", text)
    object_ = re.search(r"\b(plate|bracket|part|component|assembly|motor|mount|hole|geometry|cad|solid)\b", text)
    return bool(action and object_) or bool(explicit_requirements(message))


def merge_requirements(message, supplied):
    # Request-derived values take priority over model-supplied values of the same kind.
    explicit = explicit_requirements(message)
    kinds = {x["kind"] for x in explicit}
    # The legacy extractor can only represent one XY hole pattern.  Complex
    # requests commonly contain separate motor and frame patterns; once triage
    # has described those patterns, preserve them instead of replacing them
    # with the extractor's first diameter/X/Y match.
    if any(r["kind"] == "through_holes" for r in supplied):
        explicit = [r for r in explicit if r["kind"] != "through_holes"]
        kinds.discard("through_holes")
    return explicit + [r for r in supplied if r["kind"] not in kinds]
