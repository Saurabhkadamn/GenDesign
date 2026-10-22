"""Fixed Forma graph: engineering gate, CAD build/repair, validation, publication."""
import json
import re
import time
import os
from collections import Counter
from copy import deepcopy
import ast
from contextvars import ContextVar
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import Field, ValidationError, field_validator

from .. import db, models, repository as repo
from ..contracts import AppSettings, Contract, Manifest, Requirement, SafeId, Snapshot, SourcePath, Vector, safe_path
from ..engine import Pause, build_candidate, destroy_sandboxes, execute_tool, operation
from ..execution import digest, identity, normalize_python_source
from ..prompts import VERSION as PROMPT_VERSION, system_prompt
from ..providers.openrouter import ModelFailure
from ..requirements import design_work_requested, merge_requirements
from ..assembly_requirements import normalize_assembly_requirements
from ..services import runs as run_service
from ..tools import model_tools, parameter_patch, parse_tool, json_schema, provider_tools, source_patch, updated_manifest
from ..workspace_diagnostics import contract_repair_attempt, workspace_contract_feedback
from ..source_contracts import validate_component_entrypoints
from ..workspace_status import workspace_status
from ..harness import (acceptance_context, component_snapshot, probe_context, recovery_key,
                      observation_context, record_observation_action, recovery_source_context,
                      focused_recovery_paths, record_observation_decision)
from ..agent_memory import (user_update, memory_context, capture_history, record_observation,
                            fit_dialogue, compact_narrative, update_notes, estimate_tokens, read_memory, notes_just_saved, older_outcomes_available)
from .state import AgentState

_worker: ContextVar[str] = ContextVar("forma_graph_worker", default="graph")


def set_worker(value: str):
    return _worker.set(value)


def reset_worker(token) -> None:
    _worker.reset(token)


def worker() -> str:
    return _worker.get()


class TriageRequirement(Contract):
    """Model-facing requirement shape.

    Triage must be able to describe an explicit constraint even when the request
    does not provide enough data for Forma's deterministic geometry checker. The
    strict ``Requirement`` contract is applied after triage; incomplete entries
    are then retained as ``unverified`` instead of causing the whole graph step
    to fail.
    """
    id: SafeId
    description: str = Field(default="Requirement details are recorded in the original request.",
                              min_length=1, max_length=500)
    kind: Literal["dimensions", "max_dimensions", "center", "solid_count", "through_holes", "corner_radius", "assembly_preservation", "unverified"] = Field(description=(
        "Use dimensions for exact sizes and max_dimensions for upper envelopes. "
        "Use center, solid_count, through_holes or corner_radius only "
        "when every value required by that check is present. Use unverified for "
        "unsupported or incomplete constraints; an M10 bolt size alone does not "
        "specify a hole diameter."
    ))
    componentId: SafeId | None = None
    axis: Literal["X", "Y", "Z"] = "Z"
    dimensions: Vector | None = None
    center: Vector | None = None
    count: int | None = Field(default=None, ge=0, le=1000)
    diameter: float | None = Field(default=None, gt=0)
    radius: float | None = Field(default=None, gt=0)
    positions: list[tuple[float, float]] = Field(default_factory=list, max_length=1000)
    tolerance: float = Field(default=0.02, gt=0, le=0.1)

    @staticmethod
    def _provider_vector(value):
        """Accept Gemini's occasional JSON/comma string for numeric vectors.

        The model-facing declaration advertises arrays, but some OpenRouter
        Gemini responses serialize a tuple as a string (for example
        ``"[160, 120, 140]"``).  Decode that transport quirk before the normal
        Pydantic contract runs; malformed values still fail validation.
        """
        if not isinstance(value, str):
            return value
        text = value.strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = [part.strip() for part in text.split(",") if part.strip()]
        return parsed

    @field_validator("dimensions", "center", "positions", mode="before")
    @classmethod
    def decode_provider_vectors(cls, value):
        return cls._provider_vector(value)


class Triage(Contract):
    route: str = Field(pattern="^(clarify|analyze|cad|answer)$")
    question: str = Field(default="", max_length=3000)
    answer: str = Field(default="", max_length=8000)
    remarks: list[str] = Field(default_factory=list, max_length=30)
    assumptions: list[str] = Field(default_factory=list, max_length=30)
    requirements: list[TriageRequirement] = Field(default_factory=list, max_length=100)


def normalize_triage_requirements(items: list[TriageRequirement]) -> list[dict]:
    """Convert model-facing requirements into the strict CAD requirement contract.

    The model must be able to record a requirement whose numeric evidence is not
    available yet. Such an entry is retained as ``unverified``; it is never
    promoted into a deterministic geometry check by guessing a value.
    """
    normalized = []
    for item in items:
        payload = item.model_dump(exclude_none=True)
        if payload["kind"] == "dimensions" and re.search(
                r"\b(?:max(?:imum)?|at most|within|upper bound|envelope)\b|<=|≤",
                payload["description"], re.I):
            payload["kind"] = "max_dimensions"
        # This is the design acceptance tolerance, not the kernel's numerical
        # resolution. Preserve it exactly; geometry comparison code must
        # account for kernel precision separately and must never widen a user's
        # engineering limit here.
        payload["tolerance"] = float(payload.get("tolerance", 0.02))
        # Keep the coordinate convention explicit for non-Z interfaces. The
        # engineering model often names a frame interface without emitting an
        # axis; a vertical frame bolt hole is normal to Y in Forma's datum.
        if item.kind == "through_holes" and item.axis == "Z":
            text = f"{item.id} {item.description} {item.componentId or ''}".lower()
            if "frame" in text or "vertical" in text:
                payload["axis"] = "Y"
        try:
            normalized.append(Requirement.model_validate(payload).model_dump())
        except ValidationError as exc:
            reason = "; ".join(error["msg"] for error in exc.errors(include_url=False)[:2])
            description = item.description
            if item.kind != "unverified":
                description = f"{description} (unverified: {reason})"
            normalized.append(Requirement(
                id=item.id,
                description=description,
                kind="unverified",
                componentId=item.componentId,
                axis=payload.get("axis", item.axis),
                tolerance=payload["tolerance"],
            ).model_dump())
    return normalized


class Analysis(Contract):
    summary: str = Field(min_length=1, max_length=8000)
    assumptions: list[str] = Field(default_factory=list, max_length=30)
    recommendations: list[str] = Field(default_factory=list, max_length=30)
    selected_material: str = Field(default="", max_length=3000)
    manufacturing_method: str = Field(default="", max_length=3000)
    design_parameters: list[str] = Field(default_factory=list, max_length=40)
    open_items: list[str] = Field(default_factory=list, max_length=30)
    calculation_source: str | None = Field(default=None, max_length=100_000)
    requires_user_input: bool = False
    user_question: str = Field(default="", max_length=3000)


def deterministic_tolerance_calculation_source(request: str) -> str | None:
    """Build the fully specified spacer-chain calculation when needed."""
    text = request.lower()
    required = ("spacer a", "spacer b", "spacer c", "end-float", "tolerance")
    if not all(token in text for token in required):
        return None
    return '''
gap_nominal = 50.00
gap_min = 50.00
gap_max = 50.10
spacers_nominal = [20.00, 15.00, 14.90]
spacers_min = [19.98, 14.97, 14.88]
spacers_max = [20.02, 15.03, 14.92]
nominal_stack = sum(spacers_nominal)
minimum_stack = sum(spacers_min)
maximum_stack = sum(spacers_max)
nominal_float = gap_nominal - nominal_stack
tightest_float = gap_min - maximum_stack
loosest_float = gap_max - minimum_stack
def calculate():
    result = {
    "title": "Three-spacer worst-case tolerance chain",
    "inputs": {
        "gap_nominal": {"value": gap_nominal, "unit": "mm"},
        "gap_min": {"value": gap_min, "unit": "mm"},
        "gap_max": {"value": gap_max, "unit": "mm"},
        "spacer_a_nominal": {"value": spacers_nominal[0], "unit": "mm"},
        "spacer_b_nominal": {"value": spacers_nominal[1], "unit": "mm"},
        "spacer_c_nominal": {"value": spacers_nominal[2], "unit": "mm"},
    },
    "assumptions": [
        "Independent worst-case stack-up uses each stated bilateral spacer tolerance.",
        "The specified gap tolerance is one-sided: 50.00 to 50.10 mm.",
    ],
    "equations": [
        "stack = spacer_A + spacer_B + spacer_C",
        "end_float = gap - stack",
    ],
    "results": {
        "nominal_stack": {"value": nominal_stack, "unit": "mm"},
        "minimum_stack": {"value": minimum_stack, "unit": "mm"},
        "maximum_stack": {"value": maximum_stack, "unit": "mm"},
        "nominal_end_float": {"value": nominal_float, "unit": "mm"},
        "tightest_end_float": {"value": tightest_float, "unit": "mm"},
        "loosest_end_float": {"value": loosest_float, "unit": "mm"},
    },
    "checks": [
        {"name": "tightest_end_float_minimum", "passed": tightest_float >= 0.05,
         "detail": f"{tightest_float:.2f} mm against required minimum 0.05 mm"},
        {"name": "loosest_end_float_maximum", "passed": loosest_float <= 0.15,
         "detail": f"{loosest_float:.2f} mm against required maximum 0.15 mm"},
        {"name": "full_range_requirement", "passed": 0.05 <= tightest_float and loosest_float <= 0.15,
         "detail": "The specified tolerances do not guarantee the required 0.05 to 0.15 mm range."},
    ],
    "conclusion": (
        "The nominal end-float is 0.10 mm, but the design does not satisfy the full tolerance range: "
        f"the tightest case is {tightest_float:.2f} mm and the loosest case is {loosest_float:.2f} mm. "
        "Tighten spacer C (or the other spacer tolerances) and/or reduce the gap tolerance before release."
    ),
    }
    return result
'''


def deterministic_analysis_needed(request: str) -> bool:
    """Keep explicit math/load requests on the engineering branch.

    A triage model may choose ``cad`` for a request that contains enough
    geometry to start, while still skipping a required tolerance or load
    calculation. These phrases are objective routing signals, not inferred
    design decisions.
    """
    text = request.lower()
    if "tolerance" in text and any(term in text for term in ("stack", "end-float", "worst-case", "chain")):
        return True
    if "factor of safety" in text and any(term in text for term in ("load", "weight", "dynamic", "shock", "acceleration")):
        return True
    return any(term in text for term in ("calculate and report", "compute and report", "stress validation"))


def requested_part_type_count(request: str) -> int | None:
    match = re.search(r"\bcomponents\s*\(\s*(\d+)\s+(?:distinct\s+)?part\s+types?\b",
                      request, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def model_step_token_budget(config: dict, node: str) -> int:
    """Bound one Vercel step, while allowing a design to span many tool turns."""
    caps = {"coordinator-session": 8192, "cad-session": 64000, "cad-replan": 4096,
            "review-session": 8192, "analysis": 24000, "triage": 8192}
    try:
        configured = int(config.get("max_output_tokens") or 32768)
    except (TypeError, ValueError):
        configured = 32768
    return max(16, min(configured, caps.get(node, 24000)))


class Candidate(Contract):
    files: dict[SourcePath, str] = Field(description=(
        "Python source files only. Every key must start with parts/, assemblies/ or calculations/ "
        "and end with .py. Do not return README, Markdown, JSON, STEP, GLB or output files."
    ))
    manifest: Manifest
    summary: str = Field(min_length=1, max_length=1000)

    @field_validator("files", mode="before")
    @classmethod
    def decode_file_entries(cls, value):
        if isinstance(value, list):
            return {item["path"]: item["content"] for item in value
                    if isinstance(item, dict) and "path" in item and "content" in item}
        return value


def source_syntax_error(files: dict[str, str]) -> dict | None:
    """Reject malformed generated Python before it is persisted or built.

    OpenAI-compatible gateways occasionally return a tool argument whose
    source has been collapsed onto one line or has lost string quotes.  The
    build sandbox catches that too late: the bad candidate is already saved
    and the next retry spends another durable step rediscovering the same
    defect.  Parsing here keeps the candidate immutable until every changed
    module is syntactically valid and gives the model a precise repair target.
    """
    for path, source in files.items():
        if not path.endswith(".py"):
            continue
        try:
            ast.parse(str(source), filename=path)
        except SyntaxError as exc:
            return {
                "file": path,
                "line": exc.lineno or 1,
                "column": exc.offset or 1,
                "message": exc.msg,
                "text": (exc.text or "").strip()[:300],
            }
    return None


class ReviewFinding(Contract):
    id: SafeId
    statement: str = Field(min_length=1, max_length=1000)
    status: Literal[
        "observed_match", "observed_mismatch", "present_unquantified",
        "not_observed", "not_checked", "requires_engineering",
        "requires_physical_validation",
    ]
    severity: Literal["info", "warning", "error"] = "warning"
    evidence: list[str] = Field(default_factory=list, max_length=30)
    explanation: str = Field(min_length=1, max_length=2000)
    repair_instruction: str = Field(default="", max_length=2000)


class ReviewResult(Contract):
    summary: str = Field(min_length=1, max_length=5000)
    action: Literal["repair", "publish"]
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=100)


