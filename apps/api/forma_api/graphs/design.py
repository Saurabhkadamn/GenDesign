"""Fixed Forma graph: engineering gate, CAD build/repair, validation, publication."""
import json
import re
import time
from copy import deepcopy
from contextvars import ContextVar
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import Field, ValidationError, field_validator

from .. import db, models, repository as repo
from ..contracts import AppSettings, Contract, Manifest, Requirement, SafeId, Snapshot, SourcePath, Vector, safe_path
from ..engine import Pause, build_candidate, destroy_sandboxes, execute_tool, operation
from ..execution import digest, normalize_python_source
from ..prompts import VERSION as PROMPT_VERSION, system_prompt
from ..requirements import design_work_requested, merge_requirements
from ..services import runs as run_service
from ..tools import model_tools, parse_tool, portable_schema
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
    description: str = Field(min_length=1, max_length=500)
    kind: Literal["dimensions", "center", "solid_count", "through_holes", "corner_radius", "unverified"] = Field(description=(
        "Use dimensions, center, solid_count, through_holes or corner_radius only "
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
    selected_material: str = Field(default="", max_length=200)
    manufacturing_method: str = Field(default="", max_length=200)
    design_parameters: list[str] = Field(default_factory=list, max_length=40)
    open_items: list[str] = Field(default_factory=list, max_length=30)
    calculation_source: str | None = Field(default=None, max_length=100_000)
    requires_user_input: bool = False
    user_question: str = Field(default="", max_length=3000)


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

    async def call(call_messages):
        return await models.turn(config, call_messages, tools, max_tokens=None,
            web_search=web, max_searches=max(0, 2-state.get("search_count", 0)))

    result = await operation(run, f"graph:{node}:{ordinal}", "model", lambda: call(messages))

    def parse(current_result):
        match = next((item for item in current_result["calls"] if item["name"] == tool_name), None)
        if not match:
            # Some providers return a successful HTTP response with an empty
            # assistant message when a large tool call is interrupted. Treat
            # that as a bounded contract correction, rather than ending the
            # graph before the model gets one explicit chance to emit the tool.
            raise ValueError(f"The {role} model did not return the required structured {node} tool call.")
        return contract.model_validate(match["input"])

    calls_used = 1
    try:
        value = parse(result)
    except (ValidationError, ValueError) as exc:
        # An empty tool response is not a successful external operation. Keep
        # its ledger row retryable so a later Continue sends a fresh provider
        # request instead of replaying the same empty result forever.
        if isinstance(exc, ValueError):
            await db.update("run_operations", {"status": "failed", "result": {
                "category": "tool_protocol", "diagnostic": str(exc)}, "updated_at": repo.utcnow()},
                run_id=run["id"], operation_key=f"graph:{node}:{ordinal}")
        if isinstance(exc, ValidationError):
            feedback = "; ".join(
                f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
                for item in exc.errors(include_url=False, include_input=False)[:12]
            )
        else:
            feedback = str(exc)
        if ordinal + 1 >= max_model_calls:
            raise Pause(f"The {role} result violated the required contract: {feedback}") from None
        await repo.event(run["id"],
            f"{role.capitalize()} returned an invalid {node} contract; requesting one bounded correction.",
            kind="validation", stage=role)
        correction = [*messages, {"role": "system", "content":
            "The previous structured result was rejected before execution. Regenerate the complete result and "
            "correct every contract error. For CAD files, return Python source only under parts/, assemblies/ or "
            "calculations/; do not include README or generated artifacts. Contract errors: " + feedback}]
        repair_ordinal = ordinal + 1
        result = await operation(run, f"graph:{node}:contract-repair:{repair_ordinal}", "model",
            lambda: call(correction))
        calls_used = 2
        try:
            value = parse(result)
        except (ValidationError, ValueError) as repair_exc:
            if isinstance(repair_exc, ValueError):
                await db.update("run_operations", {"status": "failed", "result": {
                    "category": "tool_protocol", "diagnostic": str(repair_exc)}, "updated_at": repo.utcnow()},
                    run_id=run["id"], operation_key=f"graph:{node}:contract-repair:{repair_ordinal}")
            if isinstance(repair_exc, ValidationError):
                repaired_feedback = "; ".join(
                    f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
                    for item in repair_exc.errors(include_url=False, include_input=False)[:12]
                )
            else:
                repaired_feedback = str(repair_exc)
            raise Pause(
                f"The {role} model returned an invalid {node} result twice. {repaired_feedback}"
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


def bounded_history(history: list[dict], *, messages: int = 30, characters: int = 500_000,
                    allow_pending: bool = False) -> list[dict]:
    """Keep recent tool context without duplicating a whole workspace in checkpoints."""
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
        return await models.turn(config, messages, tools, max_tokens=None)

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


async def coordinator(state: AgentState) -> dict:
    if state.get("phase"):
        return {}
    run = await run_row(state)
    snapshot = await repo.load_snapshot(run["base_revision_id"])
    candidate_hash = digest(snapshot)
    await run_service.save_candidate(run["id"], snapshot, candidate_hash)
    await repo.event(run["id"], "Coordinator opened an incremental CAD coding session.", stage="coordination")
    return {"phase": "cad_session", "candidate_hash": candidate_hash,
        "repairs": 0, "attempts": 0, "model_calls": 0, "search_count": 0,
        "engineering_remarks": [], "engineering_assumptions": [],
        "requirements": merge_requirements(state["original_request"], []),
        "cad_edits_since_build": 0,
        "cad_history": [{"role": "user", "content": state["original_request"]}],
        "review_history": [], "review": {},
        "review_repairs": 0,
        "started_ns": time.time_ns()}


async def engineering_triage(state: AgentState) -> dict:
    prompt = """Classify this request before CAD. Use route=clarify only for missing inputs that block useful work;
route=analyze for safety, load, material, tolerance, or calculations that require explicit assumptions and approval;
route=cad for a sufficiently clear geometry request; route=answer for conversation with no design work.
Preserve every explicit requirement. Use a supported geometry kind only when all of its numeric fields are present:
dimensions needs a three-value vector, center needs a three-value vector, solid_count needs count, through_holes
needs diameter, count and every plane position, and corner_radius needs radius and count. Set through_holes axis=Z
for holes normal to the XY plane and axis=Y for holes normal to the XZ frame plane. Put unsupported or incomplete
checks in kind=unverified without inventing values. In particular, an M10 bolt size does not specify a hole
diameter, so record the frame bolt pattern as unverified unless a clearance diameter is explicitly supplied.
Web search is available only when current external engineering facts are necessary; prefer the request and deterministic calculation."""
    value, usage = await structured_turn(state, "engineering", "triage", prompt, "submit_triage", Triage, web=True)
    requirements = merge_requirements(state["original_request"], normalize_triage_requirements(value.requirements))
    route = value.route
    if design_work_requested(state["original_request"]) and route == "answer":
        route = "cad"
    await repo.event(state["run_id"], f"Engineering review routed the request to {route}.", stage="engineering")
    return {**usage, "phase": "engineering_triage", "route": route, "question": value.question,
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
        "clarifiedRequest": state.get("clarified_request", ""),
        "explicitRequirements": state.get("requirements", []),
        "triageAssumptions": state.get("engineering_assumptions", []),
        "triageRemarks": state.get("engineering_remarks", []),
        "cadRequest": state.get("engineering_request", ""),
    }
    prompt = """Perform the engineering analysis needed before geometry. Use the engineering packet below as the
source of truth and do not call a value missing when it is present in the original request, explicit requirements,
or clarification. The request deliberately asks the engineer to choose a material and manufacturing method: make
those design choices, state them in selected_material and manufacturing_method, and give concrete thickness,
fillet, reinforcement, bolt and load-path recommendations. Distinguish an engineering choice from a truly blocking
unknown in open_items. The CAD agent's request is the immediate task. State equations, loads, units, assumptions, recommended design parameters, safety-factor
target and limitations. When numerical validation is useful, provide a calculations/analysis.py module that writes
calculation.json matching the CalculationResult contract used by Forma. The result will be executed twice in isolated
processes. Return calculation_source as ordinary Python source with real newline characters; do not return literal
backslash-n escape sequences in place of line breaks. Set requires_user_input only when a missing user choice prevents
useful geometry; visible engineering assumptions and limitations do not require an approval pause. Do not claim FEA or certification.

Engineering packet:
""" + json.dumps(packet, ensure_ascii=False)
    value, usage = await structured_turn(state, "engineering", "analysis", prompt, "submit_analysis", Analysis, web=True)
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
    if value.calculation_source:
        calculation_source = normalize_python_source(value.calculation_source)
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
    return {"approved": True, "phase": "cad_session"}


def sandbox_name(run_id: str, suffix: str) -> str:
    return f"forma-{UUID(run_id).hex}-{suffix}-{uuid4().hex[:8]}"


def checkpoint_view(state: AgentState, snapshot: dict) -> dict:
    return {"snapshot": snapshot, "role": "cad", "requirements": state.get("requirements", []),
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
    payload = json.loads(json.dumps(manifest))
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


async def cad_session(state: AgentState) -> dict:
    """Let the CAD model choose one incremental workspace/tool action."""
    snapshot = await run_service.load_candidate(state["run_id"])
    history = state.get("cad_history") or [{
        "role": "user", "content": state.get("clarified_request") or state["original_request"]
    }]
    context = {
        "request": state.get("clarified_request") or state["original_request"],
        "engineeringSummary": state.get("engineering_summary", ""),
        "engineeringRemarks": state.get("engineering_remarks", []),
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
    edits_since_build = state.get("cad_edits_since_build", 0)
    if edits_since_build >= MAX_CAD_EDITS_WITHOUT_BUILD:
        # Keep the graph progressing even when a model repeatedly proposes
        # patches. A build is the only useful next action after this bound.
        tools = [item for item in tools if item["function"]["name"] in
                 {"build", "read_file", "search_files", "inspect_geometry"}]
        context["buildRequired"] = True
        context["editsSinceBuild"] = edits_since_build
    call, history, usage = await agent_tool_turn(
        state, model_role="cad", prompt_role="cad", node="cad-session",
        context=context, history=history, tools=tools,
    )
    if not call:
        history = bounded_history([*history, {"role": "user", "content":
            "Continue with exactly one tool action. Read or patch a focused target, request engineering or user input, or build the current candidate."}])
        return {**usage, "phase": "cad_session", "cad_history": history}
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
        history = bounded_history([*history, tool_message(call, {
            "ok": False, "category": "tool_contract", "message": str(exc)[:3000]
        })])
        return {**usage, "phase": "cad_session", "cad_history": history}

    name = call["name"]
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
                "cad_history": next_history}
        return {**usage, "phase": "cad_session", "cad_invalid_tool_attempts": 0,
            "cad_history": bounded_history([*history, tool_message(call, {
                "ok": True, "path": value["path"], "content": content})])}
    if name == "search_files":
        matches = [{"path": path, "line": index + 1, "text": line[:300]}
            for path, source in snapshot["files"].items()
            for index, line in enumerate(source.splitlines())
            if value["query"] in line][:100]
        return {**usage, "phase": "cad_session", "cad_history": bounded_history([
            *history, tool_message(call, {"matches": matches})])}
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
                value["manifest"] or snapshot["manifest"])
            hierarchy_normalized = hierarchy_pre_normalized or hierarchy_normalized
            candidate = Snapshot.model_validate({
                "manifest": manifest, "files": files,
            }).model_dump()
        except (ValidationError, ValueError) as exc:
            result = {"ok": False, "category": "workspace_contract", "message": str(exc)[:5000]}
            return {**usage, "phase": "cad_session", "cad_history": bounded_history([
                *history, tool_message(call, result)])}
        candidate_hash = digest(candidate)
        await run_service.save_candidate(state["run_id"], candidate, candidate_hash)
        await repo.event(state["run_id"], "CAD updated a focused part of the code workspace.", stage="cad")
        result = {"ok": True, "candidateHash": candidate_hash,
            "changedFiles": sorted(value["files"]), "deletedFiles": value.get("deletePaths", []),
            "hierarchyNormalized": hierarchy_normalized,
            "hierarchyNote": ("Top-level or invalid parent sentinels were normalized to null; parentId must name "
                "another instance id." if hierarchy_normalized else "")}
        return {**usage, "phase": "cad_session", "candidate_hash": candidate_hash,
            "cad_invalid_tool_attempts": 0,
            "cad_edits_since_build": edits_since_build + 1,
            "cad_history": bounded_history([*history, tool_message(call, result)]),
            "validation": {}, "review": {}, "build_result": {}}
    if name == "request_engineering":
        if state.get("engineering_summary") and state.get("engineering_candidate_hash") == digest(snapshot):
            result = {"ok": False, "category": "unchanged_engineering_request",
                "message": ("Engineering already analyzed this unchanged workspace. Use the returned parameters, "
                    "edit geometry, or build before requesting another calculation.")}
            return {**usage, "phase": "cad_session", "cad_history": bounded_history([
                *history, tool_message(call, result)])}
        await repo.event(state["run_id"], "CAD requested an engineering calculation or parameter study.", stage="engineering")
        return {**usage, "phase": "engineering_analysis", "engineering_request": value["task"],
            "pending_cad_call": call, "cad_history": history}
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
            "pending_cad_call": call, "cad_history": history}
    if name == "build":
        if not snapshot["manifest"].get("components") or not snapshot["manifest"].get("rootComponentId"):
            history = bounded_history([*history, tool_message(call, {
                "ok": False, "category": "empty_workspace",
                "message": "Create at least one component and choose a root component before building.",
            })])
            return {**usage, "phase": "cad_session", "cad_history": history}
        if (state.get("review", {}).get("action") == "repair"
                and state.get("reviewed_candidate_hash") == digest(snapshot)):
            history = bounded_history([*history, tool_message(call, {
                "ok": False, "category": "unchanged_reviewed_candidate",
                "message": "The independent review requested a source change. Edit the candidate before rebuilding.",
            })])
            return {**usage, "phase": "cad_session", "cad_history": history}
        # Execute the build transition in the same graph step as the explicit
        # CAD build action.  Hosted LangGraph interrupts after each node; in
        # practice that boundary could lose the phase update and schedule
        # another CAD turn without ever entering the build node.
        return await build({**state, **usage, "phase": "build",
            "pending_cad_call": call, "cad_history": history,
            "cad_edits_since_build": 0})
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
    })])
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
        candidate = Snapshot.model_validate({"manifest": manifest,
            "files": {**snapshot["files"], **value.files}}).model_dump()
        if hierarchy_changed:
            await repo.event(state["run_id"],
                "CAD assembly hierarchy had invalid parent references; flattened those edges for a buildable draft.",
                kind="validation", stage="cad")
    except ValidationError as exc:
        feedback = "; ".join(
            f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
            for item in exc.errors(include_url=False, include_input=False)[:12]
        )
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
    async def execute_build():
        result = await build_candidate(run, cp, limits, f"graph:build:{state.get('attempts', 0)}")
        return {"result": result, "checkpoint": cp}
    try:
        output = await operation(run, f"graph:build:{state.get('attempts', 0)}", "build",
            execute_build, idempotent=True)
    except Pause as exc:
        if "has not changed" not in str(exc):
            raise
        pending = state.get("pending_cad_call") or {"id": "build"}
        history = bounded_history([*state.get("cad_history", []), tool_message(pending, {
            "ok": False, "category": "unchanged_failed_candidate", "message": str(exc),
        })])
        return {"phase": "cad_session", "cad_history": history, "pending_cad_call": {}}
    cp = output["checkpoint"]
    return {**sync_checkpoint(cp), "phase": "validate", "build_result": output["result"]}


