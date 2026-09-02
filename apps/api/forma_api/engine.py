"""Checkpointed coordinator → specialist loop with an external-operation ledger."""
import asyncio
import json
import time
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from fastapi import HTTPException
from pydantic import ValidationError

from . import db, models, repository as repo, tracing
from .config import settings
from .contracts import AppSettings, CalculationResult, Snapshot, TERMINAL
from .execution import ExecutionFailure, build_error, digest, executor, identity
from .prompts import VERSION as PROMPT_VERSION, system_prompt
from .requirements import design_work_requested, merge_requirements
from .tools import model_tools, parse_tool


class Pause(Exception):
    pass


async def operation(run, key, kind, callback, *, idempotent=False):
    old = await db.one("run_operations", {"run_id": f"eq.{run['id']}", "operation_key": f"eq.{key}"}, required=False)
    if old and old["status"] == "complete":
        return old["result"]
    # A definitive provider throttle did not execute a model request.  An
    # explicit Continue may retry it; timeouts and unknown failures remain
    # non-repeatable because the provider may have accepted the request.
    retryable_model_failure = (
        old and kind == "model" and old["status"] == "failed"
        and (old.get("result") or {}).get("category") in {"rate_limit", "overloaded", "tool_protocol"}
    )
    if old and not idempotent:
        if old["status"] == "started":
            await db.update("run_operations", {"status": "ambiguous"}, run_id=run["id"], operation_key=key)
        if not retryable_model_failure:
            raise Pause("The previous external request has an uncertain or failed outcome. It was not repeated automatically. Review the connection and Continue when ready.")
        await db.update("run_operations", {"status": "started", "result": None, "updated_at": repo.utcnow()},
                        run_id=run["id"], operation_key=key)
    if not old:
        await db.insert("run_operations", {"run_id": run["id"], "operation_key": key, "kind": kind, "status": "started"})
    try:
        result = await callback()
    except models.ModelFailure as exc:
        await db.update("run_operations", {"status": "ambiguous" if exc.category in ("timeout", "connection") else "failed",
            "result": {"category": exc.category, "diagnostic": tracing.sanitize(exc.diagnostic)}}, run_id=run["id"], operation_key=key)
        raise Pause(str(exc)) from None
    await db.update("run_operations", {"status": "complete", "result": result, "updated_at": repo.utcnow()}, run_id=run["id"], operation_key=key)
    return result


async def persist(run, cp, worker, version):
    if len(json.dumps(cp)) > 4_000_000:
        raise Pause("The private checkpoint reached its size limit. Start a smaller request.")
    return await db.rpc("checkpoint_run_v2", {"p_run": run["id"], "p_worker": worker, "p_version": version,
        "p_checkpoint": cp, "p_model_calls": cp["modelCalls"]})


def tool_result(cp, call, result, role=None):
    cp["history"][role or cp["role"]].append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})


async def destroy_sandboxes(cp):
    from .maintenance import release_sandbox
    for key in ("sandbox", "validator"):
        name = cp.pop(key, None)
        if name:
            try:
                await release_sandbox(name)
            except Exception:
                # Vercel also enforces the maximum lifetime; keep the reservation if cleanup failed.
                pass
    cp.pop("sandboxReady", None)


async def finish_run(run, cp, worker, status, message):
    await destroy_sandboxes(cp)
    await db.rpc("finish_run_v2", {"p_run": run["id"], "p_worker": worker, "p_status": status, "p_message": message, "p_checkpoint": cp})
    await tracing.record(run, f"run", "Forma design run", cp["startedNs"], inputs={"request": run["message"]},
        outputs={"status": status, "message": message, "revisionId": cp.get("published")},
        attributes={"repair.count": cp["repairs"], "model.calls": cp["modelCalls"]}, error=status in ("failed", "paused"))
    return "done"


async def ensure_sandbox(run, cp, limits):
    if cp.get("sandboxReady"):
        return
    name = cp["sandbox"]
    started = time.time_ns()

    async def create():
        if settings().executor == "vercel" and settings().resource_budgets_enabled:
            reserved = await db.rpc("reserve_execution", {"p_run": run["id"], "p_key": name,
                "p_seconds": 1800, "p_budget": limits.monthlySandboxSeconds})
            if not reserved:
                raise Pause("The monthly CAD execution budget is exhausted. Review the limit in Settings.")
        return {"name": await executor().create(name)}

    await operation(run, f"sandbox:{name}", "sandbox_prepare", create, idempotent=True)
    cp["sandboxReady"] = True
    await tracing.record(run, f"prepare:{name}", "Sandbox preparation", started, outputs={"sandbox": name})
    await repo.event(run["id"], "Build environment ready for this run.", stage="preparation", elapsed_ms=(time.time_ns()-started)/1e6)