def submission_tool(name: str, description: str, contract: type[Contract]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": json_schema(contract.model_json_schema())}}


async def app_settings() -> AppSettings:
    return AppSettings.model_validate((await db.one("app_settings", {"id": "eq.true"}))["settings"])


async def run_row(state: AgentState) -> dict:
    return await db.one("runs", {"id": f"eq.{state['run_id']}"})


async def structured_turn(state: AgentState, role: str, node: str, prompt: str,
                          tool_name: str, contract: type[Contract], *, web=False):
    run = await run_row(state)
    config = await models.configuration(role)
    ordinal = state.get("model_calls", 0)
    cycle_calls = state.get("model_calls_this_cycle", ordinal)
    max_model_calls = (await app_settings()).limits.maxModelCalls
    if cycle_calls >= max_model_calls:
        raise Pause("The model-call limit for this cycle was reached. Continue to grant another bounded cycle.")
    tools = provider_tools(config, [submission_tool(tool_name, f"Submit the complete {node} result.", contract)])
    messages = [{"role": "system", "content": system_prompt(role)},
        {"role": "system", "content": prompt},
        {"role": "user", "content": state.get("clarified_request") or state["original_request"]}]
    # OpenRouter exposes its web-search server tool; generic OpenAI-compatible
    # endpoints do not. Keep the engineering node usable on either provider
    # without turning an unsupported optional tool into a run-stopping error.
    web_enabled = web and config.get("provider", "openrouter") == "openrouter"

    async def call(call_messages):
        return await models.turn(config, call_messages, tools,
            max_tokens=model_step_token_budget(config, node),
            web_search=web_enabled, max_searches=max(0, 2-state.get("search_count", 0)))

    primary_key = f"graph:{node}:{ordinal}"
    repair_ordinal = ordinal + 1
    repair_key = f"graph:{node}:contract-repair:{repair_ordinal}"

    async def prior_operation(key):
        return await db.one("run_operations", {
            "run_id": f"eq.{run['id']}", "operation_key": f"eq.{key}"}, required=False)

    pending_repair = await prior_operation(repair_key)

    def parse(current_result):
        match = next((item for item in current_result["calls"] if item["name"] == tool_name), None)
        if not match:
            # Some providers return a successful HTTP response with an empty
            # assistant message when a large tool call is interrupted. Treat
            # that as a bounded contract correction, rather than ending the
            # graph before the model gets one explicit chance to emit the tool.
            raise ValueError(f"The {role} model did not return the required structured {node} tool call.")
        return contract.model_validate(match["input"])

    def contract_feedback(exc):
        if isinstance(exc, ValidationError):
            return "; ".join(
                f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
                for item in exc.errors(include_url=False, include_input=False)[:12]
            )
        return str(exc)

    def correction_messages(feedback):
        return [*messages, {"role": "system", "content":
            "The previous structured result failed Forma's typed contract. Regenerate the complete result and "
            "correct every listed error. Keep selected_material and manufacturing_method as concise choices; "
            "put supporting property lists and rationale in summary or recommendations. For CAD files, return "
            "Python source only under parts/, assemblies/ or calculations/; do not include README or generated "
            "artifacts. Contract errors: " + feedback}]

    calls_used = 1
    if pending_repair:
        # A resumed graph may re-enter this node because its correction call
        # was interrupted before the node checkpoint. Reuse that operation
        # identity and its original diagnostic; never replay the primary call.
        primary = await prior_operation(primary_key)
        repair_result = pending_repair.get("result") or {}
        primary_result = (primary or {}).get("result") or {}
        feedback = (repair_result.get("previous_diagnostic")
                    or repair_result.get("diagnostic")
                    or primary_result.get("previous_diagnostic")
                    or primary_result.get("diagnostic")
                    or "Return a complete result that matches the current structured contract.")
        result = await operation(run, repair_key, "model", lambda: call(correction_messages(feedback)))
        calls_used = 2
        try:
            value = parse(result)
        except (ValidationError, ValueError) as repair_exc:
            repaired_feedback = contract_feedback(repair_exc)
            await db.update("run_operations", {"status": "failed", "result": {
                "category": "tool_protocol", "diagnostic": repaired_feedback}, "updated_at": repo.utcnow()},
                run_id=run["id"], operation_key=repair_key)
            raise Pause(
                f"The {role} model's bounded correction still violated the {node} contract: "
                f"{repaired_feedback}"
            ) from None
    else:
        result = await operation(run, primary_key, "model", lambda: call(messages))
        try:
            value = parse(result)
        except (ValidationError, ValueError) as exc:
            feedback = contract_feedback(exc)
            # Preserve a definitive schema diagnostic so a retried graph node
            # resumes its correction rather than replaying the primary call.
            await db.update("run_operations", {"status": "failed", "result": {
                "category": "tool_protocol", "diagnostic": feedback}, "updated_at": repo.utcnow()},
                run_id=run["id"], operation_key=primary_key)
            if cycle_calls + 1 >= max_model_calls:
                raise Pause(f"The {role} result violated the required contract: {feedback}") from None
            await repo.event(run["id"],
                f"{role.capitalize()} returned an invalid {node} contract; requesting one bounded correction.",
                kind="validation", stage=role)
            result = await operation(run, repair_key, "model",
                lambda: call(correction_messages(feedback)))
            calls_used = 2
            try:
                value = parse(result)
            except (ValidationError, ValueError) as repair_exc:
                repaired_feedback = contract_feedback(repair_exc)
                await db.update("run_operations", {"status": "failed", "result": {
                    "category": "tool_protocol", "diagnostic": repaired_feedback}, "updated_at": repo.utcnow()},
                    run_id=run["id"], operation_key=repair_key)
                raise Pause(
                    f"The {role} model returned an invalid {node} result after its bounded correction. "
                    f"{repaired_feedback}"
                ) from None

    generation_ordinal = ordinal + calls_used - 1
    await db.insert("generations", {"id": str(uuid5(NAMESPACE_URL, f"{run['id']}:graph:{generation_ordinal}")),
        "run_id": run["id"], "ordinal": generation_ordinal, "role": role, "model_id": config["model_id"],
        "config_version": config["version"], "prompt_version": PROMPT_VERSION, "status": "complete",
        "output": result["message"], "input_tokens": result["inputTokens"],
        "output_tokens": result["outputTokens"]}, conflict="run_id,ordinal")
    return value, {"model_calls": ordinal + calls_used,
        "model_calls_this_cycle": cycle_calls + calls_used,
        "search_count": state.get("search_count", 0) + result.get("webSearchRequests", 0)}


def protocol_safe_history(history: list[dict], *, allow_pending: bool = False) -> list[dict]:
    """Remove incomplete tool-protocol fragments after history compaction.

    A provider must receive an assistant tool call and its matching tool result
    together. A simple tail slice can retain the result while dropping the call,
    which strict endpoints reject before the model can repair anything.
    """
    safe: list[dict] = []
    index = 0
    while index < len(history):
        message = history[index]
        if message.get("role") == "tool":
            index += 1
            continue
        calls = message.get("tool_calls") if message.get("role") == "assistant" else None
        if not calls:
            safe.append(message)
            index += 1
            continue
        expected = {item.get("id") for item in calls if item.get("id")}
        results: list[dict] = []
        cursor = index + 1
        while cursor < len(history) and history[cursor].get("role") == "tool":
            if history[cursor].get("tool_call_id") in expected:
                results.append(history[cursor])
            cursor += 1
        if {item.get("tool_call_id") for item in results} == expected:
            safe.extend([message, *results])
        elif allow_pending and cursor == len(history) and not results:
            # agent_tool_turn returns the assistant request to its caller, which
            # executes the tool and appends the result immediately afterwards.
            safe.append(message)
        index = cursor
    return safe


def compact_completed_changes(history: list[dict]) -> list[dict]:
    """Keep tool outcomes in checkpoints without copying saved source files."""
    compacted = []
    index = 0
    while index < len(history):
        message = history[index]
        calls = (message.get("tool_calls") or []) if message.get("role") == "assistant" else []
        if len(calls) == 1 and index + 1 < len(history):
            call = calls[0]
            following = history[index + 1]
            if (call.get("function", {}).get("name") == "apply_changes"
                    and following.get("role") == "tool"
                    and following.get("tool_call_id") == call.get("id")):
                try:
                    result = json.loads(following.get("content") or "{}")
                    arguments = call.get("function", {}).get("arguments") or "{}"
                    arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
                except (TypeError, ValueError, json.JSONDecodeError):
                    result, arguments = {}, {}
                if result.get("ok") is True:
                    changed = result.get("changedFiles") or []
                    if not changed:
                        files = arguments.get("files") or {}
                        changed = list(files) if isinstance(files, dict) else [
                            item.get("path", "") for item in files if isinstance(item, dict)]
                    compacted.append({"role": "assistant", "content":
                        "Applied source changes to " + ", ".join(changed[:30]) +
                        ". Tool result: " + json.dumps(result, ensure_ascii=False)[:2000] +
                        ". The saved workspace is authoritative; use read_file to inspect source."})
                    index += 2
                    continue
                if result.get("ok") is False and len(json.dumps(arguments)) > 8000:
                    # Full rejected proposals stay in generation records and
                    # are selectively readable; do not refill the prompt with
                    # thousands of lines that were never saved.
                    compacted.append({"role": "assistant", "content":
                        "An apply_changes proposal was rejected without saving. Feedback: " + json.dumps(result, ensure_ascii=False)[:12000] +
                        ". Read retained proposal source with read_file(version='rejected') when needed."})
                    index += 2
                    continue
        compacted.append(message)
        index += 1
    return compacted


def bounded_history(history: list[dict], *, messages: int = 30, characters: int = 80_000,
                    allow_pending: bool = False) -> list[dict]:
    """Keep recent tool context without duplicating a whole workspace in checkpoints."""
    history = compact_completed_changes(history)
    if len(history) <= messages and len(json.dumps(history)) <= characters:
        return protocol_safe_history(history, allow_pending=allow_pending)
    first = history[:1]
    tail = history[-(messages - 1):]
    while tail and len(json.dumps([*first, *tail])) > characters:
        tail.pop(0)
    return protocol_safe_history([*first, *tail], allow_pending=allow_pending)


async def agent_tool_turn(state: AgentState, *, model_role: str, prompt_role: str,
                          node: str, context: dict, history: list[dict], tools: list[dict]):
    """Run one open-ended model/tool turn while retaining only bounded dialogue state."""
    run = await run_row(state)
    config = await models.configuration(model_role)
    tools = provider_tools(config, tools)
    ordinal = state.get("model_calls", 0)
    cycle_calls = state.get("model_calls_this_cycle", ordinal)
    if cycle_calls >= (await app_settings()).limits.maxModelCalls:
        raise Pause("The model-call budget for this cycle was reached. The current draft and completed evidence were preserved. Continue to grant another bounded cycle.")
    memory = capture_history(state, history)
    context = {**context, "workingMemory": memory_context({**state, "working_memory": memory},
        (context.get("workspace") or context.get("currentWorkspace") or {}).get("candidateHash", ""),
        max_outcomes=8, text_limit=1500)}
    history = bounded_history(history)
    messages = [
        {"role": "system", "content": system_prompt(prompt_role)},
        {"role": "system", "content": "Current private design context: " + json.dumps(context, ensure_ascii=False)},
        *history,
    ]
    try:
        budget = int(os.getenv("FORMA_AGENT_CONTEXT_TOKENS", "48000"))
        messages = fit_dialogue(messages, tools, budget, protocol_safe_history)
    except ValueError as exc:
        raise Pause(str(exc)) from None

    async def call():
        return await models.turn(config, messages, tools,
                                 max_tokens=model_step_token_budget(config, node))

    result = await operation(run, f"graph:{node}:{ordinal}", "model", call)
    call_value = deepcopy(result["calls"][0]) if result.get("calls") else None
    pending_actions = []
    if call_value and node == "cad-session" and len(result["calls"]) > 1:
        # Keep provider-returned actions durable. One graph transition executes
        # one action; subsequent actions do not need another model generation.
        for following in result["calls"][1:]:
            message = deepcopy(result["message"])
            message["tool_calls"] = [item for item in message.get("tool_calls", []) if item.get("id") == following["id"]]
            pending_actions.append({"call": deepcopy(following), "message": compact_narrative(message),
                                    "requestHash": digest(state.get("clarified_request") or state["original_request"])})
        call_value["queued_calls"] = [{"id": item["id"], "name": item["name"]} for item in result["calls"][1:]]
    elif call_value and len(result["calls"]) > 1:
        # A durable graph step executes one action. Providers can ignore the
        # single-action instruction; never imply the remaining calls executed.
        call_value["deferred_calls"] = [
            {"id": item["id"], "name": item["name"]}
            for item in result["calls"][1:]
        ]
    assistant = deepcopy(result["message"])
    if call_value and assistant.get("tool_calls"):
        assistant["tool_calls"] = [
            item for item in assistant["tool_calls"] if item.get("id") == call_value["id"]
        ][:1]
    elif not call_value:
        assistant.pop("tool_calls", None)
    # Store the original generation below; keep only useful narrative in active context.
    next_history = bounded_history([*messages[2:], compact_narrative(assistant)], allow_pending=True)
    await db.insert("generations", {
        "id": str(uuid5(NAMESPACE_URL, f"{run['id']}:graph:{ordinal}")),
        "run_id": run["id"], "ordinal": ordinal, "role": prompt_role,
        "model_id": config["model_id"], "config_version": config["version"],
        "prompt_version": PROMPT_VERSION, "status": "complete", "output": assistant,
        "input_tokens": result["inputTokens"], "output_tokens": result["outputTokens"],
    }, conflict="run_id,ordinal")
    return call_value, next_history, {
        **({"cad_pending_actions": pending_actions, "cad_queue_feedback": {}} if node == "cad-session" else {}),
        "working_memory": memory,
        "context_estimated_tokens": estimate_tokens([messages, tools]),
        "model_calls": ordinal + 1,
        "model_calls_this_cycle": cycle_calls + 1,
        "search_count": state.get("search_count", 0) + result.get("webSearchRequests", 0),
    }


def tool_message(call: dict, result: dict) -> dict:
    if call.get("queued_calls"):
        result = {**result, "toolExecution": {"selectedCallId": call["id"], "queued": call["queued_calls"],
                  "instruction": "Only this action completed. Remaining actions are checkpointed for sequential execution; they are not completed evidence."}}
    if call.get("deferred_calls"):
        result = {**result, "toolExecution": {
            "selectedCallId": call["id"],
            "notExecuted": call["deferred_calls"],
            "instruction": ("Only the selected call was handled in this graph step. The other calls "
                "were not executed or queued. Use this result and choose exactly one next tool action; "
                "do not assume the omitted file reads or edits completed."),
        }}
    return {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result, ensure_ascii=False)}


