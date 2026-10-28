"""Engineering outcome is independent of successful execution or draft publication."""

from .execution import identity
from .acceptance_plan import artifact_coverage


def assess_completion(state: dict, snapshot: dict) -> dict:
    validation = state.get("validation") or {}
    report = validation.get("report") or {}
    requirements = state.get("requirements") or []
    current = validation.get("identity") == identity(snapshot, requirements)
    checks = {item["id"]: item for item in report.get("requirements", [])}
    outstanding = []
    if not requirements:
        outstanding.append(
            {
                "id": "requirement_coverage",
                "status": "unverified",
                "reason": "No complete engineering acceptance criteria were recorded.",
            }
        )
    for requirement in requirements:
        check = checks.get(requirement["id"], {})
        status = check.get("status", "unverified") if current else "stale"
        if status != "passed":
            outstanding.append(
                {
                    "id": requirement["id"],
                    "status": status,
                    "description": requirement.get("description", ""),
                }
            )
    review = state.get("review") or report.get("review") or {}
    for item in review.get("findings", []):
        if item.get("severity") in {"error", "warning"}:
            outstanding.append(
                {
                    "id": item.get("id", "review"),
                    "status": "failed",
                    "description": item.get(
                        "explanation",
                        item.get("message", item.get("description", "Review finding")),
                    ),
                }
            )
    plan = state.get("acceptance_plan") or {}
    deliverables = artifact_coverage(plan, validation)
    for item in deliverables:
        if item["status"] != "verified" or not current:
            outstanding.append({"id": item.get("id", "deliverable"), "status": "unverified"})
    sources = ((state.get("project_context") or {}).get("authoritativeInputs") or {}).get(
        "sourceMessages"
    ) or []
    covered_sources = {source["id"] for source in sources} <= set(plan.get("sourceIds") or [])
    covered_requirements = {r["id"] for r in requirements} == {
        r["requirement"]["id"] for r in plan.get("requirements", [])
    }
    reviewed = (
        bool(plan.get("version"))
        and review.get("coveragePlanVersion") == plan["version"]
        and review.get("coverageComplete") is True
        and state.get("reviewed_candidate_hash") == identity(snapshot, requirements)["candidate"]
    )
    if not (deliverables and covered_sources and covered_requirements and reviewed):
        outstanding.append(
            {
                "id": "deliverable_coverage",
                "status": "unverified",
                "reason": "Requested deliverables have not all been mapped to verified artifacts.",
            }
        )
    satisfied = bool(current and not outstanding)
    return {
        "taskOutcome": "satisfied" if satisfied else "incomplete",
        "acceptanceState": "ready_for_review" if satisfied else "draft",
        "outstanding": outstanding,
        "deliverables": deliverables,
        "currentEvidence": current,
    }