async def validate(state: AgentState) -> dict:
    result = state.get("build_result", {})
    pending = state.get("pending_cad_call") or {"id": "build"}
    if result.get("ok") is False:
        error = result.get("error", {})
        history = bounded_history([*state.get("cad_history", []), tool_message(pending, result)])
        if result.get("repeated"):
            history = bounded_history([*history, {"role": "user", "content":
                "The normalized build error repeated after a source change. Re-plan the affected operation instead of retrying the same construction."}])
        return {"phase": "cad_session", "cad_history": history, "pending_cad_call": {},
            "review": {}, "final_message": error.get("guidance", "Repair the failed CAD operation.")}
    history = bounded_history([*state.get("cad_history", []), tool_message(pending, {
        "ok": True, "message": "The candidate built and passed universal CAD integrity checks.",
        "inspectionAvailable": bool(result.get("inspection")),
    })])
    return {"phase": "review_session", "cad_history": history,
        "pending_cad_call": {}, "review_history": [], "review_reads": 0,
        "review_inspected": False}


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
        validation = deepcopy(validation)
        validation.setdefault("report", {})["review"] = preflight
        fingerprint = digest({"action": preflight["action"], "findings": preflight["findings"]})
        await repo.event(state["run_id"], "Independent CAD preflight found an incomplete assembly instance inventory.",
            kind="validation", stage="review")
        repair_context = {"message": "Independent review found an actionable evidence gap. Modify the manifest before rebuilding.",
            "summary": preflight["summary"], "findings": preflight["findings"]}
        return {"phase": "cad_session", "review": preflight,
            "review_fingerprint": fingerprint, "reviewed_candidate_hash": digest(snapshot),
            "review_history": [], "cad_history": bounded_history([*state.get("cad_history", []),
                {"role": "user", "content": json.dumps(repair_context, ensure_ascii=False)}]),
            "validation": validation}
    history = state.get("review_history") or [{"role": "user", "content":
        "Independently review this built CAD candidate against the original request. Use tools for evidence, then submit the review."}]
    context = {
        "originalRequest": state["original_request"],
        "clarifiedRequest": state.get("clarified_request", ""),
        "engineeringSummary": state.get("engineering_summary", ""),
        "engineeringRemarks": state.get("engineering_remarks", []),
        "manifest": snapshot["manifest"],
        "sourceFiles": sorted(snapshot["files"]),
        "buildReport": validation.get("report", validation),
    }
    allowed_review_tools = set()
    if state.get("review_reads", 0) < 3:
        allowed_review_tools.add("read_file")
    if not state.get("review_inspected", False):
        allowed_review_tools.add("inspect_geometry")
    tools = [item for item in model_tools("cad")
             if item["function"]["name"] in allowed_review_tools]
    tools.append(submission_tool("submit_review", "Submit evidence-backed findings for this candidate.", ReviewResult))
    call, history, usage = await agent_tool_turn(
        state, model_role="cad", prompt_role="reviewer", node="review-session",
        context=context, history=history, tools=tools,
    )
    if not call:
        return {**usage, "phase": "review_session", "review_history": bounded_history([
            *history, {"role": "user", "content": "Use read_file or inspect_geometry, then call submit_review."}
        ])}
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
            "review_history": bounded_history([*history, tool_message(call, result)])}
    if name == "inspect_geometry":
        return {**usage, "phase": "review_session", "review_inspected": True,
            "review_history": bounded_history([
                *history, tool_message(call, validation.get("report", validation))])}
    if name != "submit_review":
        return {**usage, "phase": "review_session", "review_history": bounded_history([
            *history, tool_message(call, {"ok": False, "message": "Reviewer tools are read-only."})])}
    try:
        review = ReviewResult.model_validate(call["input"]).model_dump()
    except ValidationError as exc:
        return {**usage, "phase": "review_session", "review_history": bounded_history([
            *history, tool_message(call, {"ok": False, "category": "review_contract",
                "message": str(exc)[:5000]})])}
    if review["action"] == "repair" and state.get("review_repairs", 0) >= 1:
        review["action"] = "publish"
        review["summary"] += (" The bounded reviewer repair cycle is complete; remaining findings are published "
            "with this draft for user-directed editing.")
    validation = deepcopy(validation)
    validation.setdefault("report", {})["review"] = review
    fingerprint = digest({"action": review["action"], "findings": review["findings"]})
    await repo.event(state["run_id"],
        f"Independent CAD review completed with {len(review['findings'])} findings.",
        kind="validation", stage="review")
    if review["action"] == "repair":
        review_repairs = state.get("review_repairs", 0) + 1
        repair_context = {
            "message": "Independent review found actionable geometry defects. Modify the source before rebuilding.",
            "summary": review["summary"], "findings": review["findings"],
        }
        cad_history = bounded_history([*state.get("cad_history", []), {
            "role": "user", "content": json.dumps(repair_context, ensure_ascii=False),
        }])
        return {**usage, "phase": "cad_session", "review": review,
            "review_fingerprint": fingerprint, "reviewed_candidate_hash": digest(snapshot),
            "review_history": history, "cad_history": cad_history, "validation": validation,
            "review_repairs": review_repairs}
    return {**usage, "phase": "publish", "review": review,
        "review_fingerprint": fingerprint, "reviewed_candidate_hash": digest(snapshot),
        "review_history": history, "validation": validation}


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
            "input": {"summary": state.get("candidate_summary") or "Verified CAD design"}},
            settings_value, worker())
    result = await operation(run, "graph:publish", "publish_revision", publish_candidate, idempotent=True)
    await destroy_sandboxes(cp)
    review = state.get("review") or {}
    message = "The CAD draft built successfully and is ready for your review."
    if review.get("summary"):
        message += " Independent review: " + review["summary"]
    message += " You can continue editing or download the files; engineering and physical validation remain your responsibility."
    return {**sync_checkpoint(cp), "phase": "final", "published_revision_id": result["revisionId"],
        "final_message": message}