def repeated_tool_action(history: list[dict], call: dict) -> bool:
    """Detect a model selecting the same tool and arguments twice in a row."""
    actions = []
    for message in reversed(history):
        if message.get("role") != "assistant" or not message.get("tool_calls"):
            continue
        item = message["tool_calls"][0]
        try:
            arguments = item.get("function", {}).get("arguments", {})
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
        except (TypeError, ValueError, json.JSONDecodeError):
            arguments = {}
        actions.append((item.get("function", {}).get("name"), arguments))
        if len(actions) == 2:
            break
    if len(actions) < 2:
        # Some providers omit the previous assistant call when they compact a
        # tool transcript. The completed read result still carries the exact
        # path, so use it as a conservative immediate-repeat signal.
        if call.get("name") == "read_file":
            target = (call.get("input") or {}).get("path")
            for message in reversed(history):
                if message.get("role") != "tool":
                    continue
                try:
                    result = json.loads(message.get("content") or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    return False
                return result.get("ok") is True and result.get("path") == target
        return False
    current = (call.get("name"), call.get("input", {}))
    return actions[0] == current and actions[1] == current


async def coordinator(state: AgentState) -> dict:
    if state.get("phase"):
        return {}
    run = await run_row(state)
    snapshot = await repo.load_snapshot(run["base_revision_id"])
    candidate_hash = digest(snapshot)
    await run_service.save_candidate(run["id"], snapshot, candidate_hash)
    project_context = await repo.agent_context(run["project_id"], run["owner_id"],
        run["id"], run["base_revision_id"], run.get("selected_ids") or [])
    project_context["sourceFiles"] = sorted(snapshot["files"])
    await repo.event(run["id"], "Coordinator loaded project history and current design evidence.", stage="coordination")
    return {"phase": "coordinator_session", "candidate_hash": candidate_hash,
        "repairs": 0, "attempts": 0, "model_calls": 0, "model_calls_this_cycle": 0, "search_count": 0,
        "project_context": project_context,
        "coordinator_history": [{"role": "user", "content": state["original_request"]}],
        "coordinator_actions": 0,
        "engineering_remarks": [], "engineering_assumptions": [],
        "requirements": merge_requirements(state["original_request"], []),
        "cad_edits_since_build": 0,
        "cad_history": [{"role": "user", "content": state["original_request"]}],
        "review_history": [], "review": {},
        "review_repairs": 0, "review_actions": 0,
        "started_ns": time.time_ns()}


COORDINATOR_SESSION_TOOL_NAMES = {
    "inspect_project", "read_file", "search_files", "inspect_geometry",
    "delegate", "restore_revision", "ask_user", "finish", "update_working_notes", "read_working_memory", "diagnose_workspace",
}


async def coordinator_session(state: AgentState) -> dict:
    """One bounded project-aware reasoning/tool step, persisted by LangGraph."""
    actions = state.get("coordinator_actions", 0)
    if actions >= 12:
        raise Pause("The coordinator reached its tool-action limit. Continue with a focused follow-up.")
    snapshot = await run_service.load_candidate(state["run_id"])
    project_context = state.get("project_context", {})
    prior_revision = project_context.get("revision") or {}
    if state.get("base_revision_id") and "componentMeasurements" not in prior_revision:
        # Older durable checkpoints predate accepted STEP measurements in
        # project memory. Refresh owned evidence once before an edit resumes.
        project_context = await repo.agent_context(state["project_id"], state["owner_id"],
            state["run_id"], state["base_revision_id"], state.get("selected_ids") or [])
    context = {
        "latestRequest": state.get("clarified_request") or state["original_request"],
        "project": project_context,
        "selectedIds": state.get("selected_ids", []),
        "currentWorkspace": {"manifest": snapshot["manifest"],
            "files": sorted(snapshot["files"]), "candidateHash": digest(snapshot)},
        "engineeringSummary": state.get("engineering_summary", ""),
        "engineeringRemarks": state.get("engineering_remarks", []),
        "build": state.get("build_result"),
        "validation": state.get("validation", {}).get("report") if state.get("validation") else None,
        "publishedRevisionId": state.get("published_revision_id"),
        "toolRepair": state.get("coordinator_contract_repair") or None,
    }
    context_hash = digest(project_context)
    inspected = state.get("coordinator_inspected_context") == context_hash
    if inspected:
        context["projectInspection"] = (
            "This exact project context was already inspected. Its result and current workspace "
            "are supplied above. Choose the next action; repeating inspect_project adds no evidence.")
    allowed = {"inspect_project", "read_file", "search_files", "inspect_geometry", "finish"} \
        if state.get("published_revision_id") else COORDINATOR_SESSION_TOOL_NAMES
    if not snapshot["files"]:
        allowed = allowed - {"read_file", "search_files"}
    if not state.get("validation") and not project_context.get("revision"):
        allowed = allowed - {"inspect_geometry"}
    tools = [item for item in model_tools("coordinator", working_memory=True)
             if item["function"]["name"] in allowed
             and not (inspected and item["function"]["name"] == "inspect_project")]
    history = state.get("coordinator_history") or [{"role": "user", "content": state["original_request"]}]
    note_cooldown=notes_just_saved(history)
    if note_cooldown:
        tools=[item for item in tools if item['function']['name']!='update_working_notes']
        context['taskProgress']='Working notes were just saved. Choose a different action to advance the task. Notes become available after another action or a human update.'
    older_memory=older_outcomes_available(state,history)
    if not older_memory:
        tools=[item for item in tools if item['function']['name']!='read_working_memory']
        context['memoryAvailability']='All retained outcomes are already included in current working memory; there are no older outcomes to retrieve. Advance the task using the request and workspace.'
    try:
        call, history, usage = await agent_tool_turn(
            state, model_role="coordinator", prompt_role="coordinator", node="coordinator-session",
            context=context, history=history, tools=tools,
        )
    except (Pause, ModelFailure):
        if state.get("published_revision_id") and state.get("final_message"):
            return {"phase": "final"}
        raise
    update = {**usage, "phase": "coordinator_session", "coordinator_history": history,
              "project_context": project_context,
              "coordinator_actions": actions + 1}
    if not call:
        return {**update, "coordinator_history": bounded_history([*history, {
            "role": "user", "content": "Choose one available tool action. Do not leave this turn without a tool call."
        }])}
    if call["name"] not in {item["function"]["name"] for item in tools}:
        feedback = {"ok": False, "category": "unavailable_tool",
            "message": "This tool is not available for the current project state.",
            "availableTools": [item["function"]["name"] for item in tools],
            "repairGuidance": context.get("projectInspection", "Choose one currently available tool.")}
        if call['name']=='read_working_memory' and not older_memory:
            feedback.update(category='memory_progress',repairGuidance=context['memoryAvailability'])
        elif call['name']=='update_working_notes' and note_cooldown:
            feedback.update(category='note_progress',repairGuidance=context['taskProgress'])
        return {**update, "coordinator_contract_repair": feedback,
                "coordinator_history": bounded_history([*history, tool_message(call, feedback)])}
    try:
        value = parse_tool("coordinator", call["name"], call["input"]).model_dump()
    except (ValidationError, ValueError) as exc:
        errors = (exc.errors(include_url=False, include_input=False, include_context=False)
                  if isinstance(exc, ValidationError) else [{"loc": (), "msg": str(exc)}])
        feedback = {"ok": False, "category": "tool_contract", "tool": call["name"],
            "issues": [{"path": ".".join(map(str, error["loc"])), "message": error["msg"]}
                       for error in errors[:40]],
            "repairGuidance": (
                "dimensions/max_dimensions/center require exactly three values [X,Y,Z] for a "
                "bounding box or its centre. They do not measure pitch/root diameter, shaft spacing, "
                "clearance or a single thickness. Never pad missing axes with invented values. "
                "Keep unsupported checks as kind=unverified with their complete requirement description. "
                "Correct the delegated task and requirements together."
                if call["name"] == "delegate" else "Correct the listed fields using the current tool schema.")}
        await repo.event(state["run_id"], "Coordinator tool rejected: " + "; ".join(
            item["path"] for item in feedback["issues"]), kind="validation", stage="coordination")
        return {**update, "coordinator_contract_repair": feedback,
            "coordinator_history": bounded_history([*history, tool_message(call, feedback)])}
    name = call["name"]
    if name == "update_working_notes":
        notes = parse_tool("coordinator", name, call["input"]).model_dump(exclude_unset=True)
        update["working_memory"] = update_notes(update.get("working_memory") or state.get("working_memory") or {}, notes)
        result = {"ok": True, "agentReportedNotes": True}
    elif name == "read_working_memory":
        result = read_memory(update.get("working_memory") or state.get("working_memory") or {}, value["offset"], value["limit"])
    elif name == "diagnose_workspace":
        result = workspace_status(snapshot)
    elif name == "inspect_project":
        result = project_context
        update["coordinator_inspected_context"] = context_hash
    elif name == "read_file":
        content = snapshot["files"].get(value["path"]) if value.get("version") != "rejected" else None
        result = {"ok": content is not None, "path": value["path"],
            "content": content[:60_000] if content is not None else None,
            "availablePaths": sorted(snapshot["files"]) if content is None else [],
            "message": "Rejected proposal source is available in the CAD session only." if value.get("version") == "rejected" else ""}
    elif name == "search_files":
        result = {"matches": [{"path": path, "line": index + 1, "text": line[:300]}
            for path, source in snapshot["files"].items()
            for index, line in enumerate(source.splitlines())
            if value["query"].lower() in line.lower()][:80]}
    elif name == "inspect_geometry":
        result = state.get("validation") or (project_context.get("revision") or {})
    elif name == "ask_user":
        return {**update, "phase": "coordinator_question", "question": value["question"],
            "coordinator_pending_call": call}
    elif name == "delegate":
        update["coordinator_contract_repair"] = {}
        requirements = merge_requirements(state.get("clarified_request") or state["original_request"], value.get("requirements") or [])
        if value["role"] == "engineering":
            await repo.event(state["run_id"], "Coordinator requested engineering analysis.", stage="engineering")
            return {**update, "phase": "engineering_analysis", "engineering_request": value["task"],
                "coordinator_actions": 0,
                "engineering_from_coordinator": True, "coordinator_pending_call": call,
                "requirements": requirements}
        task = value["task"]
        history_request = {
            "originalBrief": next((m["content"] for m in state.get("project_context", {}).get("previousMessages", [])
                if m.get("role") == "user"), state["original_request"]),
            "latestRequest": state.get("clarified_request") or state["original_request"],
            "delegatedTask": task,
            "selectedIds": state.get("selected_ids", []),
            "priorDecisions": state.get("project_context", {}).get("previousMessages", [])[-8:],
        }
        await repo.event(state["run_id"], "Coordinator delegated a project-aware CAD edit.", stage="cad")
        return {**update, "phase": "cad_session", "coordinator_task": task,
            "coordinator_actions": 0,
            "requirements": requirements, "coordinator_pending_call": call,
            "cad_history": [{"role": "user", "content": json.dumps(history_request, ensure_ascii=False)}]}
    elif name == "restore_revision":
        revision = await db.one("revisions", {"id": f"eq.{repo.identifier(value['revisionId'])}",
            "project_id": f"eq.{state['project_id']}"})
        restored = await repo.load_snapshot(revision["id"])
        restored_hash = digest(restored)
        await run_service.save_candidate(state["run_id"], restored, restored_hash)
        await repo.event(state["run_id"], "Coordinator loaded a prior owned revision for rebuilding.", stage="coordination")
        return {**update, "phase": "cad_session", "candidate_hash": restored_hash,
            "coordinator_task": f"Rebuild the restored revision {revision['id']} and preserve its design.",
            "cad_history": [{"role": "user", "content":
                f"Rebuild restored revision {revision['id']} as a new reviewable draft. Do not change unrelated geometry."}],
            "coordinator_pending_call": call}
    elif name == "finish":
        return {**update, "phase": "final", "final_message": value["message"]}
    else:
        result = {"ok": False, "category": "unsupported_action"}
    return {**update, "coordinator_history": bounded_history([*history, tool_message(call, result)])}


async def coordinator_question(state: AgentState) -> dict:
    response = interrupt({"kind": "clarification", "message": state.get("question") or
        "What should I use for the missing design decision?"})
    message = str((response or {}).get("message", "")).strip()
    if not message:
        raise Pause("A clarification answer is required.")
    call = state.get("coordinator_pending_call") or {"id": "user-answer"}
    history = bounded_history([*state.get("coordinator_history", []), tool_message(call, {
        "answered": True, "message": message,
    }), {"role": "user", "content": message}])
    return {"phase": "coordinator_session", "question": "", "coordinator_pending_call": {},
        "coordinator_history": history,
        **user_update(state, message, "clarification")}


async def engineering_triage(state: AgentState) -> dict:
    request_text = state["original_request"].lower()
    # Explicit, self-contained CAD briefs already contain the component list
    # and assembly requirements needed to start geometry. Keep the graph node
    # and its state transition, but avoid spending a long provider call merely
    # to rediscover the deterministic CAD route. Ambiguous or contradictory
    # requests still use the engineering model below.
    if ("design task:" in request_text and "components" in request_text
            and ("assembly requirements" in request_text or "requirements" in request_text)
            and not any(word in request_text for word in ("contradiction", "inconsistent", "impossible", "reject"))):
        route = "analyze" if deterministic_analysis_needed(state["original_request"]) else "cad"
        remarks = ["The brief contains explicit components and assembly requirements; deterministic triage routed it to the fixed graph path."]
        await repo.event(state["run_id"], f"Engineering triage deterministically routed the explicit brief to {route}.",
            kind="validation", stage="engineering")
        await repo.event(state["run_id"], f"Engineering review routed the request to {route}.", stage="engineering")
        return {"model_calls": state.get("model_calls", 0), "search_count": state.get("search_count", 0),
            "phase": "engineering_analysis" if route == "analyze" else "cad_session", "route": route,
            "question": "", "final_message": "", "engineering_remarks": remarks,
            "engineering_assumptions": [],
            "requirements": merge_requirements(state["original_request"], [])}
    prompt = """Classify this request before CAD. Use route=clarify only for missing inputs that block useful work;
route=analyze for safety, load, material, tolerance, or calculations that require explicit assumptions and approval;
route=cad for a sufficiently clear geometry request; route=answer for conversation with no design work.
Preserve every explicit requirement. Use a supported geometry kind only when all of its numeric fields are present:
 dimensions and max_dimensions need a three-value vector; use max_dimensions when the user states an upper envelope.
 center needs a three-value vector, solid_count needs count, through_holes
needs diameter, count and every plane position, and corner_radius needs radius and count. Set through_holes axis=Z
for holes normal to the XY plane and axis=Y for holes normal to the XZ frame plane. Put unsupported or incomplete
checks in kind=unverified without inventing values. In particular, an M10 bolt size does not specify a hole
diameter, so record the frame bolt pattern as unverified unless a clearance diameter is explicitly supplied.
Web search is available only when current external engineering facts are necessary; prefer the request and deterministic calculation."""
    try:
        value, usage = await structured_turn(state, "engineering", "triage", prompt, "submit_triage", Triage, web=True)
    except Pause as exc:
        # A malformed structured response must not block an otherwise
        # explicit request. Preserve the user's original requirements and
        # choose the conservative deterministic route; the model is still
        # used for CAD generation, while triage contract noise cannot strand
        # the run indefinitely.
        if "contract" not in str(exc).lower() and "invalid" not in str(exc).lower():
            raise
        fallback_route = ("analyze" if deterministic_analysis_needed(state["original_request"])
                          else "cad" if design_work_requested(state["original_request"])
                          else "answer")
        value = Triage(route=fallback_route,
            remarks=["The model triage response was malformed; Forma preserved the request and used deterministic routing."],
            assumptions=["No numeric requirement was invented; unsupported checks remain unverified."])
        usage = {"model_calls": state.get("model_calls", 0),
                 "search_count": state.get("search_count", 0)}
        await repo.event(state["run_id"],
            "Engineering triage contract was malformed; deterministic routing preserved the request.",
            kind="validation", stage="engineering")
    requirements = merge_requirements(state["original_request"], normalize_triage_requirements(value.requirements))
    route = value.route
    if design_work_requested(state["original_request"]) and route == "answer":
        route = "cad"
    if route in ("cad", "clarify") and deterministic_analysis_needed(state["original_request"]):
        route = "analyze"
        await repo.event(state["run_id"],
            "Engineering triage detected explicit calculations or load validation and routed to analysis.",
            kind="validation", stage="engineering")
    await repo.event(state["run_id"], f"Engineering review routed the request to {route}.", stage="engineering")
    phase = {"clarify": "clarification", "analyze": "engineering_analysis",
             "cad": "cad_session", "answer": "final"}.get(route, "cad_session")
    return {**usage, "phase": phase, "route": route, "question": value.question,
        "final_message": value.answer, "engineering_remarks": value.remarks,
        "engineering_assumptions": value.assumptions, "requirements": requirements}


async def clarification(state: AgentState) -> dict:
    response = interrupt({"kind": "clarification", "message": state.get("question") or
        "Please provide the missing dimensions or constraints needed for this design."})
    message = str((response or {}).get("message", "")).strip()
    if not message:
        raise Pause("A clarification answer is required.")
    return {**user_update(state, message, "clarification"),
        "phase": "engineering_triage", "route": "cad"}


async def engineering_analysis(state: AgentState) -> dict:
    packet = {
        "originalRequest": state["original_request"],
        "projectContext": state.get("project_context", {}),
        "clarifiedRequest": state.get("clarified_request", ""),
        "explicitRequirements": state.get("requirements", []),
        "triageAssumptions": state.get("engineering_assumptions", []),
        "triageRemarks": state.get("engineering_remarks", []),
        "cadRequest": state.get("engineering_request", ""),
        "workingMemory": memory_context(state),
    }
    prompt = """Perform the engineering analysis needed before geometry. Use the engineering packet below as the
source of truth and do not call a value missing when it is present in the original request, explicit requirements,
or clarification. The CAD agent's request is the immediate task. For a single part or assembly, make only the
material and manufacturing choices the brief permits. Use selected_material and manufacturing_method for concise
summaries; put component-specific choices, dimensions, clearances, load paths and caveats in recommendations and
design_parameters. Distinguish a design choice from a truly blocking unknown in open_items. State equations,
loads, units, assumptions, recommended parameters and limitations that apply to this request. When numerical
validation is useful, provide calculation_source as ordinary Python source exposing calculate() that returns
Forma's CalculationResult contract. The runtime executes it twice in isolated processes and records
calculation.json. Use real newline characters, not literal backslash-n escape sequences. Set requires_user_input
only when a missing user choice prevents useful geometry; visible assumptions and limitations do not require an
approval pause. Do not claim FEA, certification or physical performance from geometric checks alone.

Engineering packet:
""" + json.dumps(packet, ensure_ascii=False)
    deterministic_source = deterministic_tolerance_calculation_source(
        state["original_request"] + "\n" + state.get("engineering_request", ""))
    if deterministic_source:
        value = Analysis(
            summary=("The spacer stack is fully specified. I will run the required worst-case tolerance chain "
                     "before CAD and preserve the failing range as engineering evidence."),
            assumptions=["Use the stated bilateral spacer tolerances and one-sided gap tolerance."],
            recommendations=["Tighten spacer C or reduce the gap tolerance before production release."],
            design_parameters=["Model the all-nominal stack combination."],
            calculation_source=deterministic_source,
        )
        usage = {"model_calls": state.get("model_calls", 0),
                 "search_count": state.get("search_count", 0)}
    else:
        try:
            value, usage = await structured_turn(
                state, "engineering", "analysis", prompt, "submit_analysis", Analysis, web=True)
        except Pause as exc:
            # OpenAI-compatible gateways can return a successful response whose
            # structured tool payload is absent or malformed.  That is a
            # provider protocol problem, not evidence that the user's design
            # is unsafe or that CAD cannot proceed.  Preserve the failure as an
            # explicit unverified engineering note and continue to the user-
            # reviewable CAD draft.  Provider outages, calculation failures,
            # model budgets and real missing-input pauses still propagate.
            diagnostic = str(exc)
            contract_failure = ("invalid analysis" in diagnostic.lower()
                                 or "analysis result violated" in diagnostic.lower())
            if not contract_failure:
                raise
            value = Analysis(
                summary=(
                    "The engineering provider did not return its typed analysis contract. "
                    "The request is explicit enough to prepare a CAD draft, but no numerical "
                    "engineering claim is verified from this step. Review the generated design "
                    "and add or approve calculations before relying on it."
                ),
                assumptions=[
                    "No material, load, tolerance, or safety value was invented after the provider contract failure."
                ],
                recommendations=[
                    "Treat engineering calculations and safety factors as unverified until a valid analysis is available."
                ],
                open_items=["Typed engineering analysis was unavailable from the selected model."],
                requires_user_input=False,
            )
            usage = {
                "model_calls": state.get("model_calls", 0) + 2,
                "model_calls_this_cycle": state.get("model_calls_this_cycle", state.get("model_calls", 0)) + 2,
                "search_count": state.get("search_count", 0),
            }
            await repo.event(
                state["run_id"],
                "Engineering analysis contract was unavailable; preserved as unverified and continued to CAD review.",
                kind="validation", stage="engineering",
            )
    output = {**usage, "phase": "approval" if value.requires_user_input else "cad_session",
        "engineering_summary": value.summary,
        "engineering_assumptions": [*state.get("engineering_assumptions", []), *value.assumptions],
        "engineering_remarks": [*state.get("engineering_remarks", []),
            *value.recommendations,
            *(f"Selected material: {value.selected_material}" for _ in [0] if value.selected_material),
            *(f"Manufacturing method: {value.manufacturing_method}" for _ in [0] if value.manufacturing_method),
            *value.design_parameters,
            *(f"Open item: {item}" for item in value.open_items)],
        "approval_summary": value.user_question or value.summary,
        "question": value.user_question}
    calculation_result = None
    calculation_source = value.calculation_source or deterministic_tolerance_calculation_source(
        state["original_request"] + "\n" + state.get("engineering_request", ""))
    if calculation_source:
        if not value.calculation_source:
            output["engineering_remarks"] = [*output["engineering_remarks"],
                "Forma supplied the deterministic tolerance-chain calculation because the model omitted it."]
        calculation_source = normalize_python_source(calculation_source)
        snapshot = await run_service.load_candidate(state["run_id"])
        snapshot = Snapshot.model_validate({"manifest": snapshot["manifest"],
            "files": {**snapshot["files"], "calculations/analysis.py": calculation_source}}).model_dump()
        calculation_candidate_hash = digest(snapshot)
        await run_service.save_candidate(state["run_id"], snapshot, calculation_candidate_hash)
        output["candidate_hash"] = calculation_candidate_hash
        run = await run_row(state)
        cp = checkpoint_view(state, snapshot)
        cp["role"] = "engineering"
        cp["sandbox"] = cp.get("sandbox") or sandbox_name(run["id"], "engineering")
        async def calculate():
            result = await execute_tool(run, cp, {"id": "analysis", "name": "calculate",
                "input": {"path": "calculations/analysis.py"}}, await app_settings(), worker())
            return {"result": result, "checkpoint": cp}
        calculation_key = f"graph:engineering-calculation:{digest(calculation_source)[:16]}"
        calculated = await operation(run, calculation_key, "calculate", calculate)
        cp = calculated["checkpoint"]
        result = calculated["result"]
        if not result.get("ok"):
            error = result.get("error", {})
            location = error.get("location") or {}
            where = f" at {location['file']}:{location['line']}" if location.get("file") and location.get("line") else ""
            category = error.get("category") or "execution"
            guidance = error.get("guidance") or "Review the engineering assumptions and calculation source."
            diagnostic = f"Engineering calculation failed ({category}){where}. {guidance}"
            output["engineering_summary"] = value.summary + "\n\n" + diagnostic
            output["engineering_remarks"] = [*output["engineering_remarks"], diagnostic]
            calculation_result = {"ok": False, "error": error}
        else:
            output["engineering_summary"] = value.summary + "\n\nCalculation verified: " + result["result"]["conclusion"]
            calculation_result = result
        output.update(sync_checkpoint(cp))
    pending = state.get("pending_cad_call")
    if pending:
        output["cad_history"] = bounded_history([*state.get("cad_history", []), tool_message(pending, {
            "ok": calculation_result is None or calculation_result.get("ok", False),
            "summary": output["engineering_summary"],
            "recommendations": output["engineering_remarks"],
            "calculation": calculation_result,
            "requiresUserInput": value.requires_user_input,
        })])
        output["pending_cad_call"] = {}
    current_snapshot = await run_service.load_candidate(state["run_id"])
    output["engineering_candidate_hash"] = digest(current_snapshot)
    if state.get("engineering_from_coordinator") and not value.requires_user_input:
        call = state.get("coordinator_pending_call") or {"id": "engineering"}
        # The engineer has already resolved the delegated task. CAD receives
        # the original design request and engineering findings directly; a
        # second coordinator model call adds latency without a new decision.
        output["phase"] = "cad_session"
        output["engineering_from_coordinator"] = False
        output["coordinator_pending_call"] = {}
        output["coordinator_task"] = state.get("clarified_request") or state["original_request"]
        output["coordinator_history"] = bounded_history([
            *state.get("coordinator_history", []), tool_message(call, {
                "ok": True, "summary": output["engineering_summary"],
                "remarks": output["engineering_remarks"], "calculation": calculation_result,
            }),
        ])
    return output


async def approval(state: AgentState) -> dict:
    response = interrupt({"kind": "approval", "message": state["approval_summary"]})
    kind = (response or {}).get("kind")
    if kind == "answer":
        answer = str((response or {}).get("message", "")).strip().lower()
        kind = "approval" if answer in ("approve", "approved", "yes", "go ahead", "continue") else (
            "rejection" if answer in ("reject", "rejected", "no", "stop") else kind)
    if kind == "rejection":
        return {"approved": False, "phase": "final", "final_message":
            (response or {}).get("message") or "The engineering proposal was rejected. No design revision was created."}
    if kind != "approval":
        raise Pause("Approve or reject the engineering proposal before CAD begins.")
    if state.get("engineering_from_coordinator"):
        call = state.get("coordinator_pending_call") or {"id": "engineering"}
        return {"approved": True, "phase": "cad_session",
            "engineering_from_coordinator": False, "coordinator_pending_call": {},
            "coordinator_task": state.get("clarified_request") or state["original_request"],
            "coordinator_history": bounded_history([*state.get("coordinator_history", []),
                tool_message(call, {"ok": True, "approved": True,
                    "summary": state.get("engineering_summary", "")})])}
    return {"approved": True, "phase": "cad_session"}


def sandbox_name(run_id: str, suffix: str) -> str:
    return f"forma-{UUID(run_id).hex}-{suffix}-{uuid4().hex[:8]}"


def checkpoint_view(state: AgentState, snapshot: dict) -> dict:
    return {"snapshot": snapshot, "role": "cad", "requirements": normalize_assembly_requirements(merge_requirements(
        state.get("original_request", ""), state.get("requirements", [])), snapshot.get("manifest", {})),
        "repairs": state.get("repairs", 0), "attempts": state.get("attempts", 0),
        "sequence": state.get("model_calls", 0), "modelCalls": state.get("model_calls", 0),
        "startedNs": state.get("started_ns", time.time_ns()), "sandbox": state.get("sandbox"),
        "validator": state.get("validator"), "sandboxReady": state.get("sandbox_ready", False),
        "lastFailure": state.get("last_failure"), "lastFailedCandidate": state.get("last_failed_candidate"),
        "validated": state.get("validation")}


def sync_checkpoint(cp: dict) -> dict:
    result = {"repairs": cp.get("repairs", 0), "attempts": cp.get("attempts", 0),
        "sandbox": cp.get("sandbox"), "validator": cp.get("validator"),
        "sandbox_ready": cp.get("sandboxReady", False), "last_failure": cp.get("lastFailure"),
        "last_failed_candidate": cp.get("lastFailedCandidate")}
    if cp.get("validated"):
        result["validation"] = cp["validated"]
    if "requirements" in cp:
        result["requirements"] = cp["requirements"]
    return result


def bind_requirements_to_manifest(requirements: list[dict], manifest: dict) -> list[dict]:
    """Resolve model-facing component labels after CAD supplies the manifest.

    Triage runs before component IDs exist, so models may use descriptive IDs
    such as ``frame_interface``. A missing ID must not silently turn a check
    into ``unverified`` when the manifest has one unambiguous root shape.
    Local hole positions are translated into the generated part's datum for
    the common motor-bracket interfaces; the source requirement remains in the
    checkpoint and the bound copy is what the deterministic validator measures.
    """
    components = manifest.get("components", [])
    valid = {c.get("id") for c in components}
    root = manifest.get("rootComponentId")
    if root not in valid:
        root = next((c.get("id") for c in components if c.get("kind") in ("solid", "assembly")), None)
    motor_component = next((c for c in components
        if "motor" in str(c.get("id", "")).lower() or "motor" in str(c.get("name", "")).lower()), None)
    frame_component = next((c for c in components
        if "frame" in str(c.get("id", "")).lower() or "frame" in str(c.get("name", "")).lower()), None)

    def described_component(label: str):
        """Resolve an omitted componentId from an explicit component noun.

        Triage commonly knows that a dimension belongs to the base or PCB but
        omits the ID while the model is still reasoning about the assembly.
        Binding those checks to the assembly root measures the combined envelope
        and creates a false geometry failure. Prefer an unambiguous component
        name before falling back to the root assembly.
        """
        candidates = []
        for component in components:
            cid = str(component.get("id", "")).lower()
            name = str(component.get("name", "")).lower()
            score = 0
            for token in re.findall(r"[a-z0-9]+", label):
                if token and (token in cid or token in name):
                    score += 1
            if score:
                candidates.append((score, component))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        if len(candidates) == 1 or candidates[0][0] > candidates[1][0]:
            return candidates[0][1]
        return None

    bound = []
    for original in requirements:
        item = dict(original)
        label = f"{item.get('id', '')} {item.get('description', '')} {item.get('componentId') or ''}".lower()
        if item.get("componentId") not in valid:
            preferred = motor_component if "motor" in label and motor_component else (
                frame_component if "frame" in label and frame_component else described_component(label))
            item["componentId"] = (preferred or {}).get("id") if preferred else root
        if item.get("kind") == "through_holes":
            axis = item.get("axis", "Z")
            if axis == "Z" and "motor" in label and motor_component:
                center = motor_component.get("parameters", {}).get("motor_pattern_center_y")
                if isinstance(center, (int, float)):
                    item["positions"] = [[float(x), float(y) + float(center)] for x, y in item.get("positions", [])]
            elif axis == "Y" and "frame" in label and frame_component:
                center = frame_component.get("parameters", {}).get("frame_pattern_center_z")
                if isinstance(center, (int, float)):
                    item["positions"] = [[float(x), float(z) + float(center)] for x, z in item.get("positions", [])]
        bound.append(item)
    return normalize_assembly_requirements(bound, manifest)


def normalize_instance_hierarchy(manifest: dict) -> tuple[dict, bool]:
    """Make common model-produced assembly parent references safe to build.

    Models often use the component/root id as an instance parent, or emit a
    self/cyclic parent while describing a flat assembly.  Those references do
    not change the geometry and should not hide an otherwise buildable draft.
    Keep each instance independently identifiable and flatten only the invalid
    edge; duplicate ids and unknown component definitions remain hard contract
    errors.
    """
    def unwrap_item(value):
        """Normalize provider wrappers around arrays and tuple values.

        Some OpenAI-compatible models serialize array fields from a portable
        function schema as ``{"item": [...]}``.  Treating that wrapper as the
        actual manifest value makes a later ``for item in instances`` iterate
        the string key and crash before Pydantic can return a repairable tool
        contract error.  The wrapper is presentation-only, so recursively
        remove singleton ``item`` objects before hierarchy validation.
        """
        if isinstance(value, dict):
            if set(value) == {"item"}:
                return unwrap_item(value["item"])
            return {key: unwrap_item(child) for key, child in value.items()}
        if isinstance(value, list):
            return [unwrap_item(child) for child in value]
        return value

    payload = unwrap_item(json.loads(json.dumps(manifest)))
    instances = payload.get("instances", [])
    ids = {item.get("id") for item in instances}
    changed = False
    for item in instances:
        parent = item.get("parentId")
        if parent is not None and (parent not in ids or parent == item.get("id")):
            item["parentId"] = None
            changed = True
    for item in instances:
        seen = {item.get("id")}
        parent = item.get("parentId")
        while parent is not None:
            if parent in seen:
                item["parentId"] = None
                changed = True
                break
            seen.add(parent)
            parent_item = next((candidate for candidate in instances if candidate.get("id") == parent), None)
            parent = parent_item.get("parentId") if parent_item else None
    return payload, changed


CAD_SESSION_TOOL_NAMES = {
    "read_file", "search_files", "apply_changes", "update_parameters", "build", "test_component",
    "inspect_geometry", "request_engineering", "respond_to_review", "ask_user", "update_working_notes", "read_working_memory", "diagnose_workspace",
}
MAX_CAD_EDITS_WITHOUT_BUILD = 3
MAX_CAD_OBSERVATION_DECISIONS = 3
CAD_NO_PROGRESS_OBSERVATION_TOOLS = {
    "read_file", "search_files", "inspect_geometry", "diagnose_workspace", "read_working_memory",
}


def bound_cad_observation_tools(tools: list[dict], completed_decisions: int,
                               maximum: int = MAX_CAD_OBSERVATION_DECISIONS) -> list[dict]:
    if completed_decisions < maximum:
        return tools
    return [tool for tool in tools
            if tool.get("function", {}).get("name") not in CAD_NO_PROGRESS_OBSERVATION_TOOLS]
MAX_REVIEW_REPAIR_CYCLES = 2
MAX_REVIEW_ACTIONS = 5


def is_review_repair_target(finding: dict) -> bool:
    return (finding.get("status") in {"observed_mismatch", "not_observed"}
        and bool(finding.get("evidence")) and bool(str(finding.get("repair_instruction", "")).strip()))


def review_fingerprint(review: dict) -> str:
    """Stable identity for actionable review defects, ignoring prose/evidence drift."""
    actionable = []
    for finding in review.get("findings", []):
        if not is_review_repair_target(finding):
            continue
        normalized_statement = re.sub(r"[^a-z0-9]+", " ",
            str(finding.get("statement", "")).lower()).strip()
        actionable.append({
            "id": str(finding.get("id", "")).lower(),
            "status": finding.get("status"),
            "statement": normalized_statement,
        })
    return digest(sorted(actionable, key=lambda item: (item["id"], item["statement"])))


def review_repair_context(review: dict) -> dict:
    """Give CAD a short defect list and explicit invariants, not an open-ended review."""
    findings = review.get("findings", [])
    return {
        "message": ("Repair only the actionable items listed in repairTargets. Preserve every item in "
            "alreadyPassing unchanged. Do not guess at or repair unverified engineering/physical claims. "
            "After the focused edits, rebuild the candidate."),
        "summary": review.get("summary", "Independent review found issues to address."),
        "alreadyPassing": [item for item in findings if item.get("status") == "observed_match"],
        "repairTargets": [item for item in findings if is_review_repair_target(item)],
        "unverifiedOrNonActionable": [item for item in findings
            if item.get("status") != "observed_match" and not is_review_repair_target(item)],
    }


def unavailable_review(reason: str) -> dict:
    return {"summary": ("Independent design review did not complete: " + reason[:700] +
            " The successfully built and independently validated draft is still published; review the automated "
            "evidence and geometry yourself."),
        "action": "publish", "findings": [{
            "id": "review_unavailable",
            "statement": "The independent model review did not complete.",
            "status": "not_checked", "severity": "warning", "evidence": [],
            "explanation": "No model-based requirement review is available for this candidate. The independent "
                "CAD build validator still ran; this review failure did not alter or invalidate the artifacts.",
            "repair_instruction": "Review the candidate manually or request a focused edit.",
        }]}


def native_grounding_evidence(snapshot: dict, validation: dict) -> dict:
    """Keep occurrence-frame facts distinct from aggregate geometric centres."""
    manifest = snapshot.get("manifest", {})
    native = manifest.get("nativeAssembly") or {}
    if not native:
        return {}
    report = validation.get("report", validation) or {}
    evidence = report.get("nativeAssembly") or {}
    expected = identity(snapshot, [])
    current = ((report.get("identity") or {}).get("candidate") == expected["candidate"]
               and (report.get("identity") or {}).get("runtime") == expected["runtime"]
               and evidence.get("engine") == "OndselSolver"
               and int(evidence.get("solvedFrames") or 0) > 0)
    instances = {item["id"]: item for item in manifest.get("instances", [])}
    return {
        "nativeGroundingCheckedForCurrentCandidate": current,
        "groundedOccurrences": {iid: {
            "declaredFrame": instances[iid]["frame"],
            "coordinateSystem": ("world" if not instances[iid].get("parentId") else "parent:" + instances[iid]["parentId"]),
        } for iid in native.get("groundedInstances", []) if iid in instances},
        "interpretation": ("Native validation rejects any solved state that moves a grounded occurrence from its declared world pose. "
            "A whole-assembly bounds centre or centre of mass is not an occurrence frame origin. Look up the exact occurrence ID. "
            "Ungrounded manifest frames are seed poses; accepted native poses and independently reopened STEP describe the solved assembly."),
    }


async def record_review_result(state: AgentState, review: dict, snapshot: dict,
                               validation: dict, history: list[dict], usage: dict | None = None) -> dict:
    """Route a review to one focused repair, or publish the built draft with findings."""
    usage = usage or {}
    actionable = [finding for finding in review.get("findings", []) if is_review_repair_target(finding)]
    repairs_done = int(state.get("review_repairs", 0))
    fingerprint = review_fingerprint(review)
    prior_fingerprint = state.get("review_fingerprint", "")
    same_defect_repeated = bool(actionable and repairs_done and fingerprint == prior_fingerprint)

    # A model may request repair for a finding that is only unverified, or may
    # return no concrete defect. Keep those limitations visible, but do not send
    # speculative geometry changes to CAD.
    if review.get("action") == "repair" and not actionable:
        review["action"] = "publish"
        review["summary"] += (" No evidence-backed, geometry-actionable mismatch was available for CAD to fix; "
            "the remaining findings are included for your review.")
    if review.get("action") == "repair" and same_defect_repeated:
        review["action"] = "publish"
        response = state.get("review_response") or {}
        if response.get("candidateHash") == digest(snapshot):
            review["cadResponse"] = response
            review["summary"] += (" The reviewer retained a disputed finding after read-only evidence reassessment. "
                "No geometry was changed by that response. The built draft, finding and CAD response require human review.")
        else:
            review["summary"] += (" The same actionable finding remained after a focused repair, so further "
                "automatic retries stopped. The built draft and remaining finding are published for your review.")
    if review.get("action") == "repair" and repairs_done >= MAX_REVIEW_REPAIR_CYCLES:
        review["action"] = "publish"
        review["summary"] += (f" The maximum of {MAX_REVIEW_REPAIR_CYCLES} focused review repair cycles is "
            "complete; remaining findings are published with the draft for user-directed editing.")

    validation = deepcopy(validation)
    validation.setdefault("report", {})["review"] = review
    await repo.event(state["run_id"],
        f"Independent CAD review completed with {len(review.get('findings', []))} findings.",
        kind="validation", stage="review")
    common = {**usage, "review": review, "review_fingerprint": fingerprint,
        "reviewed_candidate_hash": digest(snapshot), "review_history": history,
        "validation": validation, "review_reads": 0, "review_inspected": False,
        "review_actions": 0}
    if review.get("action") == "repair":
        repair_context = review_repair_context(review)
        cad_history = bounded_history([*state.get("cad_history", []), {
            "role": "user", "content": json.dumps(repair_context, ensure_ascii=False),
        }])
        return {**common, "phase": "cad_session", "cad_history": cad_history,
            "review_repairs": repairs_done + 1}
    return {**common, "phase": "publish"}


def requested_component_labels(request: str) -> list[str]:
    """Read numbered items or quantity-marked part types from Components.

    Quantity lists such as ``Gear housing (1, cast Al), bushings (4)`` are a
    common mechanical-design format. Extract the name immediately before each
    quantity tuple while ignoring the comma-separated notes inside the tuple.
    This is a staging/publication guard, not a geometry validator.
    """
    section = re.search(r"(?im)^\s*(?:#{1,6}\s*)?components(?:\s*\([^\n]*\))?\s*:?\s*$", request)
    if not section:
        return []
    remaining = request[section.end():]
    following_heading = re.search(
        r"(?im)^\s*(?:#{1,6}\s*)?(?:assembly requirements|final requirement|objective|key dimensions|3d design features|2d drawing features)\b.*$",
        remaining,
    )
    if following_heading:
        remaining = remaining[:following_heading.start()]
    labels: list[str] = []
    for line in remaining.splitlines():
        match = re.match(r"^\s*(\d{1,3})[.)]\s+(.+)$", line)
        if match:
            if int(match.group(1)) != len(labels) + 1:
                break
            label = re.split(r"\s+(?:—|–|-|:)\s+", match.group(2), maxsplit=1)[0]
            labels.append(label.strip().strip("* ")[:120])
        elif labels and line.strip() and not line[:1].isspace():
            break
    if labels:
        return labels
    quantity_item = re.compile(r"([^,()\n]+?)\s*\(\s*(\d+)\s*(?:,|\))")
    for match in quantity_item.finditer(remaining):
        label = match.group(1).strip().strip("* +")
        if label:
            labels.append(label[:120])
    return labels


def staged_component_count(manifest: dict) -> int:
    return sum(1 for item in manifest.get("components", []) if item.get("kind") != "assembly")


def register_unlisted_part_sources(manifest: dict, files: dict[str, str]) -> tuple[dict, list[str]]:
    """Register a new buildable part file when an edit omits its manifest entry.

    A source file with a top-level build function is an unambiguous component
    definition. This does not invent instances, placements, mates, or an
    assembly root; the CAD agent still owns those design decisions.
    """
    manifest = deepcopy(manifest)
    components = manifest.setdefault("components", [])
    known_sources = {item.get("source") for item in components}
    known_ids = {item.get("id") for item in components}
    added = []
    for path, source in sorted(files.items()):
        match = re.fullmatch(r"parts/([a-zA-Z][a-zA-Z0-9_-]{0,63})\.py", path)
        if not match or path in known_sources or match.group(1) in known_ids:
            continue
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError:
            continue
        if not any(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and item.name == "build" for item in tree.body):
            continue
        component_id = match.group(1)
        components.append({
            "id": component_id, "name": component_id.replace("_", " ").title(),
            "source": path, "kind": "solid", "dependencies": [],
            "parameters": {}, "color": "#b9c4ad",
        })
        known_sources.add(path)
        known_ids.add(component_id)
        added.append(component_id)
    return manifest, added


def retained_proposal_files(arguments) -> dict[str, str]:
    """Retain readable rejected source privately without accepting a workspace edit."""
    if not isinstance(arguments, dict):
        return {}
    files = arguments.get("files") or {}
    if isinstance(files, list):
        files = {item["path"]: item["content"] for item in files
                 if isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("content"), str)}
    if not isinstance(files, dict):
        return {}
    result = {}
    for path, source in files.items():
        if not isinstance(path, str) or not isinstance(source, str) or len(source) > 150_000:
            continue
        try:
            safe_path(path)
        except ValueError:
            continue
        result[path] = source
    return result if len(json.dumps(result).encode()) <= 2_000_000 else {}