async def model_turn(run, cp, limits):
    config = await models.configuration(cp["role"])
    history = cp["history"][cp["role"]]
    context = {"manifest": cp["snapshot"]["manifest"], "files": list(cp["snapshot"]["files"]),
               "requirements": cp["requirements"], "selectedIds": run["selected_ids"],
               "verified": cp.get("validated", {}).get("report"), "remainingRepairs": limits.maxRepairs-cp["repairs"]}
    messages = [{"role": "system", "content": system_prompt(cp["role"])},
                {"role": "system", "content": "Current private workspace context: " + json.dumps(context)}, *history]
    sequence = cp["sequence"]
    started = time.time_ns()
    await repo.event(run["id"], f"{cp['role'].capitalize()} is preparing the next action.", stage="model")

    async def call():
        return await models.turn(config, messages, model_tools(cp["role"]), max_tokens=None)

    try:
        result = await operation(run, f"model:{sequence}", "model", call)
    except Pause:
        recorded = await db.one("run_operations", {"run_id": f"eq.{run['id']}", "operation_key": f"eq.model:{sequence}"}, required=False)
        await tracing.record(run, f"model:{sequence}", f"{cp['role']} model call", started,
            inputs={"messages": messages, "tools": model_tools(cp["role"])}, outputs=(recorded or {}).get("result"),
            attributes={"span.type": "LLM", "model": config["model_id"], "provider": "OpenRouter", "prompt.version": PROMPT_VERSION}, error=True)
        raise
    cp["history"][cp["role"]].append(result["message"])
    cp["pending"] = result["calls"]
    cp["modelCalls"] += 1
    await tracing.record(run, f"model:{sequence}", f"{cp['role']} model call", started,
        inputs={"messages": messages, "tools": model_tools(cp["role"])}, outputs=result,
        attributes={"span.type": "LLM", "model": config["model_id"], "provider": "OpenRouter", "prompt.version": PROMPT_VERSION,
        "input.tokens": result["inputTokens"], "output.tokens": result["outputTokens"], "cost": result.get("cost")})
    await db.insert("generations", {"id": str(uuid5(NAMESPACE_URL, f"{run['id']}:{sequence}")), "run_id": run["id"],
        "ordinal": sequence, "role": cp["role"], "model_id": config["model_id"], "config_version": config["version"],
        "prompt_version": PROMPT_VERSION, "status": "complete", "output": result["message"],
        "input_tokens": result["inputTokens"], "output_tokens": result["outputTokens"]}, conflict="run_id,ordinal")
    if not result["calls"]:
        if cp["role"] == "coordinator" and not cp.get("changed"):
            if cp.get("designRequested", design_work_requested(run["message"])):
                cp["protocolRepairs"] += 1
                if cp["protocolRepairs"] > 2:
                    raise Pause("The coordinator did not start the required design workflow. Choose a model with reliable tool support and Continue.")
                history.append({"role": "user", "content": "This is an actionable design request. Delegate it to the appropriate specialist or ask one focused question; do not finish without a tool action."})
            else:
                cp["answer"] = result["message"]["content"] or "What would you like to design?"
        else:
            cp["protocolRepairs"] += 1
            if cp["protocolRepairs"] > 1:
                raise Pause("The model stopped without completing its tool cycle. Choose a model with reliable tool support and Continue.")
            history.append({"role": "user", "content": "Use a tool action. A CAD task must build and verify before finish; the coordinator must publish changed geometry before finish."})


