"""Bridge between durable Vercel scheduling and LangGraph checkpoints."""
import traceback
import os
from uuid import uuid4

from langgraph.types import Command
from vercel import workflow

from .. import db, repository as repo
from ..config import settings
from ..contracts import TERMINAL
from ..engine import Pause, ProviderDeferred
from ..providers.openrouter import ModelFailure
from ..services.checkpoints import checkpoint_saver
from ..services import runs as run_service
from ..agent_memory import user_update
from .design import bounded_history


def graph_config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}, "metadata": {
        "forma_run_id": run_id, "execution_environment": settings().environment}}


async def advance_graph(run_id: str, worker: str, resume: dict | None = None) -> str:
    run = await db.one("runs", {"id": f"eq.{run_id}"})
    if run["status"] in TERMINAL:
        return "done"
    research = run.get("harness_version", 3) >= 4
    if research:
        claimed = await db.rpc("claim_research_run", {"p_run":run_id,"p_worker":worker,
            "p_environment":settings().environment,
            "p_concurrency":int(os.getenv("FORMA_WORKER_CONCURRENCY", "1"))})
    else:
        claimed = await db.rpc("claim_run_v3", {"p_run":run_id,"p_worker":worker,
            "p_environment":settings().environment})
    if not claimed:
        return "busy"
    from . import design, design_v3
    graph_module = design if run.get("harness_version", 3) >= 4 else design_v3
    token = graph_module.set_worker(worker)
    existing = await db.one("run_candidates", {"run_id":f"eq.{run_id}"}, required=False) if research else None
    lease_token = run_service.lease_context.set({"run_id":run_id,"worker":worker,"generation":claimed,
        "candidate_version":(existing or {}).get("candidate_version",0)} if research else None)
    try:
        async with checkpoint_saver() as saver:
            graph = graph_module.build_graph(saver)
            config = graph_config(run_id)
            before = await graph.aget_state(config)
            if not before.values:
                graph_input = {"run_id": run["id"], "project_id": run["project_id"],
                    "harness_version": run.get("harness_version", 3), "research_mode": run.get("research_mode", False),
                    "owner_id": run["owner_id"], "base_revision_id": run["base_revision_id"],
                    "original_request": run["message"], "selected_ids": run["selected_ids"],
                    "drawing_request": run.get("drawing_request")}
            elif resume is not None:
                interrupted = any(getattr(task, "interrupts", ()) for task in before.tasks)
                updates = {}
                if research:
                    updates['model_calls'] = await db.rpc('next_research_model_ordinal',{
                        'p_run':run_id,'p_worker':worker,'p_generation':claimed,
                        'p_minimum':before.values.get('model_calls',0)})
                if resume.get("kind") == "continue":
                    # Continue is explicit authorization for another bounded
                    # model-call cycle. Keep model_calls cumulative for audit,
                    # generation IDs and provider-cost reporting.
                    updates["model_calls_this_cycle"] = 0
                if interrupted:
                    if updates:
                        await graph.aupdate_state(config, updates)
                    graph_input = Command(resume=resume)
                else:
                    # Pause exceptions leave a pending node, not a LangGraph
                    # interrupt. Continue that node normally. Renew only the
                    # coordinator's local action allowance and per-cycle model
                    # budget on explicit Continue; keep lifetime usage and saved
                    # repair evidence.
                    message = str(resume.get("message") or "").strip()
                    if message and resume.get("kind") in {"continue", "answer"}:
                        # An exception-based pause has no interrupt node to
                        # consume user guidance. Keep it in the authoritative
                        # request and the active role's dialogue before retrying.
                        request = before.values.get("clarified_request") or \
                            before.values.get("original_request", "")
                        updates.update(user_update(before.values, message))
                        if research:
                            updates['project_context'] = await repo.agent_context(run['project_id'],run['owner_id'],
                                run['id'],run['base_revision_id'],run.get('selected_ids') or [])
                        history_key = {"coordinator_session": "coordinator_history",
                            "cad_session": "cad_history", "repair": "cad_history",
                            "review_session": "review_history"}.get(before.values.get("phase"))
                        if history_key:
                            history = before.values.get(history_key) or [{"role": "user", "content": request}]
                            updates[history_key] = bounded_history([*history,
                                {"role": "user", "content": message}])
                    if resume.get("kind") == "continue" and \
                            before.values.get("phase") == "coordinator_session" and \
                            before.values.get("coordinator_actions", 0) >= 12:
                        updates["coordinator_actions"] = 0
                    if updates:
                        await graph.aupdate_state(config, updates)
                    graph_input = None
            else:
                graph_input = None
            try:
                result = await graph.ainvoke(graph_input, config)
            except ProviderDeferred as exc:
                await repo.event(run_id,str(exc),stage="provider")
                return f"wait:{exc.seconds}:{claimed}" if research else f"wait:{exc.seconds}"
            except (Pause, ModelFailure) as exc:
                # Expected bounded stops are part of the product state, not
                # infrastructure failures. Persist the actionable reason and
                # let the workflow exit without retrying a deterministic error.
                await run_service.finish(run, worker, "paused", str(exc))
                return "done"
            except Exception as exc:
                # A fresh Workflow invocation must be diagnosable when a
                # checkpoint cannot be decoded, a resume payload is rejected,
                # or a provider/runtime integration raises outside the bounded
                # Pause/ModelFailure types. Keep the checkpoint authoritative
                # and stop this transition without silently replacing the
                # user's last visible state with a generic interruption.
                diagnostic = f"{type(exc).__name__}: {exc}"
                frames = traceback.extract_tb(exc.__traceback__)
                locations = " -> ".join(
                    f"{frame.name}:{frame.lineno}" for frame in frames[-8:]
                )
                print(f"Forma graph transition failure for {run_id}: {type(exc).__name__} at {locations}",
                      flush=True)
                await run_service.finish(
                    run, worker, "paused", f"Graph transition could not resume safely. {diagnostic[:1200]}"
                )
                return "done"
            snapshot = await graph.aget_state(config)
            if research:
                await db.rpc('record_research_progress', {'p_run':run_id,'p_worker':worker,
                    'p_generation':claimed,'p_model_calls':result.get('model_calls',0)})
            else:
                await db.update("runs", {"model_calls": result.get("model_calls", 0),
                    "updated_at": repo.utcnow()}, id=run_id)
            interrupts = [item for task in snapshot.tasks for item in getattr(task, "interrupts", ())]
            if interrupts:
                payload = interrupts[0].value if isinstance(interrupts[0].value, dict) else {}
                await run_service.wait_for_input(run, worker,
                    str(payload.get("message") or "Forma needs your input before continuing."))
                return "waiting_input"
            if result.get("phase") == "done" or not snapshot.next:
                if research:
                    await run_service.finish(run,worker,result.get('terminal_status','succeeded'),
                        result.get('final_message') or 'Execution ended; review the recorded engineering outcome.')
                return "done"
            return f"next:{claimed}" if research else "next"
    finally:
        run_service.lease_context.reset(lease_token)
        graph_module.reset_worker(token)