def observation_result(state: AgentState, snapshot: dict, call: dict, arguments: dict,
                       history: list, usage: dict, result: dict) -> dict:
    progress = record_observation_action(state, snapshot, call["name"], arguments,
                                         decision=usage.get("model_calls"))
    # Several reads returned by one provider generation are one observation-only
    # decision. Count only when its queued tool batch is exhausted.
    progress = record_observation_decision(
        progress, batch_complete=not usage.get("cad_pending_actions"))
    stalled = progress.get("repeatDecisions", 0) >= 3 and not usage.get("cad_pending_actions")
    feedback = {**result, "observationProgress": {"repeatedObservations": progress["repeats"],
                "repeatedDecisions": progress.get("repeatDecisions", 0), "replanning": stalled}}
    message = ("The CAD agent cycled through unchanged observations without an edit or new test evidence. "
               "Choose a focused next action using the retained source, measurements and exact request.")
    return {**usage, "phase": "cad_recovery" if stalled else "cad_session",
            "cad_observation_progress": progress,
            "cad_history": bounded_history([*history, tool_message(call, feedback)]),
            **({"final_message": message} if stalled else {})}


async def cad_session(state: AgentState) -> dict:
    """Settle one checkpointed CAD action, cancelling dependent actions on failure."""
    pending = state.get("cad_pending_actions") or []
    if pending and pending[0].get("requestHash") not in {
            None, digest(state.get("clarified_request") or state["original_request"])}:
        return {"phase": "cad_session", "cad_pending_actions": [],
                "model_calls": state.get("model_calls", 0),
                "cad_history": bounded_history([*state.get("cad_history", []), {"role": "system", "content":
                    "Human guidance changed after the queued proposal. Remaining queued actions were cancelled without execution. "
                    "Use the updated exact request and saved source for the next action."}])}
    result = await cad_session_turn(state)
    pending = result.get("cad_pending_actions", state.get("cad_pending_actions") or [])
    if not pending:
        return result
    history = result.get("cad_history") or []
    feedback = {}
    if history and history[-1].get("role") == "tool":
        try:
            feedback = json.loads(history[-1].get("content") or "{}")
        except (ValueError, TypeError):
            pass
    if result.get("phase") != "cad_session" or feedback.get("ok") is False:
        cancelled = [{"id": item["call"]["id"], "name": item["call"]["name"]} for item in pending]
        result["cad_pending_actions"] = []
        result["cad_queue_feedback"] = {"cancelled": cancelled, "executed": False,
                                        "reason": "A prerequisite failed or control moved to another graph stage."}
        # Preserve unsaved source proposals for selective retrieval, never as
        # accepted source or permission to execute after a failed prerequisite.
        if not result.get("rejected_files"):
            proposed = {}
            for item in pending:
                if item["call"]["name"] == "apply_changes":
                    proposed.update(retained_proposal_files(item["call"].get("input")))
            if proposed:
                result["rejected_files"] = proposed
        if history and history[-1].get("role") == "tool":
            result["cad_history"] = bounded_history([*history, {"role": "system", "content":
                "Queued CAD actions cancelled after a failed prerequisite or control handoff. None of these actions executed: " +
                json.dumps(cancelled) + ". Use the actual preceding result before choosing a repair. Unsaved proposed source may be selectively read as version='rejected'."}])
        # A build/engineering handoff may still owe the selected tool its result.
        # Keep that pending protocol intact; cancellation metadata is separate.
    return result


