"""Return stalled specialist work to the lead without losing its evidence."""

import json

from ..execution import digest
from ..project_record import append_record


async def return_to_lead(state, snapshot):
    from . import design as graph

    attempt = state.get("recovery_plan_count", 0) + 1
    result = {
        "ok": False,
        "candidateHash": digest(snapshot),
        "activeFailure": graph.active_failure_context(state, snapshot),
        "workspaceRepair": state.get("cad_contract_repair") or {},
        "observationProgress": state.get("cad_observation_progress") or {},
        "lastAction": state.get("final_message") or "",
        "instruction": "The specialist has not resolved this task. Retrieve the relevant source and prior experiments, reconsider the cause, then choose a scoped experiment, coherent edit or independent task. All read and diagnostic tools remain available. Existing files and evidence are preserved; no engineering requirement has been passed by this handoff.",
    }
    record = await append_record(
        await graph.run_row(state),
        "experiment",
        f"recovery:{state['run_id']}:{attempt}",
        result,
        candidate_hash=digest(snapshot),
    )
    result["recordId"] = record["id"]
    pending = state.get("coordinator_pending_call")
    response = (
        graph.tool_message(pending, result)
        if pending
        else {"role": "user", "content": "Saved recovery evidence: " + json.dumps(result)}
    )
    await graph.repo.event(
        state["run_id"],
        "The lead agent is reconsidering the unresolved task using saved source and failure evidence.",
        stage="recovery",
    )
    return {
        "phase": "coordinator_session",
        "coordinator_pending_call": {},
        "coordinator_history": graph.bounded_history(
            [*state.get("coordinator_history", []), response]
        ),
        "coordinator_task": state.get("clarified_request") or state["original_request"],
        "coordinator_actions": 0,
        "recovery_plan_count": attempt,
        "cad_invalid_tool_attempts": 0,
        "cad_pending_actions": [],
        "cad_observation_progress": {},
        "final_message": "",
    }
