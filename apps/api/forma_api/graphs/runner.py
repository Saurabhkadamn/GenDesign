"""Bridge between durable Vercel scheduling and LangGraph checkpoints."""
from uuid import uuid4

from langgraph.types import Command
from vercel import workflow

from .. import db, repository as repo
from ..config import settings
from ..contracts import TERMINAL
from ..engine import Pause
from ..providers.openrouter import ModelFailure
from ..services.checkpoints import checkpoint_saver
from ..services import runs as run_service
from .design import build_graph, reset_worker, set_worker


def graph_config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}, "metadata": {
        "forma_run_id": run_id, "execution_environment": settings().environment}}


async def advance_graph(run_id: str, worker: str, resume: dict | None = None) -> str:
    run = await db.one("runs", {"id": f"eq.{run_id}"})
    if run["status"] in TERMINAL:
        return "done"
    claimed = await db.rpc("claim_run_v3", {"p_run": run_id, "p_worker": worker,
        "p_environment": settings().environment})
    if not claimed:
        return "busy"
    token = set_worker(worker)
    try:
        async with checkpoint_saver() as saver:
            graph = build_graph(saver)
            config = graph_config(run_id)
            before = await graph.aget_state(config)
            if not before.values:
                graph_input = {"run_id": run["id"], "project_id": run["project_id"],
                    "owner_id": run["owner_id"], "base_revision_id": run["base_revision_id"],
                    "original_request": run["message"], "selected_ids": run["selected_ids"]}
            elif resume is not None:
                graph_input = Command(resume=resume)
            else:
                graph_input = None
            try:
                result = await graph.ainvoke(graph_input, config)
            except (Pause, ModelFailure) as exc:
                # Expected bounded stops are part of the product state, not
                # infrastructure failures. Persist the actionable reason and
                # let the workflow exit without retrying a deterministic error.
                await run_service.finish(run, worker, "paused", str(exc))
                return "done"
            snapshot = await graph.aget_state(config)
            await db.update("runs", {"model_calls": result.get("model_calls", 0),
                "updated_at": repo.utcnow()}, id=run_id)
            interrupts = [item for task in snapshot.tasks for item in getattr(task, "interrupts", ())]
            if interrupts:
                payload = interrupts[0].value if isinstance(interrupts[0].value, dict) else {}
                await run_service.wait_for_input(run, worker,
                    str(payload.get("message") or "Forma needs your input before continuing."))
                return "waiting_input"
            if result.get("phase") == "done" or not snapshot.next:
                return "done"
            return "next"
    finally:
        reset_worker(token)


async def dispatch_run(run_id: str, resume: dict | None = None) -> None:
    from ..workflows import design_workflow
    run = await db.one("runs", {"id": f"eq.{run_id}"})
    if run["workflow_id"] or run["status"] != "queued":
        return
    started = await workflow.start(design_workflow, run_id=run_id,
        worker=uuid4().hex, resume=resume)
    await db.rest("runs", "PATCH", params={"id": f"eq.{run_id}", "workflow_id": "is.null"},
        body={"workflow_id": started.run_id, "dispatch_at": repo.utcnow()})


async def cancel_graph_run(run_id: str) -> None:
    run = await db.one("runs", {"id": f"eq.{run_id}"})
    try:
        async with checkpoint_saver() as saver:
            snapshot = await build_graph(saver).aget_state(graph_config(run_id))
            from ..maintenance import release_sandbox
            for name in (snapshot.values.get("sandbox"), snapshot.values.get("validator")):
                if name:
                    await release_sandbox(name)
    except Exception:
        pass
    message = "Work stopped. Published revisions are preserved."
    await db.insert("messages", {"project_id": run["project_id"], "run_id": run_id,
        "role": "assistant", "content": message}, conflict="run_id,role")