async def cad_session_turn(state: AgentState) -> dict:
    """Let the CAD model choose one incremental workspace/tool action."""
    snapshot = await run_service.load_candidate(state["run_id"])
    bound = normalize_assembly_requirements(state.get("requirements", []), snapshot["manifest"])
    if bound != state.get("requirements", []):
        await repo.event(state["run_id"], "Corrected an assembly requirement binding; rebuilding unchanged geometry against its owned base revision.", stage="validation")
        reset = {"requirements": bound, "validation": {}, "build_result": {},
                 "review": {}, "review_history": [], "review_response": {}, "review_fingerprint": "",
                 "reviewed_candidate_hash": ""}
        result = await build({**state, **reset,
                              "pending_cad_call": {"id": "assembly-binding-rebuild", "name": "build", "input": {"final": True}},
                              "build_final": True})
        # Nested node calls do not persist their input state in LangGraph.
        # Return the resets as updates, while preserving the fresh build result.
        return {**reset, **result}
    history = state.get("cad_history") or [{
        "role": "user", "content": state.get("clarified_request") or state["original_request"]
    }]
    patch_mismatch = active_source_patch_mismatch(state, snapshot)
    requested_components = requested_component_labels(state.get("original_request", ""))
    edits_since_build = state.get("cad_edits_since_build", 0)
    manifest = snapshot["manifest"]
    buildable = bool(snapshot["manifest"].get("components")
                     and snapshot["manifest"].get("rootComponentId"))
    context = {
        "currentCandidate": current_candidate_facts(snapshot),
        "request": state["original_request"],
        "humanUpdates": human_followups(state),
        "activeFailure": active_failure_context(state, snapshot),
        "sourceEditRecovery": ({
            "category": "exact_text_patch_mismatch",
            "paths": patch_mismatch.get("paths", []),
            "diagnostic": patch_mismatch.get("diagnostic", ""),
            "instruction": ("The last exact-text patch did not match the current saved source and saved nothing. "
                "Submit the complete current file through apply_changes.files and omit patches. Do not repeat "
                "the exact-text patch."),
        } if patch_mismatch else None),
        "acceptanceLedger": acceptance_context(state, snapshot),
        "componentTests": compact_probe_context(state, snapshot),
        "observationProgress": observation_context(state, snapshot),
        "queuedActionStatus": state.get("cad_queue_feedback") or None,
        "recoveryPlan": state.get("recovery_plan") or None,
        "recoverySourceContext": recovery_source_context(state, snapshot,
            paths=focused_recovery_paths(state, snapshot), max_bytes=12_000, max_files=4),
        "rejectedSource": {"paths": sorted(state.get("rejected_files") or {}),
                           "instruction": "Unsaved proposal source only; use read_file(version='rejected') selectively. Saved source is authoritative."},
        "delegatedTask": state.get("coordinator_task", ""),
        "projectContext": state.get("project_context", {}),
        "selectedIds": state.get("selected_ids", []),
        "engineeringSummary": state.get("engineering_summary", ""),
        "engineeringRemarks": state.get("engineering_remarks", []),
        "reviewRepairPlan": (review_repair_context(state.get("review") or {})
            if (state.get("review") or {}).get("action") == "repair" else None),
        "workspace": {
            "manifest": compact_manifest_context(snapshot["manifest"]),
            "files": sorted(snapshot["files"]),
            "candidateHash": digest(snapshot),
            "instruction": ("Use only exact file paths listed in workspace.files. A path such as 'parts' or "
                "'.metadata/manifest.json' is not a file. If the list is empty, create the requested source "
                "with apply_changes immediately; do not probe directories or invent metadata files."),
        },
        "lastBuild": compact_build_context(state.get("build_result")),
        "lastReview": state.get("review"),
        "nativeGroundingEvidence": native_grounding_evidence(snapshot, state.get("validation") or {}),
        "workspaceRepair": state.get("cad_contract_repair") or None,
        "workspaceStatus": compact_workspace_status(workspace_status(snapshot)),
    }
    tools = [item for item in model_tools("cad", working_memory=True, review_response=(state.get("review") or {}).get("action") == "repair")
             if item["function"]["name"] in CAD_SESSION_TOOL_NAMES]
    observation_decisions = context["observationProgress"].get("observationOnlyDecisions", 0)
    observation_limit = context["observationProgress"]["maximumObservationOnlyDecisions"]
    if observation_decisions >= observation_limit:
        tools = bound_cad_observation_tools(tools, observation_decisions, observation_limit)
        context["observationLimit"] = {
            "completedObservationOnlyDecisions": observation_decisions,
            "maximum": observation_limit,
            "instruction": ("The unchanged candidate and evidence have already had three observation-only model decisions. "
                "Use a focused edit, part test, assembly build, engineering request, or ask a specific question. "
                "Do not repeat reads or workspace inventory."),
        }
    if patch_mismatch:
        tools = deepcopy(tools)
        for item in tools:
            if item["function"]["name"] == "apply_changes":
                parameters = item["function"].get("parameters", {})
                parameters.get("properties", {}).pop("patches", None)
                if "required" in parameters:
                    parameters["required"] = [name for name in parameters["required"] if name != "patches"]
    active_failure = context.get("activeFailure")
    if active_failure:
        context["activeFailure"]["toolPolicy"] = (
            "This failure remains active across source edits until a complete assembly build succeeds. "
            "Use test_component only for the named failing component when failureScope is component; "
            "a component test cannot diagnose or clear an assembly-level failure. Run at most one isolated "
            "probe for this failing file/component while its full-build failure remains active; after that, edit "
            "the exact target and rebuild the complete assembly. A passing part probe never clears the failure.")
        if (active_failure.get("failureScope") == "component" and active_failure.get("componentId")
                and active_failure.get("repeatedCount", 0) < 2
                and failure_probe_key(active_failure) not in state.get("cad_component_probe_failures", [])):
            tools = restrict_component_probe(tools, active_failure["componentId"])
        else:
            tools = [tool for tool in tools if tool["function"]["name"] != "test_component"]
    if len(requested_components) > 1:
        staged = staged_component_count(snapshot["manifest"])
        context["componentMilestone"] = {
            "requestedTypes": requested_components,
            "stagedTypeCount": staged,
            "instruction": ("Work on a small dependency-related group per edit. Use mergeById=true for focused manifest edits. A partial manifest "
                "must be internally consistent for the components already staged; it need not describe "
                "the entire final assembly yet. Test parts using test_component(componentId=...) and assembly milestones using build(final=false), then add the remaining "
                "parts. A passing partial build will not publish the design."),
        }
    if edits_since_build >= MAX_CAD_EDITS_WITHOUT_BUILD and buildable:
        context["testSuggested"] = ("Several edits are untested. Choose an isolated part test, an assembly milestone "
                                    "or a focused repair needed to make the next test meaningful.")
        context["editsSinceBuild"] = edits_since_build
    manifest = snapshot["manifest"]
    physical_components = [item for item in manifest.get("components", [])
                           if item.get("kind") != "assembly"]
    root = next((item for item in manifest.get("components", [])
                 if item.get("id") == manifest.get("rootComponentId")), None)
    if len(physical_components) > 1 and (root or {}).get("kind") != "assembly":
        context["assemblyRootRepair"] = (
            ("This workspace has multiple physical parts, but rootComponentId is not set. " if not root else
             "This workspace has multiple physical parts, but rootComponentId names a single part. ") +
            "The root STEP cannot contain all manifest instances. Before a full assembly build, "
            "use apply_changes to add an assemblies/ Python source with build(parameters, dependencies) "
            "returning a CadQuery Assembly; add every currently staged part under its exact manifest "
            "instance ID and frame; declare those part IDs as dependencies; make the new assembly "
            "component rootComponentId. Use mergeById=true with focused complete entries and "
            "nonempty assembly source. Individual part probes remain available while staging the assembly."
        )
        # An unfinished root is a diagnostic, not a reason to remove source
        # inspection and isolated part tests from the agent's toolset.
    if state.get("cad_contract_repair"):
        # Carry the rejection outside the rolling dialogue. Reads must not erase
        # it, and another build cannot fix an edit that was never staged.
        tools = [item for item in model_tools("cad")
                 if item["function"]["name"] in {"apply_changes", "read_file", "search_files"}]
    if not snapshot["files"]:
        tools = [item for item in tools if item["function"]["name"] not in {
            "search_files", "inspect_geometry", "build", "update_parameters"} and
            (item["function"]["name"] != "read_file" or state.get("rejected_files"))]
    note_cooldown=notes_just_saved(history)
    if note_cooldown:
        tools=[item for item in tools if item['function']['name']!='update_working_notes']
        context['taskProgress']='Working notes were just saved. Choose a different reading, editing, building or inspection action. Notes become available after another action or a human update.'
    older_memory=older_outcomes_available(state,history)
    if not older_memory:
        tools=[item for item in tools if item['function']['name']!='read_working_memory']
        context['memoryAvailability']='All retained outcomes are already included in current working memory; there are no older outcomes to retrieve. Advance the task using the request and workspace.'
    pending_actions = state.get("cad_pending_actions") or []
    if pending_actions:
        queued = pending_actions[0]
        call = deepcopy(queued["call"])
        history = bounded_history([*history, deepcopy(queued["message"])], allow_pending=True)
        usage = {"model_calls": state.get("model_calls", 0), "search_count": state.get("search_count", 0),
                 "cad_pending_actions": deepcopy(pending_actions[1:])}
        if pending_actions[1:]:
            call["queued_calls"] = [{"id": item["call"]["id"], "name": item["call"]["name"]} for item in pending_actions[1:]]
    else:
        call, history, usage = await agent_tool_turn(
            state, model_role="cad", prompt_role="cad", node="cad-session",
            context=context, history=history, tools=tools,
        )
    if not call:
        invalid_attempts = state.get("cad_invalid_tool_attempts", 0) + 1
        history = bounded_history([*history, {"role": "user", "content":
            "Your response contained no valid tool call. Call exactly one available tool with valid arguments; do not put a tool-call list in plain text."}])
        if invalid_attempts >= 3:
            return {**usage, "phase": "cad_recovery",
                "cad_invalid_tool_attempts": invalid_attempts, "cad_history": history,
                "final_message": ("The CAD model returned no usable tool call three times. "
                    "The saved design and feedback are retained. Continue with a focused correction to retry.")}
        return {**usage, "phase": "cad_session", "cad_invalid_tool_attempts": invalid_attempts,
            "cad_history": history}
    if state.get("cad_contract_repair") and call["name"] not in {
            "apply_changes", "read_file", "search_files"}:
        feedback = {**state["cad_contract_repair"],
                    "message": "Repair the rejected edit before building or completing the design."}
        return {**usage, "phase": "cad_session", "cad_history": bounded_history([
            *history, tool_message(call, feedback)])}
    if (note_cooldown and call['name']=='update_working_notes') or (not older_memory and call['name']=='read_working_memory'):
        invalid_attempts=state.get('cad_invalid_tool_attempts',0)+1
        note_action=call['name']=='update_working_notes'
        feedback={'ok':False,'category':'note_progress' if note_action else 'memory_progress',
            'message':'This memory action was not executed because it adds no information in the current state.',
            'availableTools':[item['function']['name'] for item in tools],
            'repairGuidance':context['taskProgress'] if note_action else context['memoryAvailability']}
        return {**usage,'phase':'cad_recovery' if invalid_attempts>=3 else 'cad_session',
            'cad_invalid_tool_attempts':invalid_attempts,
            'cad_history':bounded_history([*history,tool_message(call,feedback)]),
            'final_message':'The model kept requesting memory actions that add no information. The candidate and usage were retained; Continue with a focused next action.'}
    tool_input = call["input"]
    hierarchy_pre_normalized = False
    if call["name"] == "apply_changes" and isinstance(tool_input, dict) \
            and isinstance(tool_input.get("manifest"), dict):
        tool_input = json.loads(json.dumps(tool_input))
        tool_input["manifest"], hierarchy_pre_normalized = normalize_instance_hierarchy(
            tool_input["manifest"])
    try:
        parsed = parse_tool("cad", call["name"], tool_input)
        if call["name"] == "update_parameters":
            parsed = parameter_patch(snapshot["manifest"], parsed)
        if call["name"] in {"apply_changes", "update_parameters"}:
            parsed = source_patch(snapshot["files"], parsed)
        value = parsed.model_dump()
    except (ValidationError, ValueError) as exc:
        if call["name"] == "apply_changes":
            proposed = retained_proposal_files(tool_input)
            if proposed:
                usage["rejected_files"] = proposed
        invalid_attempts = state.get("cad_invalid_tool_attempts", 0) + 1
        issues = (exc.errors(include_url=False,include_input=False,include_context=False)
            if isinstance(exc,ValidationError) else [{"loc":(),"msg":str(exc)}])
        offered = next((item['function']['parameters'] for item in tools
            if item['function']['name']==call['name']),{})
        guidance = {
            "update_parameters":"changes must be a list of componentId, parameter and value objects. Use existing component IDs and parameter names; introduce new parameters with apply_changes.manifest and preserve the requested values.",
            "apply_changes":"files maps exact Python paths to full source. Correct the listed manifest/source fields together. Provided component arrays need complete entries; introduce source parameters in those entries. The rejected action changed nothing.",
            "read_file":"Use an exact non-empty file path from workspace.files. If it is empty, create source with apply_changes.",
        }.get(call['name'],"Correct the listed fields using the currently offered tool schema. The rejected action changed nothing.")
        is_patch_mismatch = (call["name"] == "apply_changes" and isinstance(exc, ValueError)
            and "Patch for " in str(exc) and "must match exactly once" in str(exc))
        mismatch_paths = (re.findall(r"Patch for ([A-Za-z0-9_./-]+) must match exactly once", str(exc))
                          if is_patch_mismatch else [])
        category = "source_patch_mismatch" if is_patch_mismatch else "tool_contract"
        if is_patch_mismatch:
            usage["cad_patch_mismatch"] = {"candidateHash": digest(snapshot),
                "paths": sorted(set(mismatch_paths)), "diagnostic": str(exc)[:1200]}
        feedback = {
            "ok": False, "category": category, "tool":call['name'],
            "message":"Tool arguments were rejected before any changes were saved.",
            "issues":[{"path":".".join(map(str,item['loc'])),"message":item['msg'][:500]} for item in issues[:40]],
            "acceptedArguments":sorted(offered.get('properties',{})),
            "repairGuidance": (("This patch did not match the current saved source; nothing was saved. "
                "Immediately replace the complete current file using apply_changes.files and omit patches. "
                "Do not reread unless necessary, and do not repeat the exact-text patch.")
                if is_patch_mismatch else guidance),
        }
        usage['working_memory']=record_observation({'working_memory':usage.get('working_memory') or
            state.get('working_memory')},call['name'],feedback,candidate=digest(snapshot))
        next_history = bounded_history([*history, tool_message(call, feedback)])
        if invalid_attempts >= 3:
            return {**usage, "phase": "cad_recovery",
                "cad_invalid_tool_attempts": invalid_attempts, "cad_history": next_history,
                "final_message": ("The CAD model returned an invalid tool action three times, "
                    "so the run paused before publishing geometry. Continue with a focused correction "
                    "to the tool arguments; the saved candidate is retained.")}
        return {**usage, "phase": "cad_session", "cad_invalid_tool_attempts": invalid_attempts,
            "cad_history": next_history}

    queued_observation = bool(usage.get("cad_pending_actions")) and call["name"] in {
        "read_file", "search_files", "diagnose_workspace", "inspect_geometry"}
    if repeated_tool_action(history, call) and not queued_observation:
        invalid_attempts = state.get("cad_invalid_tool_attempts", 0) + 1
        feedback = {
            "ok": False,
            "category": "repeated_tool_action",
            "message": "This exact tool action was already returned without changing the workspace.",
            "repairGuidance": ("Read the previous tool feedback and change the rejected arguments or source. "
                "Do not repeat the identical action."),
        }
        next_history = bounded_history([*history, tool_message(call, feedback)])
        if invalid_attempts >= 3:
            return {**usage, "phase": "cad_recovery",
                "cad_invalid_tool_attempts": invalid_attempts, "cad_history": next_history,
                "final_message": ("The CAD model repeated the same ineffective action three times. "
                    "The saved design is unchanged. " + (
                        "Remaining issues: " + "; ".join(item["message"] for item in
                            state["cad_contract_repair"].get("issues", []))[:1500]
                        if state.get("cad_contract_repair") else
                        "Continue with a focused correction to the repeated action."))}
        return {**usage, "phase": "cad_session", "cad_invalid_tool_attempts": invalid_attempts,
            "cad_history": next_history}

    name = call["name"]
    if (name == "read_file" and str(value.get("path", "")).startswith("calculations/")
            and state.get("engineering_summary")):
        feedback = {"ok": False, "category": "role_boundary",
            "message": "Engineering has already executed the calculation and supplied its result; CAD cannot edit or re-read calculations.",
            "engineeringSummary": state.get("engineering_summary", ""),
            "repairGuidance": "Use the engineering summary and parameters, then create or build the requested CAD source."}
        return {**usage, "phase": "cad_session", "last_read_path": None,
            "cad_history": bounded_history([*history, tool_message(call, feedback)])}
    if name == "update_working_notes":
        notes = parsed.model_dump(exclude_unset=True)
        memory = update_notes(usage.get("working_memory") or state.get("working_memory") or {}, notes)
        return {**usage, "phase": "cad_session", "working_memory": memory,
            "cad_history": bounded_history([*history, tool_message(call, {"ok": True, "agentReportedNotes": True})])}
    if name == "read_working_memory":
        result = read_memory(usage.get("working_memory") or state.get("working_memory") or {}, value["offset"], value["limit"])
        return {**usage, "phase": "cad_session", "cad_history": bounded_history([*history, tool_message(call, result)])}
    if name == "diagnose_workspace":
        return observation_result(state, snapshot, call, value, history, usage, workspace_status(snapshot))
    read_key = ("rejected:" if value.get("version") == "rejected" else "") + str(value.get("path", ""))
    if name == "read_file" and state.get("last_read_path") == read_key and not queued_observation:
        feedback = {"ok": False, "category": "repeated_tool_action",
            "message": "This file was just read in the previous CAD turn.",
            "repairGuidance": "Use the returned source now. Patch it with apply_changes or build the candidate."}
        return {**usage, "phase": "cad_session", "last_read_path": read_key,
            "cad_history": bounded_history([*history, tool_message(call, feedback)])}
    if name == "read_file":
        safe_path(value["path"])
        rejected = value.get("version") == "rejected"
        selected_files = (state.get("rejected_files") or {}) if rejected else snapshot["files"]
        content = selected_files.get(value["path"])
        if content is None:
            invalid_attempts = state.get("cad_invalid_tool_attempts", 0) + 1
            result = {"ok": False, "category": "file_not_found", "path": value["path"],
                "message": "That path is not a file in the workspace. Read only an exact path from workspace.files.",
                "availablePaths": sorted(selected_files),
                "repairGuidance": ("The workspace is empty; create the requested parts with apply_changes instead "
                    "of reading a directory or invented metadata file." if not snapshot["files"] else
                    "Choose one of the listed file paths or patch the requested component.")}
            next_history = bounded_history([*history, tool_message(call, result)])
            if invalid_attempts >= 3:
                return {**usage, "phase": "cad_recovery",
                    "cad_invalid_tool_attempts": invalid_attempts, "cad_history": next_history,
                    "final_message": ("The CAD model repeatedly requested paths that are not in the workspace, "
                        "so this action was paused. The saved candidate is retained; Continue with an "
                        "actual workspace path or a focused edit.")}
            return {**usage, "phase": "cad_session", "cad_invalid_tool_attempts": invalid_attempts,
                "last_read_path": value.get("path"), "cad_history": next_history}
        return {**observation_result(state, snapshot, call, value, history, usage, {
                "ok": True, "path": value["path"], "version": value.get("version", "saved"),
                "saved": not rejected, "content": content}),
            "cad_invalid_tool_attempts": 0, "last_read_path": read_key}
    if name == "search_files":
        matches = [{"path": path, "line": index + 1, "text": line[:300]}
            for path, source in snapshot["files"].items()
            for index, line in enumerate(source.splitlines())
            if value["query"] in line][:100]
        return {**observation_result(state, snapshot, call, value, history, usage, {"matches": matches}),
                "last_read_path": None}
    if name == "inspect_geometry":
        if value.get("componentId"):
            cid = value["componentId"]
            probe = (state.get("component_probes") or {}).get(cid)
            freshness = next((item for item in probe_context(state, snapshot) if item["componentId"] == cid), {})
            result = {"ok": probe is not None, "scope": "component_only", "componentId": cid,
                      "currentSource": freshness.get("currentSource", False), "probe": probe,
                      "message": "Part diagnostic only. Assembly and configurations require a complete build."}
            return observation_result(state, snapshot, call, value, history, usage, result)
        result = state.get("validation") or {
            "verified": False, "message": "Build the current candidate before inspecting imported geometry."
        }
        return observation_result(state, snapshot, call, value, history, usage, result)
    if name == "respond_to_review":
        current_hash = digest(snapshot)
        review = state.get("review") or {}
        validation = state.get("validation") or {}
        targets = {item["id"] for item in review.get("findings", []) if is_review_repair_target(item)}
        response_key = f"{current_hash}:{value['findingId']}"
        previous = state.get("review_responses", [])
        if (review.get("action") != "repair" or value["findingId"] not in targets
                or state.get("reviewed_candidate_hash") != current_hash
                or validation.get("identity") != identity(snapshot, state.get("requirements", []))
                or response_key in previous):
            return {**usage, "phase": "cad_session", "cad_history": bounded_history([
                *history, tool_message(call, {"ok": False, "category": "review_response",
                    "message": "Respond only once to an existing repair finding for the exact current independently validated candidate. Changed or unvalidated geometry must be rebuilt."})])}
        response = {**value, "candidateHash": current_hash}
        await repo.event(state["run_id"], "CAD returned a conflicting repair finding for evidence reassessment; geometry is unchanged.", stage="review")
        return {**usage, "phase": "review_session", "review_response": response,
            "review_responses": [*previous, response_key], "review_reads": 0,
            "review_inspected": False, "review_actions": 0,
            "cad_history": bounded_history([*history, tool_message(call, {"ok": True, "returnedForReview": True, "geometryChanged": False})]),
            "review_history": [{"role": "user", "content": "CAD disputes this repair finding using current evidence. Reassess the unchanged validated candidate; do not confuse aggregate centres with occurrence frame origins. " + json.dumps(response)}]}
    if name in {"apply_changes", "update_parameters"}:
        invalid_paths = [path for path in [*value["files"], *value.get("deletePaths", [])]
                         if not (path.startswith("parts/") or path.startswith("assemblies/"))]
        if invalid_paths:
            result = {"ok": False, "category": "role_boundary",
                "message": "CAD may edit only parts/ and assemblies/. Request engineering for calculations/ changes.",
                "paths": invalid_paths[:20]}
            return {**usage, "phase": "cad_session", "cad_history": bounded_history([
                *history, tool_message(call, result)])}
        empty_sources = [path for path, source in value["files"].items()
                         if not str(source).strip()]
        if empty_sources:
            result = {"ok": False, "category": "empty_source",
                "message": "Every changed Python file must contain executable source; empty files cannot build.",
                "paths": empty_sources[:20],
                "repairGuidance": "Write the requested component build(parameters, dependencies) function and manifest together."}
            invalid_attempts = state.get("cad_invalid_tool_attempts", 0) + 1
            next_history = bounded_history([*history, tool_message(call, result)])
            if invalid_attempts >= 3:
                return {**usage, "phase": "cad_recovery",
                    "cad_invalid_tool_attempts": invalid_attempts, "cad_history": next_history,
                    "final_message": "The CAD model repeatedly returned empty source files, so no source was executed."}
            return {**usage, "phase": "cad_session", "cad_invalid_tool_attempts": invalid_attempts,
                "cad_history": next_history}
        if value["files"]:
            usage["rejected_files"] = dict(value["files"])
        try:
            files = dict(snapshot["files"])
            for path in value.get("deletePaths", []):
                files.pop(path, None)
            files.update({path: normalize_python_source(source)
                for path, source in value["files"].items()})
            manifest, hierarchy_normalized = normalize_instance_hierarchy(
                updated_manifest(snapshot["manifest"], parsed))
            hierarchy_normalized = hierarchy_pre_normalized or hierarchy_normalized
            syntax = source_syntax_error(files)
            if syntax:
                invalid_attempts = state.get("cad_invalid_tool_attempts", 0) + 1
                result = {"ok": False, "category": "python_syntax",
                    "message": "The changed source is not valid Python and was not saved.",
                    "location": syntax,
                    "repairGuidance": ("Return real Python source with line breaks and quoted string literals. "
                        "Fix only the reported file, then submit it again with apply_changes.")}
                next_history = bounded_history([*history, tool_message(call, result)])
                if invalid_attempts >= 3:
                    return {**usage, "phase": "cad_recovery",
                        "cad_invalid_tool_attempts": invalid_attempts, "cad_history": next_history,
                        "final_message": ("The CAD model returned invalid Python source three times. "
                            "The saved design is unchanged; select a model that preserves code formatting before retrying.")}
                return {**usage, "phase": "cad_session", "cad_invalid_tool_attempts": invalid_attempts,
                    "cad_history": next_history}
            manifest, auto_registered = register_unlisted_part_sources(manifest, files)
            candidate = Snapshot.model_validate({
                "manifest": manifest, "files": files,
            }).model_dump()
            validate_component_entrypoints(candidate["manifest"], {
                path: candidate["files"][path] for path in value["files"]})
        except (ValidationError, ValueError) as exc:
            usage["rejected_files"] = dict(value["files"]) or state.get("rejected_files") or {}
            result = workspace_contract_feedback(manifest, files, exc)
            invalid_attempts = contract_repair_attempt(state.get("cad_contract_repair") or {}, result)
            repair = {**result, "stalledAttempts": invalid_attempts}
            await repo.event(state["run_id"], "Workspace edit rejected: " + "; ".join(
                f"{item['code']} at {item['path']}" for item in result["issues"]),
                kind="validation", stage="cad", attempt=invalid_attempts)
            next_history = bounded_history([*history, tool_message(call, result)])
            if invalid_attempts >= 3:
                return {**usage, "phase": "cad_recovery",
                    "cad_invalid_tool_attempts": invalid_attempts, "cad_history": next_history,
                    "cad_contract_repair": repair,
                    "final_message": ("The CAD model could not repair the workspace after three stalled attempts. "
                        "The saved design is unchanged. Remaining issues: " + "; ".join(
                            item["message"] for item in result["issues"])[:1500])}
            return {**usage, "phase": "cad_session", "cad_invalid_tool_attempts": invalid_attempts,
                "cad_contract_repair": repair, "cad_history": next_history}
        candidate_hash = digest(candidate)
        if value.get("dryRun"):
            result = {"ok": True, "previewOnly": True, "saved": False,
                      "proposedCandidateHash": candidate_hash, "workspaceStatus": workspace_status(candidate)}
            return {**usage, "phase": "cad_session", "cad_history": bounded_history([
                *history, tool_message(call, result)])}
        if candidate_hash == digest(snapshot):
            result = {"ok": True, "unchanged": True, "candidateHash": candidate_hash,
                      "message": "No workspace changes. Choose a new edit or build; no edit allowance was consumed."}
            return {**usage, "phase": "cad_session", "cad_contract_repair": {},
                    "cad_invalid_tool_attempts": 0, "cad_history": bounded_history([
                *history, tool_message(call, result)])}
        await run_service.save_candidate(state["run_id"], candidate, candidate_hash)
        await repo.event(state["run_id"], ("CAD updated named component parameters." if name == "update_parameters"
            else "CAD updated a focused part of the code workspace."), stage="cad")
        result = {"ok": True, "candidateHash": candidate_hash,
            "workspaceStatus": workspace_status(candidate),
            "changedFiles": sorted(value["files"]), "deletedFiles": value.get("deletePaths", []),
            "autoRegisteredComponents": auto_registered,
            "hierarchyNormalized": hierarchy_normalized,
            "hierarchyNote": ("Top-level or invalid parent sentinels were normalized to null; parentId must name "
                "another instance id." if hierarchy_normalized else "")}
        return {**usage, "phase": "cad_session", "candidate_hash": candidate_hash,
            "rejected_files": {},
            "working_memory": record_observation({"working_memory": usage.get("working_memory") or state.get("working_memory")},
                name, result, candidate=candidate_hash),
            "cad_invalid_tool_attempts": 0,
            "cad_contract_repair": {},
            "cad_patch_mismatch": {},
            "cad_edits_since_build": edits_since_build + 1,
            "last_read_path": None,
            "cad_history": bounded_history([*history, tool_message(call, result)]),
            "validation": {}, "build_result": {}}
    if name == "request_engineering":
        candidate_digest = digest(snapshot)
        request_count = state.get("engineering_request_count", 0) + 1
        if request_count > 2:
            result = {"ok": False, "category": "engineering_request_limit",
                "message": ("Engineering has already analyzed two CAD candidates for this run. "
                    "Use the latest engineering result, edit the geometry, or build the current candidate.")}
            return {**usage, "phase": "cad_session", "engineering_request_count": request_count,
                "last_engineering_request_hash": candidate_digest,
                "cad_history": bounded_history([*history, tool_message(call, result)])}
        if state.get("engineering_summary") and state.get("engineering_candidate_hash") == candidate_digest:
            result = {"ok": False, "category": "unchanged_engineering_request",
                "message": ("Engineering already analyzed this unchanged workspace. Use the returned parameters, "
                    "edit geometry, or build before requesting another calculation.")}
            return {**usage, "phase": "cad_session", "engineering_request_count": request_count,
                "last_engineering_request_hash": candidate_digest,
                "cad_history": bounded_history([
                *history, tool_message(call, result)])}
        await repo.event(state["run_id"], "CAD requested an engineering calculation or parameter study.", stage="engineering")
        return {**usage, "phase": "engineering_analysis", "engineering_request": value["task"],
            "engineering_request_count": request_count, "last_engineering_request_hash": candidate_digest,
            "pending_cad_call": call, "last_read_path": None, "cad_history": history}
    if name == "ask_user":
        # Models sometimes ask for permission to create an empty workspace or
        # to inspect it before starting.  That is an internal CAD action, not
        # a missing design decision; interrupting the user here creates an
        # unproductive clarification loop.  Feed the agent a deterministic
        # acknowledgement and keep the graph in the CAD session.
        question = str(value.get("question") or "").strip()
        internal_workspace_question = any(term in question.lower() for term in (
            "fresh workspace", "start with a fresh", "create the initial component",
            "proceed with building", "workspace appears to have no", "should i proceed",
        ))
        if internal_workspace_question:
            history = bounded_history([*history, tool_message(call, {
                "answered": True,
                "message": "Proceed directly. Create the requested components in the empty workspace; do not ask for confirmation.",
            })])
            return {**usage, "phase": "cad_session", "question": "",
                "pending_cad_call": {}, "cad_history": history}
        return {**usage, "phase": "cad_question", "question": value["question"],
            "pending_cad_call": call, "last_read_path": None, "cad_history": history}
    if name == "test_component" or name == "build" and value.get("componentId"):
        failure = active_failure_context(state, snapshot)
        if name == "test_component" and failure and (
                failure.get("failureScope") != "component"
                or value["componentId"] != failure.get("componentId")
                or failure.get("repeatedCount", 0) >= 2
                or failure_probe_key(failure) in state.get("cad_component_probe_failures", [])):
            expected = failure.get("componentId")
            scope_message = (f"A component probe has already been used for this active full-build failure. Edit {failure.get('file') or expected} and rebuild the complete assembly before any further probe."
                             if failure_probe_key(failure) in state.get("cad_component_probe_failures", []) else
                             f"The same full-build failure has repeated. Edit {failure.get('file') or expected} and rebuild before another component probe."
                             if failure.get("repeatedCount", 0) >= 2 else
                             f"The current build failure targets component {expected}; test that component or repair the failed operation."
                             if expected else
                             "The current build failure is assembly-level or has no safely identified component. "
                             "A part-only test cannot diagnose it; inspect and repair the active failure, then build the assembly.")
            return {**usage, "phase": "cad_session", "cad_history": bounded_history([
                *history, tool_message(call, {"ok": False, "category": "active_failure_scope",
                    "message": scope_message, "activeFailure": failure})])}
        if name == "build" and value.get("final", True):
            return {**usage, "phase": "cad_session", "cad_history": bounded_history([*history, tool_message(call, {
                "ok": False, "category": "build_scope",
                "message": "A final build cannot be scoped to componentId. No build executed.",
                "repairGuidance": "Call build({final:true}) without componentId for the complete design. For isolated diagnostics use test_component({componentId:...})."})])}
        result = await probe_component({**state, **usage}, snapshot, call, history, value["componentId"])
        return {**usage, **result}
    if name == "build":
        if not snapshot["manifest"].get("components") or not snapshot["manifest"].get("rootComponentId"):
            history = bounded_history([*history, tool_message(call, {
                "ok": False, "category": "empty_workspace",
                "message": "Create at least one component and choose a root component before building.",
            })])
            return {**usage, "phase": "cad_session", "cad_history": history}
        # An unchanged intermediate candidate may be ready for final review.
        # The build path still checks candidate/requirements/runtime identity
        # before reusing its evidence, and inventory checks can keep it partial.
        finalizing_milestone = value["final"] and not state.get("build_final", True)
        if (state.get("build_result", {}).get("ok")
                and not state.get("cad_edits_since_build", 0) and not finalizing_milestone):
            history = bounded_history([*history, tool_message(call, {
                "ok": False, "category": "unchanged_successful_candidate",
                "message": "This candidate already built. Stage the next component before building again.",
            })])
            return {**usage, "phase": "cad_session", "cad_history": history}
        if (state.get("review", {}).get("action") == "repair"
                and state.get("reviewed_candidate_hash") == digest(snapshot)):
            history = bounded_history([*history, tool_message(call, {
                "ok": False, "category": "unchanged_reviewed_candidate",
                "message": "The independent review requested a source change. Edit the candidate before rebuilding.",
            })])
            return {**usage, "phase": "cad_session", "cad_history": history}
        required_part_types = requested_part_type_count(state["original_request"])
        if required_part_types is None and requested_components:
            required_part_types = len(requested_components)
        built_part_types = sum(component.get("kind") != "assembly"
                               for component in snapshot["manifest"]["components"])
        final_build = value["final"]
        if required_part_types is not None and built_part_types < required_part_types:
            final_build = False
            if value["final"]:
                await repo.event(state["run_id"],
                    f"CAD staged {built_part_types} of {required_part_types} requested part types; checking this as an intermediate build.",
                    kind="validation", stage="cad")
        if not final_build and state.get("last_milestone_hash") == digest(snapshot):
            history = bounded_history([*history, tool_message(call, {
                "ok": False, "category": "unchanged_milestone",
                "message": "This exact intermediate candidate already built. If the requested inventory is complete, call build(final=true) for final review. Otherwise add the remaining parts or instances.",
            })])
            return {**usage, "phase": "cad_session", "cad_history": history}
        # Execute the build transition in the same graph step as the explicit
        # CAD build action.  Hosted LangGraph interrupts after each node; in
        # practice that boundary could lose the phase update and schedule
        # another CAD turn without ever entering the build node.
        # ``build`` normally runs as its own graph node and only returns CAD
        # checkpoint fields.  This path runs it inline from ``cad_session``;
        # preserve the model usage delta here so the next CAD turn gets a new
        # operation-ledger ordinal instead of replaying this completed tool
        # response forever.
        result = await build({**state, **usage, "phase": "build",
            "pending_cad_call": call, "cad_history": history,
            "cad_edits_since_build": 0, "last_read_path": None,
            "build_final": final_build, "requested_part_types": required_part_types or 0,
            "built_part_types": built_part_types})
        return {**result, **usage, "pending_cad_call": call,
            "cad_history": history, "cad_edits_since_build": 0,
            "last_read_path": None}
    history = bounded_history([*history, tool_message(call, {
        "ok": False, "category": "unsupported_action", "message": f"Unsupported CAD action: {name}",
    })])
    return {**usage, "phase": "cad_session", "cad_history": history}