async def dispatch_run(run_id: str, resume: dict | None = None) -> str | None:
    from ..workflows import design_workflow
    run = await db.one("runs", {"id": f"eq.{run_id}"})
    if run.get('harness_version',3)>=4:
        from ..services.dispatch import dispatch_research
        return await dispatch_research(run,resume)
    if run["workflow_id"] or run["status"] != "queued":
        return
    started = await workflow.start(design_workflow, run_id=run_id,
        worker=uuid4().hex, resume=resume, research=run.get("research_mode", False))
    await db.rest("runs", "PATCH", params={"id": f"eq.{run_id}", "workflow_id": "is.null"},
        body={"workflow_id": started.run_id, "dispatch_at": repo.utcnow()})


async def cancel_graph_run(run_id: str) -> None:
    run = await db.one("runs", {"id": f"eq.{run_id}"})
    try:
        async with checkpoint_saver() as saver:
            from . import design, design_v3
            module = design if run.get('harness_version',3)>=4 else design_v3
            snapshot = await module.build_graph(saver).aget_state(graph_config(run_id))
            from ..maintenance import release_sandbox
            for name in (snapshot.values.get("sandbox"), snapshot.values.get("validator")):
                if name:
                    await release_sandbox(name)
    except Exception:
        pass
    if run.get('harness_version', 3) < 4:
        message = "Work stopped. Published revisions are preserved."
        await db.insert("messages", {"project_id": run["project_id"], "run_id": run_id,
            "role": "assistant", "content": message})