async def build_candidate(run, cp, limits, key):
    snapshot = Snapshot.model_validate(cp["snapshot"]).model_dump()
    expected = identity(snapshot, cp["requirements"])
    if cp.get("validated", {}).get("identity") == expected:
        return cp["validated"]["report"]
    if cp.get("lastFailedCandidate") == expected:
        raise Pause("The candidate has not changed since its failed build. No identical build was repeated; edit the source before continuing.")
    if cp["repairs"] > limits.maxRepairs:
        raise Pause(f"Stopped after {cp['attempts']} build attempts. The repair limit was reached; your saved design is unchanged.")
    await ensure_sandbox(run, cp, limits)
    cp["attempts"] += 1
    await repo.event(run["id"], f"Building candidate · attempt {cp['attempts']}.", stage="execution", attempt=cp["attempts"])
    encode = lambda x: json.dumps(x).encode()
    metadata = {"manifest.json": encode(snapshot["manifest"]), "requirements.json": encode(cp["requirements"]), "identity.json": encode(expected)}
    files = {**metadata, **{path: content.encode() for path, content in snapshot["files"].items() if not path.startswith("calculations/")}}
    started = time.time_ns()
    await executor().stage(cp["sandbox"], files)
    receipt = await executor().execute(cp["sandbox"], "build", limits.commandTimeoutSeconds)
    if receipt.get("identity") != expected:
        raise ExecutionFailure("Build identity mismatch; stale output rejected")
    await tracing.record(run, f"{key}:execution", "CAD execution", started, inputs={"snapshot": snapshot, "requirements": cp["requirements"]},
        outputs=receipt, attributes={"attempt": cp["attempts"], "candidate.hash": expected["candidate"], "runtime.hash": expected["runtime"]}, error=bool(receipt["exitCode"]))
    if not receipt["clean"] or receipt["timedOut"]:
        await destroy_sandboxes(cp)
        raise Pause("The CAD process timed out or could not be cleaned up. Its environment was discarded. Continue to create a fresh one.")
    if receipt["exitCode"]:
        return await reject_candidate(run, cp, expected, build_error(receipt, "build"), limits)
    step_files, total = {}, 0
    for component in snapshot["manifest"]["components"]:
        name = component["id"] + ".step"
        content = await executor().read(cp["sandbox"], name)
        total += len(content)
        if total > 120 * 1024 * 1024:
            raise Pause("The generated STEP files exceed the transfer limit.")
        step_files[name] = content
    # One fresh validator for this candidate; no Python source or builder memory crosses over.
    validator = cp["validator"]
    started = time.time_ns()
    if settings().executor == "vercel" and settings().resource_budgets_enabled:
        reserved = await db.rpc("reserve_execution", {"p_run": run["id"], "p_key": validator,
            "p_seconds": limits.commandTimeoutSeconds + 60, "p_budget": limits.monthlySandboxSeconds})
        if not reserved:
            raise Pause("The monthly validation budget is exhausted. The candidate was not published.")
    await executor().create(validator, lifetime=limits.commandTimeoutSeconds + 60)
    try:
        await executor().stage(validator, {**metadata, **step_files})
        receipt = await executor().execute(validator, "validate", limits.commandTimeoutSeconds)
        if receipt.get("identity") != expected or not receipt["clean"]:
            raise ExecutionFailure("Validator identity or cleanup check failed")
        if receipt["exitCode"]:
            return await reject_candidate(run, cp, expected, build_error(receipt, "validation"), limits)
        report = json.loads(await executor().read(validator, "report.json"))
        if report.get("identity") != expected:
            raise ExecutionFailure("Validation identity mismatch")
        # Requirement measurements are advisory evidence for the human reviewer.
        # The build and artifact identity/format checks above remain mandatory,
        # but an unsupported or failed measurement must not hide a usable draft.
        artifacts = []
        for artifact in report["artifacts"]:
            content = await executor().read(validator, artifact["name"])
            if len(content) != artifact["bytes"] or len(content) > limits.maxArtifactBytes:
                raise ExecutionFailure("Artifact size validation failed")
            storage_path = f"{run['owner_id']}/{run['project_id']}/{run['id']}/{expected['candidate']}/{artifact['name']}"
            await db.insert("artifact_staging", {"storage_path": storage_path, "run_id": run["id"],
                "project_id": run["project_id"], "bytes": len(content)}, conflict="storage_path")
            await db.storage(f"object/cad-private/{db.object_path(storage_path)}", "POST", content=content,
                content_type="model/gltf-binary" if artifact["kind"] == "glb" else "application/step")
            artifacts.append({**artifact, "storagePath": storage_path})
        cp["validated"] = {"identity": expected, "report": report, "artifacts": artifacts}
        cp.pop("lastFailedCandidate", None)
        await repo.event(run["id"], f"Attempt {cp['attempts']} passed CAD integrity checks. Draft ready for human review; {sum(r['status']=='passed' for r in report.get('requirements', []))} automated requirement checks passed.",
            kind="validation", stage="validation", attempt=cp["attempts"], elapsed_ms=(time.time_ns()-started)/1e6)
        return report
    finally:
        from .maintenance import release_sandbox
        await release_sandbox(validator)
        cp.pop("validator", None)
        await tracing.record(run, f"{key}:validation", "Independent STEP validation", started,
            outputs=cp.get("validated", {}).get("report", {"passed": False}), attributes={"attempt": cp["attempts"], "candidate.hash": expected["candidate"]})


