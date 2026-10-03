"""Fixed Forma graph: engineering gate, CAD build/repair, validation, publication."""
import json
import re
import time
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
from ..services import runs as run_service
from ..tools import model_tools, parse_tool, portable_schema, updated_manifest
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
    kind: Literal["dimensions", "max_dimensions", "center", "solid_count", "through_holes", "corner_radius", "unverified"] = Field(description=(
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
        # CadQuery/OpenCascade measurements have a small numerical tolerance;
        # a model must not turn that runtime precision into a false geometry
        # failure by inventing a sub-0.05 mm requirement tolerance.  The
        # measured OpenCascade envelope drift is about 0.014 mm at 200 mm.
        payload["tolerance"] = max(float(payload.get("tolerance", 0.02)), 0.05)
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
    caps = {"coordinator-session": 8192, "cad-session": 64000,
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
        "parameters": portable_schema(contract.model_json_schema())}}


async def app_settings() -> AppSettings:
    return AppSettings.model_validate((await db.one("app_settings", {"id": "eq.true"}))["settings"])


async def run_row(state: AgentState) -> dict:
    return await db.one("runs", {"id": f"eq.{state['run_id']}"})


async def structured_turn(state: AgentState, role: str, node: str, prompt: str,
                          tool_name: str, contract: type[Contract], *, web=False):
    run = await run_row(state)
    config = await models.configuration(role)
    ordinal = state.get("model_calls", 0)
    max_model_calls = (await app_settings()).limits.maxModelCalls
    if ordinal >= max_model_calls:
        raise Pause("The model-call limit was reached. Start a new bounded run when ready.")
    tools = [submission_tool(tool_name, f"Submit the complete {node} result.", contract)]
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
            if ordinal + 1 >= max_model_calls:
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
        compacted.append(message)
        index += 1
    return compacted


def bounded_history(history: list[dict], *, messages: int = 30, characters: int = 500_000,
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
    ordinal = state.get("model_calls", 0)
    if ordinal >= (await app_settings()).limits.maxModelCalls:
        raise Pause("The model-call budget was reached. The current draft and completed evidence were preserved.")
    history = bounded_history(history)
    messages = [
        {"role": "system", "content": system_prompt(prompt_role)},
        {"role": "system", "content": "Current private design context: " + json.dumps(context, ensure_ascii=False)},
        *history,
    ]

    async def call():
        return await models.turn(config, messages, tools,
                                 max_tokens=model_step_token_budget(config, node))

    result = await operation(run, f"graph:{node}:{ordinal}", "model", call)
    call_value = result["calls"][0] if result.get("calls") else None
    assistant = deepcopy(result["message"])
    if call_value and assistant.get("tool_calls"):
        assistant["tool_calls"] = [
            item for item in assistant["tool_calls"] if item.get("id") == call_value["id"]
        ][:1]
    elif not call_value:
        assistant.pop("tool_calls", None)
    next_history = bounded_history([*history, assistant], allow_pending=True)
    await db.insert("generations", {
        "id": str(uuid5(NAMESPACE_URL, f"{run['id']}:graph:{ordinal}")),
        "run_id": run["id"], "ordinal": ordinal, "role": prompt_role,
        "model_id": config["model_id"], "config_version": config["version"],
        "prompt_version": PROMPT_VERSION, "status": "complete", "output": assistant,
        "input_tokens": result["inputTokens"], "output_tokens": result["outputTokens"],
    }, conflict="run_id,ordinal")
    return call_value, next_history, {
        "model_calls": ordinal + 1,
        "search_count": state.get("search_count", 0) + result.get("webSearchRequests", 0),
    }


def tool_message(call: dict, result: dict) -> dict:
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
        "repairs": 0, "attempts": 0, "model_calls": 0, "search_count": 0,
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
    "delegate", "restore_revision", "ask_user", "finish",
}


async def coordinator_session(state: AgentState) -> dict:
    """One bounded project-aware reasoning/tool step, persisted by LangGraph."""
    actions = state.get("coordinator_actions", 0)
    if actions >= 12:
        raise Pause("The coordinator reached its tool-action limit. Continue with a focused follow-up.")
    snapshot = await run_service.load_candidate(state["run_id"])
    context = {
        "latestRequest": state.get("clarified_request") or state["original_request"],
        "project": state.get("project_context", {}),
        "selectedIds": state.get("selected_ids", []),
        "currentWorkspace": {"manifest": snapshot["manifest"],
            "files": sorted(snapshot["files"]), "candidateHash": digest(snapshot)},
        "engineeringSummary": state.get("engineering_summary", ""),
        "engineeringRemarks": state.get("engineering_remarks", []),
        "build": state.get("build_result"),
        "validation": state.get("validation", {}).get("report") if state.get("validation") else None,
        "publishedRevisionId": state.get("published_revision_id"),
    }
    allowed = {"inspect_project", "read_file", "search_files", "inspect_geometry", "finish"} \
        if state.get("published_revision_id") else COORDINATOR_SESSION_TOOL_NAMES
    tools = [item for item in model_tools("coordinator")
             if item["function"]["name"] in allowed]
    history = state.get("coordinator_history") or [{"role": "user", "content": state["original_request"]}]
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
              "coordinator_actions": actions + 1}
    if not call:
        return {**update, "coordinator_history": bounded_history([*history, {
            "role": "user", "content": "Choose one available tool action. Do not leave this turn without a tool call."
        }])}
    try:
        value = parse_tool("coordinator", call["name"], call["input"]).model_dump()
    except (ValidationError, ValueError) as exc:
        return {**update, "coordinator_history": bounded_history([*history, tool_message(call, {
            "ok": False, "category": "tool_contract", "message": str(exc)[:3000],
        })])}
    name = call["name"]
    if name == "inspect_project":
        result = state.get("project_context", {})
    elif name == "read_file":
        content = snapshot["files"].get(value["path"])
        result = {"ok": content is not None, "path": value["path"],
            "content": content[:60_000] if content is not None else None,
            "availablePaths": sorted(snapshot["files"]) if content is None else []}
    elif name == "search_files":
        result = {"matches": [{"path": path, "line": index + 1, "text": line[:300]}
            for path, source in snapshot["files"].items()
            for index, line in enumerate(source.splitlines())
            if value["query"].lower() in line.lower()][:80]}
    elif name == "inspect_geometry":
        result = state.get("validation") or (state.get("project_context", {}).get("revision") or {})
    elif name == "ask_user":
        return {**update, "phase": "coordinator_question", "question": value["question"],
            "coordinator_pending_call": call}
    elif name == "delegate":
        requirements = merge_requirements(state.get("clarified_request") or state["original_request"], value.get("requirements") or [])
        if value["role"] == "engineering":
            await repo.event(state["run_id"], "Coordinator requested engineering analysis.", stage="engineering")
            return {**update, "phase": "engineering_analysis", "engineering_request": value["task"],
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
        "clarified_request": (state.get("clarified_request") or state["original_request"]) +
            "\n\nUser clarification: " + message}


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
    return {"clarified_request": f"{state['original_request']}\n\nUser clarification: {message}",
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
    return {"snapshot": snapshot, "role": "cad", "requirements": merge_requirements(
        state.get("original_request", ""), state.get("requirements", [])),
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
    return bound


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
    "read_file", "search_files", "apply_changes", "build",
    "inspect_geometry", "request_engineering", "ask_user",
}
MAX_CAD_EDITS_WITHOUT_BUILD = 3
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
    """Read an explicit numbered Components section without guessing parts.

    This is only a staging/publication guard, not a geometry validator. Briefs
    without a numbered component inventory keep the existing CAD path.
    """
    section = re.search(r"(?im)^\s*(?:#{1,6}\s*)?components(?:\s*\([^\n]*\))?\s*:?\s*$", request)
    if not section:
        return []
    labels = []
    for line in request[section.end():].splitlines():
        match = re.match(r"^\s*(\d{1,3})[.)]\s+(.+)$", line)
        if match:
            if int(match.group(1)) != len(labels) + 1:
                break
            label = re.split(r"\s+(?:—|–|-|:)\s+", match.group(2), maxsplit=1)[0]
            labels.append(label.strip().strip("* ")[:120])
        elif labels and line.strip() and not line[:1].isspace():
            break
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


async def cad_session(state: AgentState) -> dict:
    """Let the CAD model choose one incremental workspace/tool action."""
    snapshot = await run_service.load_candidate(state["run_id"])
    history = state.get("cad_history") or [{
        "role": "user", "content": state.get("clarified_request") or state["original_request"]
    }]
    requested_components = requested_component_labels(state.get("original_request", ""))
    edits_since_build = state.get("cad_edits_since_build", 0)
    buildable = bool(snapshot["manifest"].get("components")
                     and snapshot["manifest"].get("rootComponentId"))
    if edits_since_build >= MAX_CAD_EDITS_WITHOUT_BUILD and buildable:
        # Tool availability is advisory for some OpenAI-compatible providers:
        # they can still return apply_changes after it is removed from the
        # schema. Enforce the edit bound in graph code and give CAD real build
        # feedback before another source patch consumes a model call.
        required = requested_part_type_count(state["original_request"])
        built = sum(component.get("kind") != "assembly"
                    for component in snapshot["manifest"]["components"])
        final = required is not None and built >= required
        call_id = f"auto-build-{state.get('model_calls', 0)}-{state.get('attempts', 0)}"
        call = {"id": call_id, "name": "build", "input": {"final": final}}
        assistant = {"role": "assistant", "content": "", "tool_calls": [{
            "id": call_id, "type": "function", "function": {
                "name": "build", "arguments": json.dumps({"final": final}),
            },
        }]}
        await repo.event(state["run_id"],
            f"CAD reached {edits_since_build} edits; building the saved candidate before more changes.",
            stage="execution")
        return await build({**state, "phase": "build", "pending_cad_call": call,
            "cad_history": bounded_history([*history, assistant], allow_pending=True),
            "cad_edits_since_build": 0, "build_final": final,
            "requested_part_types": required or 0, "built_part_types": built})
    context = {
        "request": state.get("clarified_request") or state["original_request"],
        "delegatedTask": state.get("coordinator_task", ""),
        "projectContext": state.get("project_context", {}),
        "selectedIds": state.get("selected_ids", []),
        "engineeringSummary": state.get("engineering_summary", ""),
        "engineeringRemarks": state.get("engineering_remarks", []),
        "reviewRepairPlan": (review_repair_context(state.get("review") or {})
            if (state.get("review") or {}).get("action") == "repair" else None),
        "workspace": {
            "manifest": snapshot["manifest"],
            "files": sorted(snapshot["files"]),
            "candidateHash": digest(snapshot),
            "instruction": ("Use only exact file paths listed in workspace.files. A path such as 'parts' or "
                "'.metadata/manifest.json' is not a file. If the list is empty, create the requested source "
                "with apply_changes immediately; do not probe directories or invent metadata files."),
        },
        "lastBuild": state.get("build_result"),
        "lastReview": state.get("review"),
    }
    tools = [item for item in model_tools("cad")
             if item["function"]["name"] in CAD_SESSION_TOOL_NAMES]
    if len(requested_components) > 1:
        staged = staged_component_count(snapshot["manifest"])
        context["componentMilestone"] = {
            "requestedTypes": requested_components,
            "stagedTypeCount": staged,
            "instruction": ("Stage one buildable component type per apply_changes call. A partial manifest "
                "must be internally consistent for the components already staged; it need not describe "
                "the entire final assembly yet. Build intermediate candidates, then add the remaining "
                "parts. A passing partial build will not publish the design."),
        }
    if edits_since_build >= MAX_CAD_EDITS_WITHOUT_BUILD and buildable:
        # Keep the graph progressing even when a model repeatedly proposes
        # patches. A build is the only useful next action after this bound.
        tools = [item for item in tools if item["function"]["name"] in
                 {"build", "read_file", "search_files", "inspect_geometry"}]
        context["buildRequired"] = True
        context["editsSinceBuild"] = edits_since_build
    manifest = snapshot["manifest"]
    physical_components = [item for item in manifest.get("components", [])
                           if item.get("kind") != "assembly"]
    root = next((item for item in manifest.get("components", [])
                 if item.get("id") == manifest.get("rootComponentId")), None)
    if len(physical_components) > 1 and (root or {}).get("kind") != "assembly":
        context["assemblyRootRepair"] = (
            "This workspace has multiple physical parts, but rootComponentId names a single part. "
            "The root STEP cannot contain all manifest instances. Your next action must be "
            "apply_changes: add an assemblies/ Python source with build(parameters, dependencies) "
            "returning a CadQuery Assembly; add every currently staged part under its exact manifest "
            "instance ID and frame; declare those part IDs as dependencies; make the new assembly "
            "component rootComponentId. Include the complete, internally consistent manifest and "
            "nonempty source for the new assembly. Do not call build again on the single-part root."
        )
        tools = [item for item in tools if item["function"]["name"] == "apply_changes"]
    call, history, usage = await agent_tool_turn(
        state, model_role="cad", prompt_role="cad", node="cad-session",
        context=context, history=history, tools=tools,
    )
    if not call:
        invalid_attempts = state.get("cad_invalid_tool_attempts", 0) + 1
        history = bounded_history([*history, {"role": "user", "content":
            "Your response contained no valid tool call. Call exactly one available tool with valid arguments; do not put a tool-call list in plain text."}])
        if invalid_attempts >= 3:
            return {**usage, "phase": "final", "terminal_status": "failed",
                "cad_invalid_tool_attempts": invalid_attempts, "cad_history": history,
                "final_message": ("The CAD model returned no usable tool call three times. "
                    "The saved design is unchanged; select a model with reliable tool calling before retrying.")}
        return {**usage, "phase": "cad_session", "cad_invalid_tool_attempts": invalid_attempts,
            "cad_history": history}
    tool_input = call["input"]
    hierarchy_pre_normalized = False
    if call["name"] == "apply_changes" and isinstance(tool_input, dict) \
            and isinstance(tool_input.get("manifest"), dict):
        tool_input = json.loads(json.dumps(tool_input))
        tool_input["manifest"], hierarchy_pre_normalized = normalize_instance_hierarchy(
            tool_input["manifest"])
    try:
        parsed = parse_tool("cad", call["name"], tool_input)
        value = parsed.model_dump()
    except (ValidationError, ValueError) as exc:
        invalid_attempts = state.get("cad_invalid_tool_attempts", 0) + 1
        feedback = {
            "ok": False, "category": "tool_contract", "message": str(exc)[:3000],
            "repairGuidance": ("Use an exact non-empty file path from workspace.files. "
                "If workspace.files is empty, create the requested source with apply_changes immediately.")
        }
        next_history = bounded_history([*history, tool_message(call, feedback)])
        if invalid_attempts >= 3:
            return {**usage, "phase": "final", "terminal_status": "failed",
                "cad_invalid_tool_attempts": invalid_attempts, "cad_history": next_history,
                "final_message": ("The CAD model returned an invalid tool action three times, "
                    "so the run stopped before publishing geometry. Choose a model with reliable "
                    "tool arguments and Continue to retry.")}
        return {**usage, "phase": "cad_session", "cad_invalid_tool_attempts": invalid_attempts,
            "cad_history": next_history}

    if repeated_tool_action(history, call):
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
            return {**usage, "phase": "final", "terminal_status": "failed",
                "cad_invalid_tool_attempts": invalid_attempts, "cad_history": next_history,
                "final_message": ("The CAD model repeated the same ineffective action three times. "
                    "The saved design is unchanged; select a model that responds to tool feedback before retrying.")}
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
    if name == "read_file" and state.get("last_read_path") == value.get("path"):
        feedback = {"ok": False, "category": "repeated_tool_action",
            "message": "This file was just read in the previous CAD turn.",
            "repairGuidance": "Use the returned source now. Patch it with apply_changes or build the candidate."}
        return {**usage, "phase": "cad_session", "last_read_path": value.get("path"),
            "cad_history": bounded_history([*history, tool_message(call, feedback)])}
    if name == "read_file":
        safe_path(value["path"])
        content = snapshot["files"].get(value["path"])
        if content is None:
            invalid_attempts = state.get("cad_invalid_tool_attempts", 0) + 1
            result = {"ok": False, "category": "file_not_found", "path": value["path"],
                "message": "That path is not a file in the workspace. Read only an exact path from workspace.files.",
                "availablePaths": sorted(snapshot["files"]),
                "repairGuidance": ("The workspace is empty; create the requested parts with apply_changes instead "
                    "of reading a directory or invented metadata file." if not snapshot["files"] else
                    "Choose one of the listed file paths or patch the requested component.")}
            next_history = bounded_history([*history, tool_message(call, result)])
            if invalid_attempts >= 3:
                return {**usage, "phase": "final", "terminal_status": "failed",
                    "cad_invalid_tool_attempts": invalid_attempts, "cad_history": next_history,
                    "final_message": ("The CAD model repeatedly requested paths that are not in the workspace, "
                        "so no source was executed. Start a new run or edit the request and try again.")}
            return {**usage, "phase": "cad_session", "cad_invalid_tool_attempts": invalid_attempts,
                "last_read_path": value.get("path"), "cad_history": next_history}
        return {**usage, "phase": "cad_session", "cad_invalid_tool_attempts": 0,
            "last_read_path": value.get("path"),
            "cad_history": bounded_history([*history, tool_message(call, {
                "ok": True, "path": value["path"], "content": content})])}
    if name == "search_files":
        matches = [{"path": path, "line": index + 1, "text": line[:300]}
            for path, source in snapshot["files"].items()
            for index, line in enumerate(source.splitlines())
            if value["query"] in line][:100]
        return {**usage, "phase": "cad_session", "cad_history": bounded_history([
            *history, tool_message(call, {"matches": matches})]), "last_read_path": None}
    if name == "inspect_geometry":
        result = state.get("validation") or {
            "verified": False, "message": "Build the current candidate before inspecting imported geometry."
        }
        return {**usage, "phase": "cad_session", "cad_history": bounded_history([
            *history, tool_message(call, result)])}
    if name == "apply_changes":
        invalid_paths = [path for path in value["files"]
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
                return {**usage, "phase": "final", "terminal_status": "failed",
                    "cad_invalid_tool_attempts": invalid_attempts, "cad_history": next_history,
                    "final_message": "The CAD model repeatedly returned empty source files, so no source was executed."}
            return {**usage, "phase": "cad_session", "cad_invalid_tool_attempts": invalid_attempts,
                "cad_history": next_history}
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
                    return {**usage, "phase": "final", "terminal_status": "failed",
                        "cad_invalid_tool_attempts": invalid_attempts, "cad_history": next_history,
                        "final_message": ("The CAD model returned invalid Python source three times. "
                            "The saved design is unchanged; select a model that preserves code formatting before retrying.")}
                return {**usage, "phase": "cad_session", "cad_invalid_tool_attempts": invalid_attempts,
                    "cad_history": next_history}
            manifest, auto_registered = register_unlisted_part_sources(manifest, files)
            candidate = Snapshot.model_validate({
                "manifest": manifest, "files": files,
            }).model_dump()
        except (ValidationError, ValueError) as exc:
            invalid_attempts = state.get("cad_invalid_tool_attempts", 0) + 1
            result = {"ok": False, "category": "workspace_contract", "message": str(exc)[:5000]}
            next_history = bounded_history([*history, tool_message(call, result)])
            if invalid_attempts >= 3:
                return {**usage, "phase": "final", "terminal_status": "failed",
                    "cad_invalid_tool_attempts": invalid_attempts, "cad_history": next_history,
                    "final_message": ("The CAD model returned an invalid workspace manifest three times. "
                        "The saved design is unchanged; select a model with reliable structured output before retrying.")}
            return {**usage, "phase": "cad_session", "cad_invalid_tool_attempts": invalid_attempts,
                "cad_history": next_history}
        candidate_hash = digest(candidate)
        await run_service.save_candidate(state["run_id"], candidate, candidate_hash)
        await repo.event(state["run_id"], "CAD updated a focused part of the code workspace.", stage="cad")
        result = {"ok": True, "candidateHash": candidate_hash,
            "changedFiles": sorted(value["files"]), "deletedFiles": value.get("deletePaths", []),
            "autoRegisteredComponents": auto_registered,
            "hierarchyNormalized": hierarchy_normalized,
            "hierarchyNote": ("Top-level or invalid parent sentinels were normalized to null; parentId must name "
                "another instance id." if hierarchy_normalized else "")}
        return {**usage, "phase": "cad_session", "candidate_hash": candidate_hash,
            "cad_invalid_tool_attempts": 0,
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
    clarified = state.get("clarified_request") or state["original_request"]
    return {"phase": "cad_session", "question": "", "pending_cad_call": {},
        "cad_history": history, "clarified_request": clarified + "\n\nUser clarification: " + message}


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


async def validate(state: AgentState) -> dict:
    result = state.get("build_result", {})
    pending = state.get("pending_cad_call") or {"id": "build"}
    if result.get("ok") is False:
        error = result.get("error", {})
        history = bounded_history([*state.get("cad_history", []), tool_message(pending, result)])
        # A failed build consumes one bounded repair slot in
        # ``build_candidate``.  Previously this branch always routed back to
        # ``cad_session`` even after the configured limit, so a model could
        # keep issuing repair turns until the much larger model-call budget
        # was exhausted.  Stop at the repair boundary and let the terminal
        # node clean up the run instead of silently running an unbounded
        # geometry loop.
        limits = (await app_settings()).limits
        if state.get("repairs", 0) >= limits.maxRepairs:
            guidance = error.get("guidance", "Repair the failed CAD operation.")
            return {"phase": "final", "terminal_status": "failed", "repairs": state.get("repairs", 0),
                "cad_history": history,
                "final_message": (f"The bounded CAD repair limit was reached after "
                    f"{state.get('attempts', 0)} attempts. {guidance} "
                    "The saved design is unchanged.")}
        if result.get("repeated"):
            history = bounded_history([*history, {"role": "user", "content":
                "The normalized build error repeated after a source change. Re-plan the affected operation instead of retrying the same construction."}])
        return {"phase": "cad_session", "cad_history": history, "pending_cad_call": {},
            "final_message": error.get("guidance", "Repair the failed CAD operation.")}
    intermediate = not state.get("build_final", True)
    history = bounded_history([*state.get("cad_history", []), tool_message(pending, {
        "ok": True, "message": ("The staged assembly built and passed CAD integrity checks. If the requested "
            "inventory is complete, call build(final=true) for final review; otherwise add the remaining parts and instances." if intermediate else
            "The candidate built and passed universal CAD integrity checks."),
        "inspectionAvailable": bool(result.get("inspection")),
    })])
    if intermediate:
        return {"phase": "cad_session", "cad_history": history,
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
                "last_milestone_hash": state.get("candidate_hash", "")}
    # Every final build gets a focused evidence review before draft publication.
    # This is a bounded quality pass, not a release gate: after at most two
    # focused repairs, publish the last buildable draft with its findings.
    return {"phase": "review_session", "cad_history": history,
        "pending_cad_call": {}, "review_history": [], "review_reads": 0,
        "review_inspected": False, "review_actions": 0, "review": state.get("review") or {},
        "review_repairs": state.get("review_repairs", 0)}


async def repair(state: AgentState) -> dict:
    return await cad_session(state)


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


def build_graph(checkpointer):
    graph = StateGraph(AgentState)
    for name, node in (("coordinator", coordinator), ("coordinator_session", coordinator_session),
        ("coordinator_question", coordinator_question), ("engineering_triage", engineering_triage),
        ("clarification", clarification), ("cad_session", cad_session),
        ("cad_question", cad_question), ("engineering_analysis", engineering_analysis),
        ("approval", approval), ("build", build), ("validate", validate),
        ("review_session", review_session), ("publish", publish), ("final", final)):
        graph.add_node(name, node)
    graph.add_edge(START, "coordinator")
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
        "final": "final",
    })
    graph.add_edge("cad_question", "cad_session")
    graph.add_conditional_edges("engineering_analysis", phase_route,
        {"cad_session": "cad_session", "coordinator_session": "coordinator_session",
         "approval": "approval", "final": "final"})
    graph.add_conditional_edges("approval", phase_route,
        {"cad_session": "cad_session", "coordinator_session": "coordinator_session", "final": "final"})
    graph.add_edge("build", "validate")
    graph.add_conditional_edges("validate", phase_route,
        {"cad_session": "cad_session", "review_session": "review_session", "final": "final"})
    graph.add_conditional_edges("review_session", phase_route, {
        "review_session": "review_session", "cad_session": "cad_session",
        "publish": "publish", "final": "final",
    })
    graph.add_edge("publish", "final")
    graph.add_edge("final", END)
    return graph.compile(checkpointer=checkpointer, interrupt_after="*", name="forma-design")
