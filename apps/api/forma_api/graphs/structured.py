"""One structured model attempt per durable step, with immutable raw receipts."""

from copy import deepcopy
import json
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from ..agent_context import fit_context, shared_context, turn_with_context_recovery
from ..execution import digest
from ..project_record import append_record
from ..services.receipts import generation as save_generation


class StructuredRetry(Exception):
    def __init__(self, update):
        super().__init__("Structured response needs correction")
        self.update = update


async def structured_step(state, role, node, prompt, tool_name, contract, *, web=False):
    from . import design as graph

    run = await graph.run_row(state)
    config = await graph.models.configuration(role)
    ordinal = state.get("model_calls", 0)
    cycle = state.get("model_calls_this_cycle", ordinal)
    if (
        not state.get("research_mode")
        and cycle >= (await graph.app_settings()).limits.maxModelCalls
    ):
        raise graph.Pause(
            "The model-call limit for this cycle was reached. Saved work is preserved."
        )
    tools = graph.provider_tools(
        config, [graph.submission_tool(tool_name, f"Submit the {node} result.", contract)]
    )
    context = shared_context(
        state,
        {
            "task": prompt,
            "candidateHash": state.get("candidate_hash"),
            "requirements": state.get("requirements", []),
            "measurements": state.get("validation", {}),
        },
    )
    recovery = state.get("structured_recovery") or {}
    identity = digest(
        {
            "node": node,
            "prompt": prompt,
            "candidate": state.get("candidate_hash"),
            "plan": (state.get("acceptance_plan") or {}).get("version"),
        }
    )
    history = recovery.get("history", []) if recovery.get("identity") == identity else []
    messages = [
        {"role": "system", "content": graph.system_prompt(role)},
        {"role": "system", "content": "Current private design context: " + json.dumps(context)},
        {"role": "user", "content": state.get("clarified_request") or state["original_request"]},
        *history,
    ]
    output_tokens = graph.model_step_token_budget(config, node)
    messages = fit_context(messages, tools, config, output_tokens, graph.protocol_safe_history)

    async def call():
        async def request(fitted):
            return await graph.models.turn(
                config,
                fitted,
                tools,
                max_tokens=output_tokens,
                web_search=web and config.get("provider", "openrouter") == "openrouter",
                max_searches=max(0, 2 - state.get("search_count", 0)),
            )

        return await turn_with_context_recovery(
            messages, tools, config, output_tokens, graph.protocol_safe_history, request
        )

    result = await graph.operation(
        run,
        f"graph:{node}:{ordinal}",
        "model",
        call,
        input_identity={
            "messages": messages,
            "tools": tools,
            "model": config["model_id"],
            "configVersion": config["version"],
        },
    )
    await save_generation(
        run,
        {
            "id": str(uuid5(NAMESPACE_URL, f"{run['id']}:graph:{ordinal}")),
            "run_id": run["id"],
            "ordinal": ordinal,
            "role": role,
            "model_id": config["model_id"],
            "config_version": config["version"],
            "prompt_version": graph.PROMPT_VERSION,
            "status": "complete",
            "output": result["message"],
            "provenance": result.get("provenance", {}),
            "input_tokens": result["inputTokens"],
            "output_tokens": result["outputTokens"],
        },
    )
    usage = {
        "model_calls": ordinal + 1,
        "model_calls_this_cycle": cycle + 1,
        "search_count": state.get("search_count", 0) + result.get("webSearchRequests", 0),
    }
    try:
        calls = result.get("calls", [])
        if len(calls) != 1 or calls[0]["name"] != tool_name:
            raise ValueError(f"Return exactly one {tool_name} call with the complete typed result")
        value = contract.model_validate(calls[0]["input"])
    except (ValidationError, ValueError) as exc:
        feedback = (
            "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                for e in exc.errors(include_url=False, include_input=False)
            )
            if isinstance(exc, ValidationError)
            else str(exc)
        )
        await append_record(
            run,
            "event",
            f"structured-diagnostic:{run['id']}:{node}:{ordinal}",
            {
                "operationKey": f"graph:{node}:{ordinal}",
                "category": "tool_protocol",
                "diagnostic": feedback,
            },
            candidate_hash=state.get("candidate_hash"),
        )
        assistant = deepcopy(result["message"])
        responses = [
            {
                "role": "tool",
                "tool_call_id": c["id"],
                "content": json.dumps({"ok": False, "message": feedback}),
            }
            for c in assistant.get("tool_calls", [])
        ]
        history = graph.protocol_safe_history(
            [
                *history,
                assistant,
                *responses,
                {
                    "role": "user",
                    "content": "Inspect the typed contract and correct the result. " + feedback,
                },
            ]
        )
        raise StructuredRetry(
            {
                **usage,
                "structured_recovery": {
                    "identity": identity,
                    "history": history,
                    "diagnostic": feedback,
                    "operationKey": f"graph:{node}:{ordinal}",
                },
            }
        ) from None
    return value, {**usage, "structured_recovery": {}}
