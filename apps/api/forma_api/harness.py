"""General harness state and scoped evidence; no generated geometry or design recipes."""
import json
from copy import deepcopy

from .contracts import Snapshot
from .execution import digest, identity


MAX_OBSERVATION_ONLY_DECISIONS_PER_EPOCH = 3


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
        report = probe.get("report") or {}
        result.append({"componentId": cid, "currentSource": current, "ok": probe.get("ok"),
                       "identity": probe.get("identity"), "error": probe.get("error"),
                       "measurementAvailable": bool(report.get("inspection")),
                       "measurements": report.get("components") or {},
                       "requirementChecks": report.get("requirements") or [],
                       "allRequirementsVerified": report.get("allRequirementsVerified", False),
                       "instruction": "ok describes CAD integrity only. Failed requirement checks still need attention; "
                                      "stale measurements are historical. Part evidence cannot validate the assembly."})
    return result


def observation_epoch(state: dict, snapshot: dict) -> str:
    """New source, evidence or human guidance renews the observation window."""
    return digest({"candidate": digest(snapshot), "validation": state.get("validation") or {},
                   "probes": state.get("component_probes") or {},
                   "rejected": state.get("rejected_files") or {},
                   "request": state.get("clarified_request") or state.get("original_request", "")})


def observation_context(state: dict, snapshot: dict) -> dict:
    progress = state.get("cad_observation_progress") or {}
    if progress.get("epoch") != observation_epoch(state, snapshot):
        progress = {}
    return {"reviewed": list((progress.get("seen") or {}).values()),
            "repeatedObservations": progress.get("repeats", 0),
            "repeatedDecisions": progress.get("repeatDecisions", 0),
            "observationOnlyDecisions": progress.get("observationOnlyDecisions", 0),
            "maximumObservationOnlyDecisions": MAX_OBSERVATION_ONLY_DECISIONS_PER_EPOCH,
            "instruction": "These reads/inspections already completed for unchanged source and evidence. "
                           "Re-read when needed, then use the result for a focused edit or test. "
                           "After three observation-only model decisions for unchanged evidence, the available tools "
                           "require a focused edit, part test, assembly build, engineering request, or concrete question. "
                           "Cycling through the same observations triggers a fresh repair plan, not a part lock."}


def record_observation_action(state: dict, snapshot: dict, name: str, arguments: dict,
                              *, decision: int | None = None) -> dict:
    """Recognize nonconsecutive observation cycles even after dialogue eviction."""
    epoch = observation_epoch(state, snapshot)
    progress = deepcopy(state.get("cad_observation_progress") or {})
    if progress.get("epoch") != epoch:
        progress = {"epoch": epoch, "seen": {}, "repeats": 0}
    key = digest({"tool": name, "arguments": arguments})
    seen = progress["seen"]
    window_seen = progress.setdefault("windowSeen", dict.fromkeys(seen, True))
    repeated = key in window_seen
    progress["repeats"] = progress["repeats"] + 1 if repeated else 0
    # A provider may return many reads in one generation. Give it the results
    # before treating that single decision as multiple unsuccessful attempts.
    if repeated:
        marker = decision if decision is not None else f"observation:{progress['repeats']}"
        if marker != progress.get("lastRepeatedDecision"):
            progress["repeatDecisions"] = progress.get("repeatDecisions", 0) + 1
        progress["lastRepeatedDecision"] = marker
    else:
        progress.update(repeatDecisions=0, lastRepeatedDecision=None)
    seen[key] = {"tool": name, "arguments": arguments}
    window_seen[key] = True
    # Bound metadata only; source and evidence remain in their own stores.
    for receipts in (seen, window_seen):
        if len(receipts) > 256:
            del receipts[next(iter(receipts))]
    return progress


def record_observation_decision(progress: dict, *, batch_complete: bool) -> dict:
    """Count one model decision after all provider-returned observation actions finish."""
    progress = deepcopy(progress)
    if batch_complete:
        progress["observationOnlyDecisions"] = progress.get("observationOnlyDecisions", 0) + 1
    return progress


