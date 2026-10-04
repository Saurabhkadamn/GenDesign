"""Assembly invariants measured against an owned, immutable base revision."""
from copy import deepcopy
import re

from .execution import digest, identity


def normalize_assembly_requirements(requirements: list[dict], manifest: dict) -> list[dict]:
    """Repair legacy semantic bindings without deleting the requested invariant.

    A bounding-box centre cannot measure occurrence grounding, and a hole
    pattern cannot measure joints. Keep the requirement ID and description;
    use an exact assembly-structure comparison instead of either surrogate.
    """
    if not manifest.get("nativeAssembly"):
        return requirements
    result = []
    for requirement in requirements:
        item = deepcopy(requirement)
        description = item.get("description", "").lower()
        preserve = bool(re.search(r"\b(preserve|retain|keep|unchanged)\b", description))
        mistaken_ground = (item.get("kind") == "center"
                           and re.search(r"\bground(?:ed|ing)?\b", description)
                           and not re.search(r"\b(?:cent(?:er|re)(?:ed)?|centroid|mass)\b", description))
        mistaken_joints = (item.get("kind") == "through_holes"
                           and re.search(r"\b(joints?|mates?)\b", description)
                           and not re.search(r"\b(?:holes?|bores?|diameter|drill(?:ed)?)\b", description))
        if preserve and (mistaken_ground or mistaken_joints):
            item.update(kind="assembly_preservation", componentId=manifest.get("rootComponentId"),
                        center=None, dimensions=None, count=None, diameter=None, radius=None, positions=[])
        result.append(item)
    return result


def assembly_structure(manifest: dict) -> dict:
    """All manifest state except editable component parameter values.

    This includes occurrence frames, native settings, references, joints,
    configurations, engineering identities and BOM policies. Sort identified
    arrays so a presentation-only reorder cannot change the comparison.
    """
    value = deepcopy(manifest)
    for component in value.get("components", []):
        component.pop("parameters", None)
    for key, items in value.items():
        if isinstance(items, list) and all(isinstance(item, dict) and "id" in item for item in items):
            value[key] = sorted(items, key=lambda item: item["id"])
    return value


def check_assembly_preservation(report: dict, snapshot: dict, requirements: list[dict],
                                base: dict | None, base_revision_id: str | None) -> dict:
    """Supplement fresh STEP/native checks with a trusted revision comparison.

    No model supplies the baseline. Missing base or native evidence stays
    unverified; changed assembly structure fails. This never changes geometry
    measurements or promotes a failed native validation into a successful one.
    """
    report = deepcopy(report)
    requested = {item["id"]: item for item in requirements if item["kind"] == "assembly_preservation"}
    if not requested:
        return report
    native = report.get("nativeAssembly") or {}
    evidence_available = (base is not None and base_revision_id is not None
                          and report.get("identity") == identity(snapshot, requirements)
                          and snapshot["manifest"].get("nativeAssembly") is not None
                          and native.get("engine") == "OndselSolver"
                          and native.get("solvedFrames", 0) > 0)
    checks = {item["id"]: item for item in report.get("requirements", [])}
    for rid, requirement in requested.items():
        check = {"id": rid, "description": requirement["description"], "kind": "assembly_preservation",
                 "componentId": snapshot["manifest"].get("rootComponentId"),
                 "status": "unverified", "evidence": {"method": "owned base revision assembly comparison"}}
        if evidence_available:
            before, after = assembly_structure(base["manifest"]), assembly_structure(snapshot["manifest"])
            changed = sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))
            check["status"] = "failed" if changed else "passed"
            check["evidence"].update(baseRevisionId=base_revision_id, baselineHash=digest(before),
                                     candidateStructureHash=digest(after), changedSections=changed,
                                     groundedInstances=snapshot["manifest"]["nativeAssembly"]["groundedInstances"],
                                     instanceCount=len(snapshot["manifest"].get("instances", [])),
                                     jointCount=len(snapshot["manifest"].get("joints", [])),
                                     nativeSolvedFrames=native["solvedFrames"])
        else:
            check["evidence"]["reason"] = "An owned base revision and successful fresh native validation are required."
        checks[rid] = check
    report["requirements"] = list(checks.values())
    report["allRequirementsVerified"] = bool(checks) and all(item["status"] == "passed" for item in checks.values())
    return report
