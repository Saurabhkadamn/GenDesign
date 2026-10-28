"""Common evidence-aware context and model-capacity accounting for every role."""

from copy import deepcopy
from hashlib import sha256
import json
import os

from .agent_memory import estimate_tokens, fit_dialogue, memory_context


def input_budget(config: dict, output_tokens: int) -> int:
    # A conservative fallback is explicit, rather than pretending a model's window is known.
    capacity = int(config.get("context_length") or config.get("contextLength") or 65536)
    reserve = int(config.get("reasoning_token_reserve") or 0)
    margin = max(1024, capacity // 20)
    available = capacity - output_tokens - reserve - margin
    if available <= 0:
        raise ValueError(
            "The configured model has no input capacity after output/reasoning reservations"
        )
    override = os.getenv("FORMA_AGENT_CONTEXT_TOKENS")
    return min(available, int(override)) if override else available


def engineering_context(state: dict, snapshot: dict) -> dict:
    return {
        "originalRequest": state.get("original_request", ""),
        "projectContext": state.get("project_context") or {},
        "clarifiedRequest": state.get("clarified_request", ""),
        "explicitRequirements": state.get("requirements") or [],
        "cadRequest": state.get("engineering_request", ""),
        "triageAssumptions": state.get("engineering_assumptions") or [],
        "triageRemarks": state.get("engineering_remarks") or [],
        "candidateHash": state.get("candidate_hash"),
        "manifest": snapshot.get("manifest"),
        "files": sorted(snapshot.get("files", {})),
        "build": state.get("build_result"),
        "validation": state.get("validation"),
        "workingMemory": memory_context(state),
        "instruction": "Inspect the current candidate and measurements. Historical calculations are not current geometry evidence.",
    }


def fit_context(
    messages: list[dict], tools: list[dict], config: dict, output_tokens: int, safe_history
) -> list[dict]:
    # The context is always fitted for both tool and structured turns. Large reports
    # should be paged by tools, never silently sliced into apparently complete facts.
    return fit_dialogue(messages, tools, input_budget(config, output_tokens), safe_history)


async def turn_with_context_recovery(
    messages: list[dict], tools: list[dict], config: dict, output_tokens: int, safe_history, request
):
    """Retry only a confirmed prompt-size rejection, retaining complete tool pairs."""
    from .providers.openrouter import ModelFailure

    current = messages
    compactions = 0
    while True:
        try:
            result = await request(current)
            break
        except ModelFailure as exc:
            if exc.category != "context_overflow" or compactions >= 3:
                raise
            budget = (
                min(input_budget(config, output_tokens), estimate_tokens([current, tools])) // 2
            )
            try:
                fitted = fit_dialogue(current, tools, budget, safe_history)
            except ValueError:
                raise ModelFailure(
                    "context_overflow",
                    "The authoritative request and tool definitions exceed this model's context window. "
                    "Use a model with a larger window or narrow the task; no requirements were removed.",
                ) from None
            if fitted == current:
                raise
            current = fitted
            compactions += 1
    provenance = result.setdefault("provenance", {})
    provenance["effectivePromptDigest"] = sha256(
        json.dumps(current, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if compactions:
        retry = result.setdefault("provider_retry", {})
        retry["contextCompactions"] = compactions
        retry["reason"] = "confirmed_context_rejection"
    return result


def shared_context(state, context, memory=None):
    candidate = (
        (context.get("workspace") or context.get("currentWorkspace") or {}).get("candidateHash")
        or context.get("candidateHash")
        or state.get("candidate_hash", "")
    )
    return prompt_context(
        {
            **context,
            "acceptancePlan": state.get("acceptance_plan"),
            "researchMode": state.get("research_mode", False),
            "projectContext": context.get("projectContext")
            or context.get("project")
            or state.get("project_context", {}),
            "workingMemory": memory_context(
                {**state, "working_memory": memory or state.get("working_memory") or {}},
                candidate,
                max_outcomes=8,
                text_limit=1500,
            ),
        }
    )


def prompt_context(context: dict) -> dict:
    """Bound large historical source text with explicit, pageable references."""
    result = deepcopy(context)
    for key in ("projectContext", "project"):
        project = result.get(key)
        if not isinstance(project, dict):
            continue
        inputs = project.get("authoritativeInputs") or {}
        sources = inputs.get("sourceMessages") or []
        if estimate_tokens(inputs) < 8000 or not sources:
            continue
        inputs["sourceMessages"] = [
            {
                "id": source.get("id"),
                "characters": source.get("characters", len(source.get("content", ""))),
                "excerpt": source.get("content", source.get("excerpt", ""))[:240],
                "retrieval": "read_project_source",
                "requiresRetrieval": True,
            }
            for source in sources
        ]
        inputs["originalBrief"] = {
            "sourceId": sources[0].get("id"),
            "retrieval": "read_project_source",
            "contentInPrompt": False,
        }
        inputs["humanCorrections"] = {
            "sourceIds": [source.get("id") for source in sources[1:]],
            "retrieval": "read_project_source",
            "instruction": "Apply in order. Excerpts are not the complete requirements.",
        }
        inputs["instruction"] = (
            "Full authoritative text is preserved. Retrieve relevant source pages before planning or claiming complete requirement coverage."
        )
        project["previousMessages"] = [
            {"role": m["role"], "excerpt": m.get("content", "")[:240]}
            for m in project.get("previousMessages", [])
        ]
    return result