def recovery_key(state: dict, snapshot: dict) -> str:
    repair = state.get("cad_contract_repair") or {}
    error = state.get("cad_last_build_failure") or (state.get("build_result") or {}).get("error") or {}
    category = repair.get("category") or error.get("category") or "tool_action"
    issues = sorted((item.get("code", ""), item.get("path", "")) for item in repair.get("issues", []))
    return digest({"candidate": digest(snapshot), "category": category,
                   "failureFile": error.get("file"), "failureLine": error.get("line"),
                   "failureSignature": error.get("signature"), "issues": issues})


def recovery_source_context(state: dict, snapshot: dict, *, paths: list[str] | None = None,
                            max_bytes: int = 48_000, max_files: int = 16) -> dict:
    """Retrieve bounded current source after dialogue reset; never promote it to evidence."""
    selected = list(paths if paths is not None else state.get("recovery_source_paths") or [])
    if paths is None:
        for message in state.get("cad_history") or []:
            if message.get("role") != "tool":
                continue
            try:
                result = json.loads(message.get("content") or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(result, dict):
                continue
            path = result.get("path")
            if (result.get("ok") is True and result.get("saved") is True
                    and isinstance(path, str) and path in snapshot["files"]
                    and result.get("content") == snapshot["files"][path]):
                selected = [item for item in selected if item != path] + [path]
    files, omitted, size = {}, [], 0
    for path in reversed(list(dict.fromkeys(selected))):
        source = snapshot["files"].get(path)
        if not isinstance(source, str) or not path.endswith(".py") or path.startswith("calculations/"):
            continue
        entry_size = len(json.dumps({path: source}, ensure_ascii=False).encode("utf-8"))
        if len(files) >= max_files or size + entry_size > max_bytes:
            omitted.append(path)
            continue
        files[path] = source
        size += entry_size
    return {"candidateHash": digest(snapshot), "files": files, "omittedPaths": omitted,
            "instruction": "Current saved source for paths selected from completed reads. This is source context, "
                           "not build or geometry evidence. Use it directly for a focused edit or test; do not "
                           "re-read every file after recovery. Other source remains available through read_file. "
                           "Earlier missing-source errors may be obsolete; compare them with the current file inventory."}


def focused_recovery_paths(state: dict, snapshot: dict, *, limit: int = 4) -> list[str]:
    """Select source tied to the current failure instead of replaying the whole workspace."""
    available = snapshot.get("files") or {}
    paths: list[str] = []

    def add(value):
        if isinstance(value, str) and value in available and value.endswith(".py") and value not in paths:
            paths.append(value)

    for key in ("build_result", "cad_contract_repair"):
        evidence = state.get(key) or {}
        error = evidence.get("error") or evidence
        if not isinstance(error, dict):
            error = {}
        add(error.get("file"))
        add(error.get("path"))
        location = error.get("location") or {}
        if isinstance(location, dict):
            add(location.get("file"))
        for issue in evidence.get("issues") or []:
            if not isinstance(issue, dict):
                continue
            add(issue.get("path"))
            issue_location = issue.get("location") or {}
            if isinstance(issue_location, dict):
                add(issue_location.get("file"))
    remembered = state.get("recovery_source_paths") or []
    if len(remembered) <= limit:
        for path in remembered:
            add(path)
    add(state.get("last_read_path"))
    candidate = digest(snapshot)
    changed = (state.get("working_memory") or {}).get("changedFiles") or {}
    for path, details in changed.items():
        if details.get("candidateHash") == candidate:
            add(path)
    for message in reversed(state.get("cad_history") or []):
        if message.get("role") != "tool":
            continue
        try:
            result = json.loads(message.get("content") or "{}")
        except (TypeError, ValueError):
            continue
        if (result.get("ok") is True and result.get("saved") is True
                and result.get("content") == available.get(result.get("path"))):
            add(result.get("path"))
            break
    return paths[:limit]
