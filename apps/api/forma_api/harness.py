"""General harness state and scoped evidence; no generated geometry or design recipes."""
from copy import deepcopy

from .contracts import Snapshot
from .execution import digest, identity


def component_snapshot(snapshot: dict, component_id: str) -> dict:
    definitions = {item["id"]: item for item in snapshot["manifest"]["components"]}
    if component_id not in definitions:
        raise ValueError("Unknown component ID. Choose an ID from the saved manifest.")
    if definitions[component_id]["kind"] == "assembly":
        raise ValueError("Use build(final=false) for assembly milestones. componentId probes test individual parts/surfaces.")
    selected = set()

    def visit(cid):
        if cid in selected:
            return
        selected.add(cid)
        for dependency in definitions[cid].get("dependencies", []):
            visit(dependency)

    visit(component_id)
    # This is a temporary execution input. Never save it as the project candidate.
    # Keep source helpers available and invalidate probes on any source edit.
    return Snapshot.model_validate({"files": deepcopy(snapshot["files"]), "manifest": {
        "components": [deepcopy(item) for item in snapshot["manifest"]["components"] if item["id"] in selected],
        "rootComponentId": component_id,
    }}).model_dump()


def acceptance_context(state: dict, snapshot: dict) -> dict:
    requirements = state.get("requirements") or []
    validation = state.get("validation") or {}
    current = validation.get("identity") == identity(snapshot, requirements)
    checks = {item["id"]: item for item in (validation.get("report") or {}).get("requirements", [])} if current else {}
    return {"currentCandidateHash": digest(snapshot), "currentValidation": current,
            "requirements": [{"requirement": item, "status": (checks.get(item["id"]) or {}).get("status", "unverified"),
                              "evidence": (checks.get(item["id"]) or {}).get("evidence", {})} for item in requirements],
            "instruction": "This is the proposed measurable subset, not a replacement for the exact human request. "
                           "Track every other requested deliverable in working tasks. Do not treat bounding boxes as "
                           "shaft spacing, bore diameter, clearance or tolerance chains. Unsupported checks stay unverified."}


def probe_context(state: dict, snapshot: dict) -> list[dict]:
    result = []
    for cid, probe in (state.get("component_probes") or {}).items():
        try:
            current = identity(component_snapshot(snapshot, cid), probe.get("requirements", [])) == probe.get("identity")
        except ValueError:
            current = False
        result.append({"componentId": cid, "currentSource": current, "ok": probe.get("ok"),
                       "identity": probe.get("identity"), "error": probe.get("error"),
                       "measurementAvailable": bool((probe.get("report") or {}).get("inspection")),
                       "instruction": "Part evidence only. Full assembly and requested configurations still need validation."})
    return result


def recovery_key(state: dict, snapshot: dict) -> str:
    repair = state.get("cad_contract_repair") or {}
    error = (state.get("build_result") or {}).get("error") or {}
    category = repair.get("category") or error.get("category") or "tool_action"
    issues = sorted((item.get("code", ""), item.get("path", "")) for item in repair.get("issues", []))
    return digest({"candidate": digest(snapshot), "category": category, "issues": issues})