async def reject_candidate(run, cp, expected, error, limits):
    cp["lastFailedCandidate"] = expected
    cp["repairs"] += 1
    repeated = error["fingerprint"] == cp.get("lastFailure")
    cp["lastFailure"] = error["fingerprint"]
    await repo.event(run["id"], f"Attempt {cp['attempts']} failed: {error['guidance']}", kind="validation", stage=error["stage"], attempt=cp["attempts"])
    if repeated or cp["repairs"] > limits.maxRepairs:
        cp["stopAfterTool"] = "The same build failure repeated." if repeated else "The bounded repair limit was reached."
    return {"ok": False, "error": error, "attempt": cp["attempts"], "repeated": repeated,
        "repairsRemaining": max(0, limits.maxRepairs-cp["repairs"])}


async def execute_tool(run, cp, call, app_settings, worker):
    parsed = parse_tool(cp["role"], call["name"], call["input"])
    name, value, limits = call["name"], parsed.model_dump(), app_settings.limits
    snapshot = cp["snapshot"]
    if name == "read_file":
        return {"content": snapshot["files"].get(value["path"])}
    if name == "search_files":
        return {"matches": [{"path": p, "line": i+1, "text": line[:300]} for p, code in snapshot["files"].items() for i, line in enumerate(code.splitlines()) if value["query"] in line][:100]}
    if name == "apply_changes":
        if cp["role"] == "engineering" and (value["manifest"] is not None or any(not p.startswith("calculations/") for p in value["files"])):
            raise ValueError("Engineering can edit only calculations/ files and cannot modify the manifest.")
        if cp["role"] == "cad" and any(p.startswith("calculations/") for p in value["files"]):
            raise ValueError("Delegate calculations to the engineering agent.")
        candidate = Snapshot.model_validate({"manifest": value["manifest"] or snapshot["manifest"], "files": {**snapshot["files"], **value["files"]}}).model_dump()
        if not app_settings.surfacingEnabled and any(c["kind"] == "surface" for c in candidate["manifest"]["components"]):
            raise ValueError("Surface modeling is disabled by the administrator.")
        cp["snapshot"] = candidate
        if cp["role"] == "cad" and candidate != snapshot:
            cp["changed"] = True
            cp.pop("validated", None)
        return {"ok": True, "candidateHash": digest(candidate)}
    if name == "inspect_geometry":
        return cp.get("validated", {"verified": False, "message": "Build the current candidate first."})
    if name == "delegate":
        if value["role"] == "engineering" and not app_settings.engineeringEnabled:
            raise ValueError("Engineering calculations are disabled.")
        cp["delegation"] = {"call": call, "remaining": cp["pending"]}
        cp["role"], cp["pending"] = value["role"], []
        cp["history"][cp["role"]] = [{"role": "user", "content": value["task"]}]
        if cp["role"] == "cad":
            cp["requirements"] = merge_requirements(run["message"], value["requirements"])
            cp["sandbox"] = cp.get("sandbox") or f"forma-{UUID(run['id']).hex}-{uuid4().hex[:12]}"
        return None
    if name == "build":
        return await build_candidate(run, cp, limits, f"tool:{cp['sequence']}")
    if name == "calculate":
        if not value["path"].startswith("calculations/"):
            raise ValueError("Only calculations/ modules may be calculated.")
        box = cp["sandbox"]
        await ensure_sandbox(run, cp, limits)
        expected = identity(snapshot, [])
        await executor().stage(box, {"identity.json": json.dumps(expected).encode(), **{p: s.encode() for p, s in snapshot["files"].items() if p.startswith("calculations/")}})
        receipt = await executor().execute(box, "calculate", limits.commandTimeoutSeconds, value["path"])
        if receipt["exitCode"] or not receipt["clean"] or receipt["identity"] != expected:
            return {"ok": False, "error": build_error(receipt, "calculation")}
        result = json.loads(await executor().read(box, "calculation.json"))
        calculation = CalculationResult.model_validate(result["result"]).model_dump()
        cid = str(uuid5(NAMESPACE_URL, f"{run['id']}:calculation:{cp['sequence']}"))
        await db.insert("calculations", {"id": cid, "run_id": run["id"], "project_id": run["project_id"], "revision_id": run["base_revision_id"], "result": calculation, "reproducible": True}, conflict="id")
        await db.insert("calculation_sources", {"calculation_id": cid, "source": snapshot["files"][value["path"]], "runtime_version": settings().runtime_version}, conflict="calculation_id")
        cp["calculated"] = True
        return {"ok": True, "result": calculation, "reproducible": True}
    if name == "restore_revision":
        revision = await db.one("revisions", {"id": f"eq.{repo.identifier(value['revisionId'])}", "project_id": f"eq.{run['project_id']}"})
        cp["snapshot"] = await repo.load_snapshot(revision["id"])
        cp["restored"] = revision["id"]
        cp["changed"] = True
        cp.pop("validated", None)
        return {"loaded": True, "mustRebuild": True}
    if name == "publish_revision":
        validated = cp.get("validated")
        if not validated or validated["identity"] != identity(snapshot, cp["requirements"]):
            raise ValueError("Only the exact independently validated candidate can be published.")
        # Publication here means publishing a successfully built draft. Human
        # review owns design acceptance; automated requirement measurements are
        # advisory and may be empty or unverified for complex assemblies.
        existing = await db.rest("artifacts", params={"select": "bytes"})
        if sum(a["bytes"] for a in existing) + sum(a["bytes"] for a in validated["artifacts"]) > limits.storageBudgetBytes:
            raise Pause("The configured artifact storage budget is exhausted.")
        revision_id = str(uuid5(NAMESPACE_URL, f"{run['id']}:revision"))
        cp["published"] = await db.rpc("publish_revision_v2", {"p_run": run["id"], "p_worker": worker, "p_revision": revision_id,
            "p_summary": value["summary"], "p_manifest": snapshot["manifest"], "p_snapshot": snapshot,
            "p_artifacts": validated["artifacts"], "p_report": validated["report"], "p_restored": cp.get("restored")})
        await repo.event(run["id"], "CAD draft and downloadable artifacts published for human review.", stage="publication")
        return {"revisionId": cp["published"], "requirements": validated["report"]["requirements"], "allRequirementsVerified": validated["report"]["allRequirementsVerified"]}
    if name == "ask_user":
        cp["question"] = value["question"]
        return {"waitingForUser": True}
    if name == "finish":
        if cp["role"] == "cad" and (not cp.get("validated") or cp["validated"]["identity"] != identity(snapshot, cp["requirements"])):
            raise ValueError("The CAD agent cannot finish until the candidate builds and independently validates.")
        if cp["role"] == "engineering" and not cp.get("calculated"):
            raise ValueError("Execute and verify the calculation before finishing.")
        if cp["role"] != "coordinator":
            await destroy_sandboxes(cp)
            delegation = cp.pop("delegation")
            cp["role"], cp["pending"] = "coordinator", delegation["remaining"]
            tool_result(cp, delegation["call"], {"message": value["message"], "verified": cp.get("validated", {}).get("report")})
            return None
        if cp.get("changed") and not cp.get("published"):
            raise ValueError("Publish the validated changed geometry before finishing.")
        cp["answer"] = value["message"]
        return {"finished": True}
    raise ValueError("Unknown tool")


