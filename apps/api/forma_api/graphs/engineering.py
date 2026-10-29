"""An iterative engineering session using the same candidate and evidence as CAD."""

from uuid import UUID

from pydantic import ValidationError

from ..agent_context import engineering_context
from ..execution import digest
from ..tools import model_tools, parse_tool
from ..research_tools import SPECS, execute as execute_research_tool
from ..acceptance_plan import state_update


def return_to_design(state, snapshot, usage, summary: str, *, completed: bool) -> dict:
    """Return a specialist result to its owner without implying engineering acceptance."""
    from . import design as graph

    result = {
        "ok": completed,
        "summary": summary,
        "calculation": state.get("engineering_calculation"),
        "candidateHash": digest(snapshot),
        "requiresUserInput": False,
    }
    if not completed:
        result.update(
            category="engineering_no_action",
            instruction="Continue useful CAD work and request a focused engineering task when evidence changes.",
        )
    pending = state.get("pending_cad_call")
    coordinator = (
        state.get("coordinator_pending_call")
        if state.get("engineering_from_coordinator")
        else None
    )
    return {
        **usage,
        "phase": "coordinator_session" if coordinator else "cad_session",
        "engineering_history": [],
        "engineering_summary": summary,
        "engineering_candidate_hash": digest(snapshot),
        **({"engineering_no_action_fingerprint": state["engineering_request_fingerprint"]}
           if not completed and state.get("engineering_request_fingerprint") else {}),
        "pending_cad_call": {},
        "engineering_from_coordinator": False,
        **(
            {
                "coordinator_pending_call": {},
                "coordinator_task": state.get("clarified_request")
                or state["original_request"],
                "coordinator_history": graph.bounded_history(
                    [
                        *state.get("coordinator_history", []),
                        graph.tool_message(coordinator, result),
                    ]
                ),
            }
            if coordinator
            else {}
        ),
        **(
            {
                "cad_history": graph.bounded_history(
                    [*state.get("cad_history", []), graph.tool_message(pending, result)]
                )
            }
            if pending
            else {}
        ),
    }


async def step(state):
    # Deferred import avoids a second state machine or a circular module initialization.
    from . import design as graph

    snapshot = await graph.run_service.load_candidate(state["run_id"])
    run = await graph.run_row(state)
    history = state.get("engineering_history") or [
        {
            "role": "user",
            "content": state.get("engineering_request")
            or state.get("clarified_request")
            or state["original_request"],
        }
    ]
    tools = model_tools("engineering")
    call, history, usage = await graph.agent_tool_turn(
        state,
        model_role="engineering",
        prompt_role="engineering",
        node="engineering-session",
        context=engineering_context(state, snapshot),
        history=history,
        tools=tools,
    )
    common = {**usage, "phase": "engineering_analysis", "engineering_history": history}
    if not call:
        if state.get("harness_version", 3) >= 4:
            return return_to_design(
                state, snapshot, usage,
                "Engineering produced no executable action. Calculations and geometry checks remain unverified; "
                "continue the design from the saved candidate and revisit engineering with a focused task.",
                completed=False,
            )
        return {
            **common,
            "engineering_history": graph.bounded_history(
                [
                    *history,
                    {
                        "role": "user",
                        "content": "Choose a tool to inspect, calculate, repair or finish with the recorded limitations.",
                    },
                ]
            ),
        }
    name = call["name"]
    try:
        value = parse_tool("engineering", name, call["input"]).model_dump()
        if name == "finish":
            # Finishing reports the session outcome; successful execution is never described as proof.
            return return_to_design(state, snapshot, usage, value["message"], completed=True)
        if name == "ask_user":
            return {
                **common,
                "phase": "approval",
                "question": value["question"],
                "approval_summary": value["question"],
            }
        if name in SPECS:
            result = await execute_research_tool(run, snapshot, name, value, state)
            common.update(state_update(result, state))
        elif name == "inspect_geometry":
            result = {
                "ok": True,
                "candidateHash": digest(snapshot),
                "validation": state.get("validation"),
                "build": state.get("build_result"),
                "instruction": "Verify identity before using an older measurement.",
            }
        else:
            cp = graph.checkpoint_view(state, snapshot)
            cp["role"] = "engineering"
            if name == "calculate" and not cp.get("sandbox"):
                # Engineering may run before any CAD build. Use a stable
                # per-run identity so interrupted preparation can be retried
                # without inventing a different external environment.
                cp["sandbox"] = f"forma-{UUID(str(run['id'])).hex}-engineering"
            key = f"engineering:{name}:{digest({'input': value, 'candidate': digest(snapshot)})}"

            async def execute():
                try:
                    result = await graph.execute_tool(
                        run,
                        cp,
                        {"id": call["id"], "name": name, "input": value},
                        await graph.app_settings(),
                        graph.worker(),
                    )
                except (ValueError, ValidationError) as exc:
                    # A rejected edit or invalid calculation path has no
                    # external effect. Commit its feedback as a completed
                    # receipt instead of stranding a started operation.
                    result = {
                        "ok": False,
                        "category": "engineering_tool",
                        "message": str(exc),
                        "instruction": "Correct the source or tool input, then retry.",
                    }
                return {"result": result, "checkpoint": cp}

            receipt = await graph.operation(
                run,
                key,
                name,
                execute,
                idempotent=name in {"read_file", "search_files", "apply_changes"},
            )
            result, cp = receipt["result"], receipt["checkpoint"]
            common.update(graph.sync_checkpoint(cp))
            if name == "apply_changes" and cp["snapshot"] != snapshot:
                new_hash = digest(cp["snapshot"])
                await graph.run_service.save_candidate(run["id"], cp["snapshot"], new_hash)
                common.update(
                    candidate_hash=new_hash,
                    validation={},
                    build_result={},
                    engineering_calculation=None,
                )
            if name == "calculate":
                common["engineering_calculation"] = result
                result = {**result, "executionDoesNotProveCorrectness": True}
                if result.get("ok"):
                    from ..project_record import append_record

                    record = await append_record(
                        run,
                        "evidence",
                        "calculation:"
                        + digest({"candidate": digest(snapshot), "path": value["path"]}),
                        {
                            **result,
                            "sourcePath": value["path"],
                            "sourceHash": digest(snapshot["files"][value["path"]]),
                        },
                        candidate_hash=digest(snapshot),
                    )
                    result = {**result, "recordId": record["id"]}
    except (ValueError, ValidationError) as exc:
        result = {
            "ok": False,
            "category": "engineering_tool",
            "message": str(exc),
            "instruction": "Inspect inputs or source, correct the hypothesis, then rerun the calculation.",
        }
    common["engineering_history"] = graph.bounded_history(
        [*history, graph.tool_message(call, result)]
    )
    return common
