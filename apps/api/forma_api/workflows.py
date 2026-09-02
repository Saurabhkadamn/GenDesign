"""Vercel Workflow only schedules bounded LangGraph transitions."""
from vercel import workflow
from vercel._internal.workflow import py_sandbox


if not py_sandbox.in_sandbox():
    # Keep infrastructure-only imports outside deterministic workflow replay.
    import inspect

    import httpx

    def _extend_workflow_control_timeout(seconds: float = 30.0) -> None:
        """Keep slow Workflow control-plane callbacks from stranding queued runs.

        Vercel's Python Workflow SDK 0.10.0 creates its event client without an
        explicit timeout, so HTTPX's five-second default applies while recording
        RunStarted and step events. The Workflow API can occasionally exceed that
        during a queue callback. This adjusts the existing HTTPX default object
        before the Workflow registry is created. Remove it when the SDK exposes a
        public control-plane timeout.
        """
        default = inspect.signature(httpx.AsyncClient).parameters["timeout"].default
        if not isinstance(default, httpx.Timeout):
            return
        for field in ("connect", "read", "write", "pool"):
            current = getattr(default, field)
            if current is None or current < seconds:
                setattr(default, field, seconds)

    _extend_workflow_control_timeout()

wf = workflow.Workflows()


@wf.step
async def smoke_step(*, value: int) -> int:
    return value + 1


@wf.workflow
async def smoke_workflow(*, value: int = 0) -> dict:
    first = await smoke_step(value=value)
    await workflow.sleep("2 seconds")
    return {"value": await smoke_step(value=first), "backend": "python"}


@wf.step(max_retries=1)
async def advance(*, run_id: str, worker: str, resume: dict | None = None) -> str:
    from .graphs.runner import advance_graph
    return await advance_graph(run_id, worker, resume)


@wf.step(max_retries=1)
async def pause_interrupted(*, run_id: str, worker: str) -> None:
    from . import db
    from .services.runs import finish
    run = await db.one("runs", {"id": f"eq.{run_id}"})
    try:
        await finish(run, worker, "paused",
            "Execution was interrupted. LangGraph preserved the last completed node; Continue when the connection is ready.")
    except Exception:
        pass


@wf.workflow
async def design_workflow(*, run_id: str, worker: str, resume: dict | None = None):
    pending_resume = resume
    try:
        for _ in range(120):
            state = await advance(run_id=run_id, worker=worker, resume=pending_resume)
            pending_resume = None
            if state in ("done", "waiting_input"):
                return {"runId": run_id, "state": state}
            if state == "busy":
                await workflow.sleep("5 seconds")
    except Exception:
        await pause_interrupted(run_id=run_id, worker=worker)
        return {"runId": run_id, "state": "paused"}
    return {"runId": run_id, "state": "bounded"}