async def tick(run_id, worker):
    run = await db.one("runs", {"id": f"eq.{run_id}"})
    if run["status"] in TERMINAL or run["execution_environment"] != settings().environment or run["backend_version"] != 2:
        return "done"
    claimed = await db.rpc("claim_run_v2", {"p_run": run_id, "p_worker": worker, "p_environment": settings().environment})
    if not claimed:
        return "wait"
    private = await db.one("run_private", {"run_id": f"eq.{run_id}"})
    cp, version = private["checkpoint"], private["checkpoint_version"]
    if not cp:
        recent = await db.rest("messages", params={"project_id": f"eq.{run['project_id']}", "order": "created_at.desc", "limit": 12})
        cp = {"snapshot": await repo.load_snapshot(run["base_revision_id"]), "role": "coordinator",
            "history": {"coordinator": [{"role": m["role"], "content": m["content"]} for m in reversed(recent)], "cad": [], "engineering": []},
            "pending": [], "requirements": [], "repairs": 0, "attempts": 0, "protocolRepairs": 0,
            "sequence": 0, "modelCalls": 0, "startedNs": time.time_ns(), "designRequested": design_work_requested(run["message"])}
        version = await persist(run, cp, worker, version)
    call = None
    tool_completed = False
    try:
        profile = await db.one("profiles", {"id": f"eq.{run['owner_id']}"})
        app_settings = AppSettings.model_validate((await db.one("app_settings", {"id": "eq.true"}))["settings"])
        if not profile["active"] or profile["must_change_password"] or app_settings.emergencyStop:
            raise Pause("Work paused by account or administrator controls.")
        if cp.get("stopAfterTool"):
            message = cp.pop("stopAfterTool")
            raise Pause(f"{message} Stopped after {cp['attempts']} attempts. Your saved design is unchanged. Review the request or Continue for another bounded repair cycle.")
        if not cp["pending"]:
            if cp["modelCalls"] >= app_settings.limits.maxModelCalls:
                raise Pause("The model-call limit was reached. Continue to grant another bounded cycle.")
            if cp["role"] == "cad" and not cp.get("sandboxReady") and cp.get("validated", {}).get("identity") != identity(cp["snapshot"], cp["requirements"]):
                cp["sandbox"] = cp.get("sandbox") or f"forma-{UUID(run_id).hex}-{uuid4().hex[:12]}"
                version = await persist(run, cp, worker, version)
                results = await asyncio.gather(ensure_sandbox(run, cp, app_settings.limits), model_turn(run, cp, app_settings.limits), return_exceptions=True)
                for result in results:
                    if isinstance(result, BaseException):
                        raise result
            else:
                await model_turn(run, cp, app_settings.limits)
        else:
            call = cp["pending"].pop(0)
            old_role = cp["role"]
            if call["name"] in ("build", "calculate"):
                cp["sandbox"] = cp.get("sandbox") or f"forma-{UUID(run_id).hex}-{uuid4().hex[:12]}"
                cp["validator"] = f"forma-{UUID(run_id).hex}-{uuid4().hex[:12]}"
                # Save environment identities before any external execution for cancellation/recovery.
                saved = {**cp, "pending": [call, *cp["pending"]]}
                version = await persist(run, saved, worker, version)
            started = time.time_ns()
            try:
                async def invoke():
                    result = await execute_tool(run, cp, call, app_settings, worker)
                    return {"result": result, "checkpoint": cp}
                if call["name"] in ("build", "calculate", "publish_revision"):
                    output = await operation(run, f"tool:{cp['sequence']}", call["name"], invoke, idempotent=call["name"] == "publish_revision")
                    cp = output["checkpoint"]
                    result = output["result"]
                else:
                    result = await execute_tool(run, cp, call, app_settings, worker)
            except (ValueError, ValidationError) as exc:
                result = {"ok": False, "error": {"category": "invalid_tool", "guidance": str(exc)[:1800]}}
                cp["protocolRepairs"] += 1
                if cp["protocolRepairs"] > 3:
                    cp["stopAfterTool"] = "The model repeatedly returned invalid tool actions."
            if result is not None:
                tool_result(cp, call, result, old_role)
            tool_completed = True
            await tracing.record(run, f"tool:{cp['sequence']}", call["name"], started, inputs=call["input"], outputs=result,
                attributes={"role": old_role, "candidate.hash": digest(cp["snapshot"]), "attempt": cp["attempts"]})
        cp["sequence"] += 1
        await persist(run, cp, worker, version)
        if cp.get("question"):
            return await finish_run(run, cp, worker, "waiting_input", cp.pop("question"))
        if cp.get("answer"):
            return await finish_run(run, cp, worker, "succeeded", cp.pop("answer"))
        return "next"
    except (Pause, models.ModelFailure, ExecutionFailure) as exc:
        if call and not tool_completed:
            tool_result(cp, call, {"ok": False, "paused": True, "message": str(exc)})
        cp["sequence"] += 1  # Continue explicitly authorizes a new operation identity.
        return await finish_run(run, cp, worker, "paused", str(exc))


async def interrupted(run_id, worker):
    run = await db.one("runs", {"id": f"eq.{run_id}"})
    private = await db.one("run_private", {"run_id": f"eq.{run_id}"})
    if run["status"] not in TERMINAL and private["lease_owner"] == worker and private["checkpoint"]:
        return await finish_run(run, private["checkpoint"], worker, "paused",
            "Execution was interrupted. The checkpoint is preserved, and uncertain external requests were not repeated. Continue to recover safely.")


async def dispatch_run(run_id):
    from .graphs.runner import dispatch_run as dispatch
    await dispatch(run_id)


async def cancel_run(run_id):
    from .graphs.runner import cancel_graph_run
    await cancel_graph_run(run_id)
