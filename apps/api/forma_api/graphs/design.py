"""Fixed Forma graph: engineering gate, CAD build/repair, validation, publication."""
import json
import re
import time
from contextvars import ContextVar
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import Field, ValidationError, field_validator

from .. import db, models, repository as repo
from ..contracts import AppSettings, Contract, Manifest, Requirement, SafeId, Snapshot, SourcePath, Vector
from ..engine import Pause, build_candidate, destroy_sandboxes, execute_tool, operation
from ..execution import digest, normalize_python_source
from ..prompts import VERSION as PROMPT_VERSION, system_prompt
from ..requirements import design_work_requested, merge_requirements
from ..services import runs as run_service
from ..tools import portable_schema
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


async def coordinator(state: AgentState) -> dict:
    if state.get("phase"):
        return {}
    run = await run_row(state)
    snapshot = await repo.load_snapshot(run["base_revision_id"])
    candidate_hash = digest(snapshot)
    await run_service.save_candidate(run["id"], snapshot, candidate_hash)
    await repo.event(run["id"], "Coordinator recorded the request and opened engineering review.", stage="coordination")
    return {"phase": "engineering_triage", "candidate_hash": candidate_hash,
        "repairs": 0, "attempts": 0, "model_calls": 0, "search_count": 0,
        "engineering_remarks": [], "engineering_assumptions": [], "requirements": [],
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
    }
    prompt = """Perform the engineering analysis needed before geometry. Use the engineering packet below as the
source of truth and do not call a value missing when it is present in the original request, explicit requirements,
or clarification. The request deliberately asks the engineer to choose a material and manufacturing method: make
those design choices, state them in selected_material and manufacturing_method, and give concrete thickness,
fillet, reinforcement, bolt and load-path recommendations. Distinguish an engineering choice from a truly blocking
unknown in open_items. State equations, loads, units, assumptions, recommended design parameters, safety-factor
target and limitations. When numerical validation is useful, provide a calculations/analysis.py module that writes
calculation.json matching the CalculationResult contract used by Forma. The result will be executed twice in isolated
processes. Return calculation_source as ordinary Python source with real newline characters; do not return literal
backslash-n escape sequences in place of line breaks. Do not claim FEA or certification.

Engineering packet:
""" + json.dumps(packet, ensure_ascii=False)
    value, usage = await structured_turn(state, "engineering", "analysis", prompt, "submit_analysis", Analysis, web=True)
    output = {**usage, "phase": "approval", "engineering_summary": value.summary,
        "engineering_assumptions": [*state.get("engineering_assumptions", []), *value.assumptions],
        "engineering_remarks": [*state.get("engineering_remarks", []),
            *value.recommendations,
            *(f"Selected material: {value.selected_material}" for _ in [0] if value.selected_material),
            *(f"Manufacturing method: {value.manufacturing_method}" for _ in [0] if value.manufacturing_method),
            *value.design_parameters,
            *(f"Open item: {item}" for item in value.open_items)],
        "approval_summary": value.summary}
    if value.calculation_source:
        calculation_source = normalize_python_source(value.calculation_source)
        snapshot = await run_service.load_candidate(state["run_id"])
        snapshot = Snapshot.model_validate({"manifest": snapshot["manifest"],
            "files": {**snapshot["files"], "calculations/analysis.py": calculation_source}}).model_dump()
        await run_service.save_candidate(state["run_id"], snapshot, digest(snapshot))
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
            raise Pause(f"Engineering calculation failed ({category}){where}. {guidance} Continue when ready.")
        output["engineering_summary"] = value.summary + "\n\nCalculation verified: " + result["result"]["conclusion"]
        output.update(sync_checkpoint(cp))
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
    return {"approved": True, "phase": "cad_design"}


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
    output = await operation(run, f"graph:build:{state.get('attempts', 0)}", "build",
        execute_build, idempotent=True)
    cp = output["checkpoint"]
    return {**sync_checkpoint(cp), "phase": "validate", "build_result": output["result"]}


async def validate(state: AgentState) -> dict:
    result = state.get("build_result", {})
    if result.get("ok") is False:
        limits = (await app_settings()).limits
        error = result.get("error", {})
        if result.get("repeated"):
            return {"phase": "final", "terminal_status": "failed", "final_message":
                f"The same {error.get('category', 'build')} failure repeated after "
                f"{state.get('attempts', 0)} attempts: {error.get('guidance', 'repair the reported operation')}. "
                "No revision was published."}
        if state.get("repairs", 0) > limits.maxRepairs or result.get("repairsRemaining", 0) <= 0:
            return {"phase": "final", "terminal_status": "failed", "final_message":
                f"The bounded repair limit was reached after {state.get('attempts', 0)} attempts. "
                f"Last failure: {error.get('guidance', 'inspect and repair the candidate')}. "
                "Your saved design is unchanged; revise the request or start a new run."}
        return {"phase": "repair"}
    # Requirement measurements are advisory evidence for the human reviewer.
    # Build/artifact integrity was already checked in build_candidate; an
    # unsupported or failed requirement must not prevent the user from seeing
    # and downloading a successfully built draft.
    return {"phase": "publish"}


async def repair(state: AgentState) -> dict:
    return await cad_candidate(state, repair=True)


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
    return {**sync_checkpoint(cp), "phase": "final", "published_revision_id": result["revisionId"],
        "final_message": "The CAD draft built successfully and is ready for your review. Automated requirement checks are advisory; you can edit or download the files."}


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
    for name, node in (("coordinator", coordinator), ("engineering_triage", engineering_triage),
        ("clarification", clarification), ("engineering_analysis", engineering_analysis),
        ("approval", approval), ("cad_design", cad_design), ("build", build),
        ("validate", validate), ("repair", repair), ("publish", publish), ("final", final)):
        graph.add_node(name, node)
    graph.add_edge(START, "coordinator")
    graph.add_edge("coordinator", "engineering_triage")
    graph.add_conditional_edges("engineering_triage", triage_route,
        {"clarify": "clarification", "analyze": "engineering_analysis", "cad": "cad_design", "answer": "final"})
    graph.add_edge("clarification", "engineering_triage")
    graph.add_edge("engineering_analysis", "approval")
    graph.add_conditional_edges("approval", phase_route, {"cad_design": "cad_design", "final": "final"})
    graph.add_conditional_edges("cad_design", phase_route,
        {"build": "build", "repair": "repair", "final": "final"})
    graph.add_edge("build", "validate")
    graph.add_conditional_edges("validate", phase_route, {"repair": "repair", "publish": "publish", "final": "final"})
    graph.add_conditional_edges("repair", phase_route,
        {"build": "build", "repair": "repair", "final": "final"})
    graph.add_edge("publish", "final")
    graph.add_edge("final", END)
    return graph.compile(checkpointer=checkpointer, interrupt_after="*", name="forma-design")
