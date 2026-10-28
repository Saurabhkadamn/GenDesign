"""General project and arithmetic tools shared by all v4 sessions."""

from .engineering_math import IntervalCalculation, calculate_intervals
from .execution import digest
from .geometry_queries import GeometryQuery, measure_geometry
from .acceptance_plan import AcceptancePlan, ReadAcceptancePlan, current_plan, record_plan
from .engineering_reports import CreateEngineeringReports, create_reports
from .requirement_evidence import EvaluateRequirement, evaluate_requirement
from .project_record import (
    ReadProjectRecords,
    RecordProjectFact,
    ReadProjectSource,
    read_source,
    read_records,
    record_fact,
    append_record,
)

SPECS = {
    "read_acceptance_plan": (
        ReadAcceptancePlan,
        "Retrieve the current committed project acceptance plan, including its version and original source references. Use after a stale-plan response or when continuing another run.",
    ),
    "evaluate_requirement": (
        EvaluateRequirement,
        "Run the saved sourced interval or geometry check for one requirement and current candidate. Cannot change the required limits while checking. Failed, missing and unsupported checks remain visible.",
    ),
    "create_engineering_reports": (
        CreateEngineeringReports,
        "Create a calculation notebook, tolerance report, measured-versus-calculated table and inventory from recorded current evidence. Uses receipt IDs, never manually supplied measured values. Report files remain drafts; engineering checks keep their real outcomes.",
    ),
    "record_acceptance_plan": (
        AcceptancePlan,
        "Map every original source and correction to requirements and named deliverables. Use the current acceptancePlan version as baseVersion (null for the first plan). Every revision or retirement of an existing item requires an explicit sourced amendment. Include unsupported checks as unverified. Recording an interpretation never passes a check.",
    ),
    "read_project_source": (
        ReadProjectSource,
        "Read a complete original brief, correction or document in text pages. Continue until nextOffset is null when reconstructing the full specification.",
    ),
    "measure_geometry": (
        GeometryQuery,
        "Measure the current verified STEP in a fresh source-free worker. Inventory faces before selecting them. Face indices are candidate-specific. Missing or ambiguous references fail explicitly.",
    ),
    "read_project_records": (
        ReadProjectRecords,
        "Page full original sources, requirements, decisions, experiments and evidence. Never assume a compact context contains all historical requirements.",
    ),
    "record_project_fact": (
        RecordProjectFact,
        "Append a sourced requirement/decision or an experimental hypothesis/task. Use a new key and supersedes for amendments. Agent notes never certify geometry.",
    ),
    "calculate_intervals": (
        IntervalCalculation,
        "Calculate conservative tolerance bounds with exact rational arithmetic and explicit units and source references. Not a geometry measurement.",
    ),
}


async def execute(run, snapshot, name, value, state=None):
    parsed = SPECS[name][0].model_validate(value)
    if name == "read_acceptance_plan":
        return {"ok": True, "acceptancePlan": await current_plan(run["project_id"])}
    if name == "evaluate_requirement":
        return await evaluate_requirement(run, snapshot, state or {}, parsed)
    if name == "create_engineering_reports":
        return await create_reports(run, snapshot, state or {}, parsed)
    if name == "record_acceptance_plan":
        return await record_plan(run, parsed, state or {})
    if name == "read_project_source":
        return {"ok": True, **await read_source(run["project_id"], parsed)}
    if name == "measure_geometry":
        result = await measure_geometry(run, snapshot, state or {}, parsed)
        if result.get("ok"):
            record = await append_record(
                run,
                "evidence",
                "measurement:" + digest({"candidate": digest(snapshot), "query": value}),
                result,
                candidate_hash=digest(snapshot),
            )
            result = {**result, "recordId": record["id"]}
        return result
    if name == "read_project_records":
        return {"ok": True, **await read_records(run["project_id"], parsed)}
    if name == "record_project_fact":
        return {"ok": True, "record": await record_fact(run, parsed, digest(snapshot))}
    result = calculate_intervals(parsed)
    key = "interval:" + digest({"candidate": digest(snapshot), "input": value})
    record = await append_record(run, "evidence", key, result, candidate_hash=digest(snapshot))
    return {"ok": True, "recordId": record["id"], "result": result}