async def final(state: AgentState) -> dict:
    run = await run_row(state)
    await run_service.finish(run, worker(), state.get("terminal_status", "succeeded"),
        state.get("final_message") or "Completed.")
    return {"phase": "done"}


def triage_route(state: AgentState) -> str:
    return state.get("route", "cad")


def phase_route(state: AgentState) -> str:
    return state.get("phase", "final")


def build_graph(checkpointer):
    graph = StateGraph(AgentState)
    for name, node in (("coordinator", coordinator), ("cad_session", cad_session),
        ("cad_question", cad_question), ("engineering_analysis", engineering_analysis),
        ("approval", approval), ("build", build), ("validate", validate),
        ("review_session", review_session), ("publish", publish), ("final", final)):
        graph.add_node(name, node)
    graph.add_edge(START, "coordinator")
    graph.add_edge("coordinator", "cad_session")
    graph.add_conditional_edges("cad_session", phase_route, {
        "cad_session": "cad_session", "cad_question": "cad_question",
        "engineering_analysis": "engineering_analysis", "build": "build", "validate": "validate",
        "final": "final",
    })
    graph.add_edge("cad_question", "cad_session")
    graph.add_conditional_edges("engineering_analysis", phase_route,
        {"cad_session": "cad_session", "approval": "approval", "final": "final"})
    graph.add_conditional_edges("approval", phase_route, {"cad_session": "cad_session", "final": "final"})
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