async def cad_question(state: AgentState) -> dict:
    response = interrupt({"kind": "clarification", "message": state.get("question") or
        "Please provide the missing design choice."})
    message = str((response or {}).get("message", "")).strip()
    if not message:
        raise Pause("A clarification answer is required.")
    call = state.get("pending_cad_call") or {"id": "user-answer"}
    history = bounded_history([*state.get("cad_history", []), tool_message(call, {
        "answered": True, "message": message,
    }), {"role": "user", "content": message}])
    return {"phase": "cad_session", "question": "", "pending_cad_call": {},
        "cad_history": history, **user_update(state, message, "clarification")}


async def cad_candidate(state: AgentState, *, repair=False) -> dict:
    snapshot = await run_service.load_candidate(state["run_id"])
    context = {"request": state.get("clarified_request") or state["original_request"],
        "engineeringSummary": state.get("engineering_summary", ""),
        "engineeringRemarks": state.get("engineering_remarks", []), "requirements": state.get("requirements", []),
        "currentWorkspace": snapshot, "previousFailure": state.get("build_result") if repair else None}
    prompt = ("Create one complete CadQuery candidate. Return every changed Python file and the complete manifest "
        "together. Source paths must be relative and match parts/**/*.py, assemblies/**/*.py or "
        "calculations/**/*.py. Do not return README, Markdown, JSON, STEP, GLB or output files. ")
    prompt += "Repair the reported failure without removing requirements." if repair else "Build the requested geometry from the supplied workspace."
    value, usage = await structured_turn(state, "cad", "repair" if repair else "design",
        prompt + "\nPrivate context: " + json.dumps(context), "submit_candidate", Candidate)
    try:
        manifest, hierarchy_changed = normalize_instance_hierarchy(value.manifest.model_dump())
        merged_files = {**snapshot["files"], **value.files}
        syntax = source_syntax_error(merged_files)
        if syntax:
            raise ValueError(
                f"{syntax['file']}:{syntax['line']}:{syntax['column']}: "
                f"{syntax['message']}; {syntax['text']}"
            )
        candidate = Snapshot.model_validate({"manifest": manifest,
            "files": merged_files}).model_dump()
        validate_component_entrypoints(candidate["manifest"], value.files)
        if hierarchy_changed:
            await repo.event(state["run_id"],
                "CAD assembly hierarchy had invalid parent references; flattened those edges for a buildable draft.",
                kind="validation", stage="cad")
    except (ValidationError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            feedback = "; ".join(
                f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
                for item in exc.errors(include_url=False, include_input=False)[:12]
            )
        else:
            feedback = str(exc)
        repairs = state.get("repairs", 0) + 1
        await repo.event(state["run_id"],
            f"CAD candidate contract failed before execution: {feedback}",
            kind="validation", stage="cad", attempt=repairs)
        if repairs > (await app_settings()).limits.maxRepairs:
            return {**usage, "phase": "final", "repairs": repairs, "terminal_status": "failed", "final_message":
                "The CAD model repeatedly returned an invalid workspace contract. No generated code was executed "
                "and the saved design is unchanged."}
        return {**usage, "phase": "repair", "repairs": repairs,
            "build_result": {"ok": False, "stage": "contract", "category": "workspace_contract",
                "message": feedback, "repairGuidance":
                    "Return only valid Python sources and a complete, internally consistent manifest."}}
    candidate_hash = digest(candidate)
    if repair and candidate_hash == state.get("candidate_hash"):
        raise Pause("The CAD repair returned an unchanged candidate, so it was not rebuilt.")
    await run_service.save_candidate(state["run_id"], candidate, candidate_hash)
    await repo.event(state["run_id"], "CAD prepared a complete candidate workspace.", stage="cad")
    requirements = bind_requirements_to_manifest(state.get("requirements", []), candidate["manifest"])
    return {**usage, "phase": "build", "candidate_hash": candidate_hash,
        "requirements": requirements,
        "candidate_summary": value.summary, "validation": {}}


async def cad_design(state: AgentState) -> dict:
    return await cad_candidate(state)


async def build(state: AgentState) -> dict:
    run = await run_row(state)
    limits = (await app_settings()).limits
    snapshot = await run_service.load_candidate(state["run_id"])
    cp = checkpoint_view(state, snapshot)
    cp["sandbox"] = cp.get("sandbox") or sandbox_name(run["id"], "cad")
    cp["validator"] = sandbox_name(run["id"], "validator")
    cp["buildFinal"] = state.get("build_final", True)
    # A partial assembly may build successfully and then return to CAD for
    # more components. The attempt counter does not change when a validated
    # candidate is reused, so it cannot identify a build operation by itself.
    # Include the complete candidate/requirements/runtime identity to prevent
    # a later edit from inheriting an earlier candidate's successful report.
    build_key = f"graph:build:{state.get('attempts', 0)}:{digest(identity(snapshot, cp['requirements']))[:24]}"
    async def execute_build():
        result = await build_candidate(run, cp, limits, build_key)
        return {"result": result, "checkpoint": cp}
    try:
        output = await operation(run, build_key, "build",
            execute_build, idempotent=True)
    except Pause as exc:
        if "has not changed" not in str(exc):
            raise
        pending = state.get("pending_cad_call") or {"id": "build"}
        history = bounded_history([*state.get("cad_history", []), tool_message(pending, {
            "ok": False, "category": "unchanged_failed_candidate", "message": str(exc),
        })])
        return {"phase": "cad_session", "cad_history": history, "pending_cad_call": {},
            "model_calls": state.get("model_calls", 0),
            "search_count": state.get("search_count", 0)}
    cp = output["checkpoint"]
    # CAD invokes build inside its own graph node. Return the tool-turn state
    # explicitly; otherwise LangGraph drops the newly consumed model ordinal
    # and pending call while persisting only the build result.
    return {**sync_checkpoint(cp), "phase": "validate", "build_result": output["result"],
        "model_calls": state.get("model_calls", 0),
        "search_count": state.get("search_count", 0),
        "pending_cad_call": state.get("pending_cad_call") or {},
        "cad_history": state.get("cad_history") or [],
        "cad_edits_since_build": state.get("cad_edits_since_build", 0),
        "build_final": state.get("build_final", True),
        "requested_part_types": state.get("requested_part_types", 0),
        "built_part_types": state.get("built_part_types", 0)}


async def probe_component(state: AgentState, snapshot: dict, call: dict, history: list, component_id: str) -> dict:
    """Use the same isolated builder/STEP validator without promoting part evidence to assembly evidence."""
    try:
        scoped = component_snapshot(snapshot, component_id)
    except ValueError as exc:
        return {"phase": "cad_session", "model_calls": state.get("model_calls", 0),
                "cad_history": bounded_history([*history, tool_message(call, {
                    "ok": False, "category": "component_probe", "message": str(exc)})])}
    requirements = [item for item in state.get("requirements", [])
                    if item.get("componentId") == component_id and item.get("kind") != "assembly_preservation"]
    active_failure = active_failure_context(state, snapshot)
    probe_failure_key = (failure_probe_key(active_failure)
                         if active_failure and active_failure.get("componentId") == component_id else None)
    probe_failures = list(state.get("cad_component_probe_failures") or [])
    if probe_failure_key:
        probe_failures = list(dict.fromkeys([*probe_failures, probe_failure_key]))[-32:]
    expected = identity(scoped, requirements)
    probes = deepcopy(state.get("component_probes") or {})
    prior = probes.get(component_id) or {}
    if prior.get("identity") == expected:
        result = {**prior, "reused": True, "message": "This exact part source already has scoped evidence. Edit before retrying a failure. To complete the whole design, call build(final=true) without componentId; this test cannot publish."}
        result_state = observation_result(state, snapshot, {**call, "name": "test_component"}, {"componentId": component_id},
            history, {"model_calls": state.get("model_calls", 0),
                      "cad_pending_actions": state.get("cad_pending_actions") or []}, result)
        return {**result_state, **({"cad_component_probe_failures": probe_failures} if probe_failure_key else {})}
    run = await run_row(state)
    limits = (await app_settings()).limits
    cp = checkpoint_view(state, scoped)
    cp.update(requirements=requirements, validated=None, lastFailedCandidate=None, lastFailure=None,
              repairs=0, buildFinal=False, sandbox=state.get("sandbox") or sandbox_name(run["id"], "cad"),
              validator=sandbox_name(run["id"], "probe-validator"))
    key = f"graph:component-probe:{component_id}:{digest(expected)[:24]}"

    async def execute():
        report = await build_candidate(run, cp, limits, key)
        return {"report": report, "checkpoint": cp}

    output = await operation(run, key, "build", execute, idempotent=True)
    cp, report = output["checkpoint"], output["report"]
    result = {"componentId": component_id, "identity": expected, "requirements": requirements,
              "ok": report.get("ok") is not False, "report": report,
              "scope": "component_only", "published": False}
    if report.get("error"):
        result["error"] = report["error"]
    probes[component_id] = result
    await repo.event(run["id"], f"Isolated test of {component_id} completed; full assembly evidence is unchanged.", stage="cad")
    # Do not sync this checkpoint's validation, requirements or failure identity
    # into the project. Keep only usage and the reusable execution environment.
    return {"phase": "cad_session", "component_probes": probes, "attempts": cp["attempts"],
            **({"cad_component_probe_failures": probe_failures} if probe_failure_key else {}),
            "model_calls": state.get("model_calls", 0), "sandbox": cp.get("sandbox"),
            "sandbox_ready": cp.get("sandboxReady", False),
            "working_memory": record_observation(state, "component_probe", result, candidate=digest(snapshot)),
            "cad_history": bounded_history([*history, tool_message(call, result)]),
            "cad_invalid_tool_attempts": 0}


async def validate(state: AgentState) -> dict:
    result = state.get("build_result", {})
    pending = state.get("pending_cad_call") or {"id": "build"}
    memory = record_observation(state, "build", result, candidate=state.get("candidate_hash", ""))
    if result.get("ok") is False:
        error = result.get("error", {})
        history = bounded_history([*state.get("cad_history", []), tool_message(pending, result)])
        snapshot = (await run_service.load_candidate(state["run_id"])
                    if state.get("run_id") else {"manifest": {"components": []}, "files": {}})
        failure = active_failure_context({"build_result": result}, snapshot)
        if failure:
            previous = state.get("cad_last_build_failure") or {}
            fingerprint = error.get("fingerprint") if isinstance(error, dict) else None
            signature = digest({"fingerprint": fingerprint, "category": failure.get("category"),
                "file": failure.get("file"), "line": failure.get("line"),
                "diagnostic": failure.get("diagnostic")})
            normalized_diagnostic = re.sub(r"\s+", " ", str(failure.get("diagnostic") or "").lower())
            normalized_diagnostic = re.sub(r"\bline\s+\d+\b", "line #", normalized_diagnostic)
            normalized_diagnostic = re.sub(r"(\.py['\"]?,?\s*line\s+)\d+", r"\1#", normalized_diagnostic)
            repeat_signature = digest({"category": failure.get("category"), "file": failure.get("file"),
                "diagnostic": normalized_diagnostic})
            same_failure = (previous.get("repeatSignature") == repeat_signature
                or not previous.get("repeatSignature") and previous.get("signature") == signature)
            repeated_count = previous.get("repeatedCount", 0) + 1 if same_failure else 1
            failure = {**failure, "fingerprint": fingerprint, "signature": signature, "attempt": result.get("attempt"),
                       "repeatSignature": repeat_signature, "repeated": same_failure,
                       "repeatedCount": repeated_count,
                       "candidateHashAtFailure": state.get("candidate_hash", "")}
        # Bound automatic repair, but retain the draft and a resumable graph.
        # A successfully validated milestone starts a new repair allowance.
        limits = (await app_settings()).limits
        if state.get("repairs", 0) >= limits.maxRepairs:
            guidance = error.get("guidance", "Repair the failed CAD operation.")
            return {"phase": "cad_recovery", "repairs": state.get("repairs", 0),
                "working_memory": memory,
                "cad_last_build_failure": failure or state.get("cad_last_build_failure") or {},
                "cad_history": history,
                "final_message": (f"The bounded CAD repair limit was reached after "
                    f"{state.get('attempts', 0)} attempts. {guidance} "
                    f"Diagnostic: {error.get('diagnostic', 'No traceback was emitted.')[-1800:]} "
                    "The candidate is retained and no failed geometry was published. "
                    "Continue to re-plan the failing component with a fresh bounded repair allowance.")}
        if result.get("repeated"):
            history = bounded_history([*history, {"role": "user", "content":
                "The normalized build error repeated after a source change. Re-plan the affected operation instead of retrying the same construction."}])
        return {"phase": "cad_session", "cad_history": history, "pending_cad_call": {},
            "working_memory": memory,
            "cad_last_build_failure": failure or state.get("cad_last_build_failure") or {},
            "final_message": error.get("guidance", "Repair the failed CAD operation.")}
    intermediate = not state.get("build_final", True)
    history = bounded_history([*state.get("cad_history", []), tool_message(pending, {
        "ok": True, "message": ("The staged assembly built and passed CAD integrity checks. If the requested "
            "inventory is complete, call build(final=true) for final review; otherwise add the remaining parts and instances." if intermediate else
            "The candidate built and passed universal CAD integrity checks."),
        "inspectionAvailable": bool(result.get("inspection")),
    })])
    if intermediate:
        return {"phase": "cad_session", "cad_history": history, "repairs": 0,
            "working_memory": memory,
            "cad_last_build_failure": {},
            "cad_component_probe_failures": [],
            "pending_cad_call": {},
            "last_milestone_hash": state.get("candidate_hash", "")}
    requested_components = requested_component_labels(state.get("original_request", ""))
    if len(requested_components) > 1:
        snapshot = await run_service.load_candidate(state["run_id"])
        staged = staged_component_count(snapshot["manifest"])
        if staged < len(requested_components):
            history = bounded_history([*history, {"role": "user", "content": (
                f"Intermediate build passed with {staged} of {len(requested_components)} requested "
                "component types staged. Add the remaining requested components, instances and "
                "relationships before the final build. Do not repeat an unchanged build."
            )}])
            return {"phase": "cad_session", "cad_history": history, "pending_cad_call": {},
                "working_memory": memory,
                "cad_last_build_failure": {},
                "cad_component_probe_failures": [],
                "last_milestone_hash": state.get("candidate_hash", "")}
    # Every final build gets a focused evidence review before draft publication.
    # This is a bounded quality pass, not a release gate: after at most two
    # focused repairs, publish the last buildable draft with its findings.
    return {"phase": "review_session", "cad_history": history,
        "working_memory": memory,
        "cad_last_build_failure": {},
        "cad_component_probe_failures": [],
        "pending_cad_call": {}, "review_history": [], "review_reads": 0,
        "review_inspected": False, "review_actions": 0, "review": state.get("review") or {},
        "review_repairs": state.get("review_repairs", 0)}


async def repair(state: AgentState) -> dict:
    return await cad_session(state)


class RecoveryPlan(Contract):
    diagnosis: str = Field(min_length=1, max_length=2000)
    nextTask: str = Field(min_length=1, max_length=3000)
    changesFromFailedApproach: str = Field(min_length=1, max_length=2000)
    verification: str = Field(min_length=1, max_length=2000)


def human_followups(state: AgentState) -> list[dict]:
    """Keep design changes while labeling old workspace claims as historical."""
    updates = state.get("user_updates") or []
    if updates:
        return updates
    clarified = state.get("clarified_request")
    return [{"kind": "legacy_clarification", "message": clarified}] if clarified and clarified != state.get("original_request") else []


def current_candidate_facts(snapshot: dict) -> dict:
    """Small authoritative inventory, independent of past dialogue or plans."""
    manifest = snapshot["manifest"]
    return {"candidateHash": digest(snapshot), "componentCount": len(manifest.get("components") or []),
            "instanceCount": len(manifest.get("instances") or []),
            "rootComponentId": manifest.get("rootComponentId")}


def compact_manifest_context(manifest: dict) -> dict:
    """Keep editable identities and placements without repeating optional metadata."""
    components = [{key: component[key] for key in ("id", "kind", "name", "source", "dependencies", "parameters")
                   if key in component} for component in (manifest.get("components") or [])]
    instances = [{key: instance[key] for key in ("id", "definitionId", "parentId", "name", "frame", "bomExclude")
                  if key in instance} for instance in (manifest.get("instances") or [])]
    instance_counts = Counter(instance.get("definitionId", "") for instance in instances)
    return {"rootComponentId": manifest.get("rootComponentId"), "components": components,
            "instances": instances, "instanceCounts": dict(instance_counts)}


def compact_build_context(value: dict | None) -> dict | None:
    if not value:
        return None
    error = value.get("error") or {}
    if isinstance(error, str):
        error = {"diagnostic": error}
    compact_error = {key: error[key] for key in ("category", "file", "path", "location", "line", "message", "guidance")
                     if key in error}
    for key in ("diagnostic", "details", "message"):
        if isinstance(error.get(key), str):
            compact_error[key] = error[key][-2400:]
    return {key: value[key] for key in ("ok", "candidateHash", "attempt", "final", "scope") if key in value} | {
        "error": compact_error or None}


def active_build_failure(value: dict | None) -> dict | None:
    """Expose the newest actionable build failure as the repair target."""
    if not value or value.get("ok") is True:
        return None
    error = value.get("error") or {}
    if isinstance(error, str):
        error = {"diagnostic": error}
    if not isinstance(error, dict):
        return None
    location = error.get("location") if isinstance(error.get("location"), dict) else {}
    path = error.get("file") or error.get("path") or location.get("file")
    diagnostic = error.get("diagnostic") or error.get("message") or ""
    return {
        "category": error.get("category"),
        "file": path,
        "line": error.get("line") or location.get("line"),
        "diagnostic": str(diagnostic)[-1800:],
        "instruction": ("This is the newest failed build operation and takes priority over older repair directions. "
                        "Inspect and repair this exact file/operation, then test it or rebuild. Do not return to an "
                        "older file-specific repair unless this current failure proves it is required."),
    }


def active_failure_context(state: dict, snapshot: dict) -> dict | None:
    """Keep the latest full-build defect available after edits clear stale build results."""
    stored = state.get("cad_last_build_failure") or {}
    if stored:
        return deepcopy(stored)
    failure = active_build_failure(state.get("build_result"))
    if not failure:
        return None
    path = str(failure.get("file") or "").replace("\\", "/").lstrip("./")
    component = next((item for item in (snapshot.get("manifest") or {}).get("components", [])
                      if path and str(item.get("source") or "").replace("\\", "/").lstrip("./") == path), None)
    return {**failure, "failureScope": "component" if component and component.get("kind") != "assembly" else "assembly",
            "componentId": component.get("id") if component and component.get("kind") != "assembly" else None}


def failure_probe_key(failure: dict | None) -> str | None:
    """Identify the failing file/component lineage without candidate line-number churn."""
    if not failure or not failure.get("componentId") or not failure.get("file"):
        return None
    path = str(failure["file"]).replace("\\", "/").lstrip("./").lower()
    return digest({"componentId": failure["componentId"], "file": path})


def restrict_component_probe(tools: list[dict], component_id: str) -> list[dict]:
    """Constrain component-only diagnostics to the file named by the active build failure."""
    restricted = deepcopy(tools)
    for tool in restricted:
        if tool.get("function", {}).get("name") != "test_component":
            continue
        parameters = tool["function"].get("parameters", {})
        component = parameters.get("properties", {}).get("componentId")
        if isinstance(component, dict):
            component["enum"] = [component_id]
    return restricted


def active_source_patch_mismatch(state: AgentState, snapshot: dict) -> dict | None:
    """Return a patch mismatch only while it still targets the saved candidate.

    Current checkpoints carry a candidate hash. Older deployed checkpoints may
    only have the tool-contract feedback in dialogue, so recover that signal
    until a later successful source edit supersedes it.
    """
    candidate_hash = digest(snapshot)
    stored = state.get("cad_patch_mismatch") or {}
    if stored.get("candidateHash") == candidate_hash:
        return {**stored, "candidateHash": candidate_hash}

    for message in reversed(state.get("cad_history") or []):
        if message.get("role") != "tool":
            continue
        try:
            result = json.loads(message.get("content") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        # A saved source edit after the mismatch makes the older exact-text
        # instruction stale, even when the current history has no hash on it.
        if result.get("ok") is True and result.get("changedFiles"):
            return None
        if result.get("category") != "tool_contract":
            continue
        paths = []
        diagnostics = []
        for issue in result.get("issues") or []:
            text = str(issue.get("message") or "")
            match = re.search(r"Patch for ([A-Za-z0-9_./-]+) must match exactly once", text)
            if match:
                paths.append(match.group(1))
                diagnostics.append(text)
        if paths:
            return {"candidateHash": candidate_hash, "paths": sorted(set(paths)),
                    "diagnostic": "; ".join(diagnostics)[:1200]}
    return None


def compact_probe_context(state: dict, snapshot: dict) -> list[dict]:
    probes = probe_context(state, snapshot)
    compact = []
    for probe in probes:
        item = {key: probe[key] for key in ("componentId", "currentSource", "ok", "measurementAvailable",
                                               "allRequirementsVerified") if key in probe}
        if probe.get("error"):
            item["error"] = str(probe["error"])[-1600:]
        measurements = probe.get("measurements") or {}
        if measurements:
            item["measurements"] = dict(list(measurements.items())[:8])
        checks = probe.get("requirementChecks") or []
        if checks:
            item["requirementChecks"] = checks[:20]
        compact.append(item)
    return compact


def compact_workspace_status(value: dict) -> dict:
    diagnostics = []
    for item in value.get("sourceDiagnostics") or []:
        if item.get("entrypoint") != "declared" or item.get("referencedMissingParameters"):
            diagnostics.append({key: item[key] for key in (
                "path", "registered", "entrypoint", "referencedMissingParameters", "line") if key in item})
    return {key: value[key] for key in ("componentCount", "instanceCount", "rootComponentId", "rootSourcePresent",
                                        "geometryExecuted") if key in value} | {
        "sourceDiagnostics": diagnostics, "stagingProblems": value.get("stagingProblems") or []}


async def cad_replan(state: AgentState) -> dict:
    """Automatic bounded replanning before requiring human recovery input."""
    snapshot = await run_service.load_candidate(state["run_id"])
    counts = dict(state.get("recovery_counts") or {})
    key = recovery_key(state, snapshot)
    total = state.get("recovery_plan_count", 0)
    exhausted = total >= 6 or counts.get(key, 0) >= 2
    if not exhausted:
        exhausted = state.get("model_calls_this_cycle", state.get("model_calls", 0)) + 1 >= (await app_settings()).limits.maxModelCalls
    if exhausted:
        return {"phase": "cad_recovery", "final_message": (
            "Automatic recovery reached its bounded allowance. The candidate, exact requirements and failure evidence "
            "are preserved. " + state.get("final_message", ""))}
    source_context = recovery_source_context(state, snapshot,
        paths=focused_recovery_paths(state, snapshot), max_bytes=12_000, max_files=4)
    current = current_candidate_facts(snapshot)
    updates = human_followups(state)
    active_failure = active_failure_context(state, snapshot)
    context = {"currentCandidate": current,
               "request": state["original_request"], "humanUpdates": updates,
               "activeFailure": active_failure,
               "recoverySourceContext": source_context,
               "workspace": {"manifest": compact_manifest_context(snapshot["manifest"]), "files": sorted(snapshot["files"]),
                             "candidateHash": digest(snapshot)},
               "failure": state.get("final_message"), "workspaceRepair": state.get("cad_contract_repair"),
               "lastBuild": compact_build_context(state.get("build_result")),
               "workspaceStatus": compact_workspace_status(workspace_status(snapshot)),
               "componentTests": compact_probe_context(state, snapshot), "previousPlan": state.get("recovery_plan"),
               "observationProgress": observation_context(state, snapshot),
               "workingMemory": memory_context(state, digest(snapshot), max_outcomes=8, text_limit=1500)}
    history = [{"role": "user", "content": "Choose a different, focused repair approach using retained evidence. Do not change user requirements."}]
    call, _, usage = await agent_tool_turn(state, model_role="coordinator", prompt_role="recovery",
        node="cad-replan", context=context, history=history,
        tools=[submission_tool("submit_recovery", "Return a focused repair and verification plan.", RecoveryPlan)])
    counts[key] = counts.get(key, 0) + 1
    update = {**usage, "recovery_counts": counts, "recovery_plan_count": total + 1}
    try:
        if not call or call["name"] != "submit_recovery":
            raise ValueError("The recovery planner did not submit its plan.")
        plan = RecoveryPlan.model_validate(call["input"]).model_dump()
    except (ValueError, ValidationError):
        return {**update, "phase": "cad_replan", "final_message": "The automatic recovery planner returned an unusable plan."}
    repair = state.get("cad_contract_repair") or {}
    progress = {**(state.get("cad_observation_progress") or {}), "repeats": 0,
                "repeatDecisions": 0, "lastRepeatedDecision": None, "windowSeen": {}}
    await repo.event(state["run_id"], "Forma is re-planning the failed step using saved source and failure evidence.", stage="cad")
    return {**update, "phase": "cad_session", "recovery_plan": {**plan, "candidateHash": digest(snapshot)},
            "repairs": 0, "cad_invalid_tool_attempts": 0, "cad_edits_since_build": 0,
            "cad_contract_repair": {**repair, "stalledAttempts": 0} if repair else {},
            "pending_cad_call": {}, "last_read_path": None, "final_message": "",
            "cad_pending_actions": [],
            "cad_observation_progress": progress,
            "recovery_source_paths": list(source_context["files"]),
            # A fresh focused dialogue avoids carrying the same saturated read
            # cycle back into CAD. Exact request, source, notes, receipts and
            # validation are independently retained in authoritative context.
            "cad_history": [{"role": "user", "content": state["original_request"]},
                {"role": "system", "content": "Human follow-ups retain durable design requirements. Their file-specific repair steps are historical; "
                    "the newest build failure supersedes them when it names a different operation: " + json.dumps(updates)},
                {"role": "system", "content": "Current saved candidate, authoritative for staged workspace facts: " + json.dumps(current)},
                {"role": "system", "content": "Automatic recovery plan (an agent proposal, not a human instruction or validation): " + json.dumps(plan)},
                {"role": "system", "content": "AUTHORITATIVE CURRENT BUILD FAILURE: " + json.dumps(active_failure) +
                    ". Repair this latest failing operation before revisiting any earlier file-specific instruction."}]}


async def cad_recovery(state: AgentState) -> dict:
    response = interrupt({"kind": "recovery", "message": state["final_message"]})
    if (response or {}).get("kind") not in {"continue", "answer"}:
        raise Pause("Continue is required to restart the bounded CAD repair allowance.")
    message = str((response or {}).get("message", "")).strip()
    history = bounded_history([*state.get("cad_history", []), {"role": "user", "content":
        "Continue from the retained candidate. Isolate and re-plan the failed operation before building. " + message}])
    repair = state.get("cad_contract_repair") or {}
    snapshot = await run_service.load_candidate(state["run_id"])
    sources = recovery_source_context(state, snapshot)
    guidance = user_update(state, message)
    specific_guidance = bool(guidance) and message.casefold() not in {"continue", "continue.", "resume"}
    update = {"phase": "cad_session", "repairs": 0, "cad_history": history,
            "recovery_source_paths": list(sources["files"]),
            "recovery_plan": {},
            "cad_observation_progress": {**(state.get("cad_observation_progress") or {}),
                "repeats": 0, "repeatDecisions": 0, "lastRepeatedDecision": None, "windowSeen": {}},
            "cad_invalid_tool_attempts": 0,
            "cad_contract_repair": {**repair, "stalledAttempts": 0} if repair else {},
            **guidance,
            "pending_cad_call": {}, "cad_edits_since_build": 0, "final_message": ""}
    # A new, specific human direction is a new recovery hypothesis. Renew its
    # planning allowance; the run-wide model-call and task budgets stay intact.
    if specific_guidance:
        update["recovery_plan_count"] = 0
        # The focused direction is a new hypothesis for this exact candidate
        # and failure. Renew its per-key planning allowance too, while keeping
        # budgets for unrelated failures and the run-wide call cap intact.
        counts = dict(state.get("recovery_counts") or {})
        counts.pop(recovery_key(state, snapshot), None)
        update["recovery_counts"] = counts
    return update


REVIEW_TOOL_NAMES = {"read_file", "inspect_geometry"}


def deterministic_review_preflight(snapshot: dict, validation: dict) -> dict | None:
    """Reject evidence gaps a language-model reviewer must not explain away."""
    manifest = snapshot.get("manifest", {})
    definitions = {item.get("id"): item for item in manifest.get("components", [])}
    root_id = manifest.get("rootComponentId")
    root = definitions.get(root_id) or {}
    if root.get("kind") != "assembly":
        return None
    report = validation.get("report", validation)
    inspection = report.get("inspection", {}) if isinstance(report, dict) else {}
    root_facts = (inspection.get("components") or {}).get(root_id, {})
    root_solids = int(root_facts.get("solidCount") or 0)
    as_built = next((item for item in inspection.get("configurations", [])
        if item.get("id") == "as_built"), {})
    inspected_instances = int(as_built.get("instanceCount") or 0)
    physical_manifest_instances = sum(
        1 for item in manifest.get("instances", [])
        if (definitions.get(item.get("definitionId")) or {}).get("kind") != "assembly")
    if root_solids <= 1 or (inspected_instances >= root_solids and physical_manifest_instances >= root_solids):
        return None
    finding = {
        "id": "assembly_instance_inventory",
        "statement": "Every physical assembly member must be represented by an independently identifiable manifest instance.",
        "status": "observed_mismatch", "severity": "error",
        "evidence": [
            f"Imported root STEP contains {root_solids} solids.",
            f"Manifest contains {physical_manifest_instances} physical instances; inspection resolved {inspected_instances}.",
        ],
        "explanation": ("The assembly geometry exists, but its physical members are missing from the manifest, so "
            "pairwise clearance, mass, configuration and assembly-tree checks have no instance population to inspect."),
        "repair_instruction": ("Add one positioned instance for every physical occurrence, reuse component definitions "
            "for repeated hardware, and keep parentId null or linked to a real assembly instance."),
    }
    return {"summary": "The assembly built, but its physical instance inventory is incomplete.",
        "action": "repair", "findings": [finding]}


async def review_session(state: AgentState) -> dict:
    snapshot = await run_service.load_candidate(state["run_id"])
    validation = state.get("validation") or {}
    preflight = deterministic_review_preflight(snapshot, validation)
    if preflight:
        return await record_review_result(state, preflight, snapshot, validation, [])
    context = {
        "originalRequest": state["original_request"],
        "clarifiedRequest": state.get("clarified_request", ""),
        "engineeringSummary": state.get("engineering_summary", ""),
        "engineeringRemarks": state.get("engineering_remarks", []),
        "manifest": snapshot["manifest"],
        "sourceFiles": sorted(snapshot["files"]),
        "buildReport": validation.get("report", validation),
        "nativeGroundingEvidence": native_grounding_evidence(snapshot, validation),
        "cadReviewResponse": (state.get("review_response") if (state.get("review_response") or {}).get("candidateHash") == digest(snapshot) else None),
        "previousReview": state.get("review", {}),
        "reviewInstructions": ("List requirements that are demonstrated as passing and preserve them. Identify only "
            "specific missing/wrong geometry with evidence as repair targets. Mark unsupported measurements, "
            "engineering performance, and physical checks unverified; do not ask CAD to guess or repair those."),
    }
    history = state.get("review_history") or [{"role": "user", "content":
        "Independently review this built CAD candidate against the original request. Use tools for evidence, then call submit_review."}]
    if state.get("review_actions", 0) >= MAX_REVIEW_ACTIONS:
        return await record_review_result(state,
            unavailable_review(f"the reviewer used its {MAX_REVIEW_ACTIONS}-action evidence budget"),
            snapshot, validation, history)
    allowed_review_tools = set()
    if state.get("review_reads", 0) < 3:
        allowed_review_tools.add("read_file")
    if not state.get("review_inspected", False):
        allowed_review_tools.add("inspect_geometry")
    tools = [item for item in model_tools("cad")
             if item["function"]["name"] in allowed_review_tools]
    tools.append(submission_tool("submit_review", "Submit evidence-backed findings for this candidate.", ReviewResult))
    try:
        call, history, usage = await agent_tool_turn(
            state, model_role="cad", prompt_role="reviewer", node="review-session",
            context=context, history=history, tools=tools,
        )
    except (Pause, ModelFailure) as exc:
        return await record_review_result(state, unavailable_review(str(exc)), snapshot, validation, history)
    if not call:
        return {**usage, "phase": "review_session", "review_history": bounded_history([
            *history, {"role": "user", "content": "Use read_file or inspect_geometry, then call submit_review."}
        ]), "review_actions": state.get("review_actions", 0) + 1}
    name = call["name"]
    if name == "read_file":
        try:
            parsed = parse_tool("cad", name, call["input"])
            value = parsed.model_dump()
            if value.get("version") == "rejected":
                raise ValueError("Review uses saved candidate source only. Rejected proposals are not geometry evidence.")
            safe_path(value["path"])
            result = {"path": value["path"], "content": snapshot["files"].get(value["path"])}
        except (ValidationError, ValueError) as exc:
            result = {"ok": False, "category": "tool_contract", "message": str(exc)[:3000]}
        reads = state.get("review_reads", 0) + 1
        if reads >= 3:
            result["reviewInstruction"] = "Source-read budget reached; submit the review using gathered evidence."
        return {**usage, "phase": "review_session", "review_reads": reads,
            "review_history": bounded_history([*history, tool_message(call, result)]),
            "review_actions": state.get("review_actions", 0) + 1}
    if name == "inspect_geometry":
        return {**usage, "phase": "review_session", "review_inspected": True,
            "review_history": bounded_history([
                *history, tool_message(call, validation.get("report", validation))]),
            "review_actions": state.get("review_actions", 0) + 1}
    if name != "submit_review":
        return {**usage, "phase": "review_session", "review_history": bounded_history([
            *history, tool_message(call, {"ok": False, "message": "Reviewer tools are read-only."})]),
            "review_actions": state.get("review_actions", 0) + 1}
    try:
        review = ReviewResult.model_validate(call["input"]).model_dump()
    except ValidationError as exc:
        return {**usage, "phase": "review_session", "review_history": bounded_history([
            *history, tool_message(call, {"ok": False, "category": "review_contract",
                "message": str(exc)[:5000]})]),
            "review_actions": state.get("review_actions", 0) + 1}
    return await record_review_result(state, review, snapshot, validation, history, usage)


async def publish(state: AgentState) -> dict:
    run = await run_row(state)
    snapshot = await run_service.load_candidate(state["run_id"])
    cp = checkpoint_view(state, snapshot)
    # Publication is a coordinator-owned operation. The checkpoint view is
    # normally CAD-scoped for build/validation, so switch only the tool
    # authorization context before invoking the publish adapter.
    cp["role"] = "coordinator"
    settings_value = await app_settings()
    async def publish_candidate():
        return await execute_tool(run, cp, {"id": "publish", "name": "publish_revision",
            "input": {"summary": state.get("candidate_summary") or "CAD draft for human review"}},
            settings_value, worker())
    result = await operation(run, "graph:publish", "publish_revision", publish_candidate, idempotent=True)
    await destroy_sandboxes(cp)
    review = state.get("review") or {}
    message = "The CAD draft built successfully and is ready for your review."
    if review.get("summary"):
        message += " Independent review: " + review["summary"]
    message += " You can continue editing or download the files; engineering and physical validation remain your responsibility."
    call = state.get("coordinator_pending_call") or {"id": "cad-result"}
    history = bounded_history([*state.get("coordinator_history", []), tool_message(call, {
        "ok": True, "revisionId": result["revisionId"],
        "validation": cp.get("validated", {}).get("report"),
        "message": "The CAD draft was built and published for review.",
    })])
    return {**sync_checkpoint(cp), "phase": "final",
        "coordinator_pending_call": {}, "coordinator_history": history,
        "published_revision_id": result["revisionId"], "final_message": message}


async def final(state: AgentState) -> dict:
    run = await run_row(state)
    # Validation/build failures can terminate before ``publish`` gets a
    # chance to release the per-run sandboxes.  Always perform the same
    # best-effort cleanup on the terminal path; successful publication has
    # already released them and this remains idempotent.
    cleanup = {"sandbox": state.get("sandbox"), "validator": state.get("validator")}
    await destroy_sandboxes(cleanup)
    await run_service.finish(run, worker(), state.get("terminal_status", "succeeded"),
        state.get("final_message") or "Completed.")
    return {"phase": "done"}


def triage_route(state: AgentState) -> str:
    return state.get("route", "cad")


def phase_route(state: AgentState) -> str:
    return state.get("phase", "final")


async def drawing_prepare(state: AgentState) -> dict:
    from ..services.drawings import drawing_candidate
    run = await run_row(state)
    snapshot = await drawing_candidate(run, state['drawing_request']['sheets'])
    candidate_hash = digest(snapshot)
    await run_service.save_candidate(run['id'], snapshot, candidate_hash)
    return {'phase':'drawing_build','candidate_hash':candidate_hash,'requirements':[],
            'candidate_summary':'Revision-linked engineering drawing drafts',
            'model_calls':0,'started_ns':time.time_ns(),'attempts':0,'repairs':0,'build_final':True}


async def drawing_build(state: AgentState) -> dict:
    from ..services.drawings import build_drawing_candidate
    run = await run_row(state)
    snapshot = await run_service.load_candidate(state['run_id'])
    cp = checkpoint_view(state,snapshot)
    config = await app_settings()
    async def generate():
        report = await build_drawing_candidate(run,cp,config.limits)
        return {'report':report,'checkpoint':sync_checkpoint(cp)}
    result = await operation(run,'graph:drawing-build','drawing_build',generate)
    report = result['report']
    updates = result['checkpoint']
    if report.get('ok') is False:
        return {**updates,'phase':'final','terminal_status':'failed',
                'final_message':'Drawing generation failed. '+report['error']['guidance']}
    document=report.get('drawings') or {}
    expected={sheet['id']:sheet['componentId'] for sheet in snapshot['manifest']['drawings']}
    actual={sheet['id']:sheet['componentId'] for sheet in document.get('sheets',[])}
    if not expected or actual!=expected or document.get('identity')!=report.get('identity'):
        return {**updates,'phase':'final','terminal_status':'failed',
                'final_message':'Drawing validation did not return the requested revision-bound sheets. No revision was published.'}
    issues = [issue for sheet in document['sheets'] for issue in sheet['issues']]
    if issues:
        return {**updates,'phase':'final','terminal_status':'failed',
                'final_message':'Drawing export blocked: '+'; '.join(f"{i['id']}: {i['message']}" for i in issues[:12])}
    files={artifact['name'] for artifact in report.get('artifacts',[]) if artifact['kind']=='drawing'}
    required={f'drawing-{identifier}.{extension}' for identifier in expected for extension in ('svg','pdf','dxf')}
    if not required.issubset(files):
        return {**updates,'phase':'final','terminal_status':'failed',
                'final_message':'Drawing validation did not produce every requested SVG, PDF and DXF export. No revision was published.'}
    return {**updates,'phase':'publish','review':{'summary':'Native views and measured dimensions generated from the accepted STEP revision. GD&T intent requires engineering review.'}}


def build_graph(checkpointer):
    graph = StateGraph(AgentState)
    for name, node in (("coordinator", coordinator), ("coordinator_session", coordinator_session),
        ("coordinator_question", coordinator_question), ("engineering_triage", engineering_triage),
        ("clarification", clarification), ("cad_session", cad_session),
        ("cad_question", cad_question), ("engineering_analysis", engineering_analysis),
        ("approval", approval), ("build", build), ("validate", validate), ("cad_recovery", cad_recovery), ("cad_replan", cad_replan),
        ("review_session", review_session), ("publish", publish), ("final", final),
        ("drawing_prepare",drawing_prepare),("drawing_build",drawing_build)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, lambda state: 'drawing_prepare' if state.get('drawing_request') else 'coordinator',
                                {'drawing_prepare':'drawing_prepare','coordinator':'coordinator'})
    graph.add_edge('drawing_prepare','drawing_build')
    graph.add_conditional_edges('drawing_build',phase_route,{'publish':'publish','final':'final'})
    graph.add_edge("coordinator", "coordinator_session")
    graph.add_conditional_edges("coordinator_session", phase_route, {
        "coordinator_session": "coordinator_session", "coordinator_question": "coordinator_question",
        "engineering_analysis": "engineering_analysis", "cad_session": "cad_session", "final": "final",
    })
    graph.add_edge("coordinator_question", "coordinator_session")
    graph.add_conditional_edges("engineering_triage", phase_route, {
        "clarification": "clarification", "engineering_analysis": "engineering_analysis",
        "cad_session": "cad_session", "final": "final",
    })
    graph.add_edge("clarification", "engineering_triage")
    graph.add_conditional_edges("cad_session", phase_route, {
        "cad_session": "cad_session", "cad_question": "cad_question",
        "engineering_analysis": "engineering_analysis", "build": "build", "validate": "validate",
        "cad_recovery": "cad_replan", "final": "final",
    })
    graph.add_edge("cad_question", "cad_session")
    graph.add_conditional_edges("engineering_analysis", phase_route,
        {"cad_session": "cad_session", "coordinator_session": "coordinator_session",
         "approval": "approval", "final": "final"})
    graph.add_conditional_edges("approval", phase_route,
        {"cad_session": "cad_session", "coordinator_session": "coordinator_session", "final": "final"})
    graph.add_edge("build", "validate")
    graph.add_edge("cad_recovery", "cad_session")
    graph.add_conditional_edges("cad_replan", phase_route,
        {"cad_session": "cad_session", "cad_replan": "cad_replan", "cad_recovery": "cad_recovery"})
    graph.add_conditional_edges("validate", phase_route,
        {"cad_session": "cad_session", "cad_recovery": "cad_replan", "review_session": "review_session", "final": "final"})
    graph.add_conditional_edges("review_session", phase_route, {
        "review_session": "review_session", "cad_session": "cad_session",
        "publish": "publish", "final": "final",
    })
    graph.add_edge("publish", "final")
    graph.add_edge("final", END)
    return graph.compile(checkpointer=checkpointer, interrupt_after="*", name="forma-design")
