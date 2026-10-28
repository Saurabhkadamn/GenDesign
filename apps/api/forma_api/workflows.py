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
async def pause_interrupted(*, run_id: str, worker: str, diagnostic: str = "") -> str:
    from . import db
    from .services.runs import finish
    run = await db.one("runs", {"id": f"eq.{run_id}"})
    from .tracing import sanitize
    message = ("Execution was interrupted. The last completed step and built files are saved. "
               "Continue to resume from that point.")
    if diagnostic:
        message += f" Diagnostic: {sanitize(diagnostic)[:1200]}"
    if run.get('harness_version',3)>=4:
        return await db.rpc('interrupt_research_workflow',{
            'p_run':run_id,'p_worker':worker,'p_message':message})
    await finish(run, worker, "paused", message)
    return 'paused'


@wf.step(max_retries=1)
async def continue_research(*, run_id: str, worker: str, generation: int):
    from .services.research_execution import rollover
    return await rollover(run_id,worker,generation)


@wf.workflow
async def design_workflow(*, run_id: str, worker: str, resume: dict | None = None, research: bool = False):
    pending_resume = resume
    transitions = 0
    generation = None
    busy_delay = 5
    try:
        # Budget completed graph transitions, not queue polling. A queued run
        # may wait behind a long build without consuming any model allowance.
        # The graph enforces its own call/repair/resource limits; leave room
        # for non-model build/validation/recovery nodes within those budgets.
        while transitions < 512:
            state = await advance(run_id=run_id, worker=worker, resume=pending_resume)
            if state.startswith("wait:"):
                fields = state.split(':')
                if len(fields)>2:
                    generation = int(fields[2])
                await workflow.sleep(f"{int(fields[1])} seconds")
                pending_resume = None
                continue
            if state == "busy":
                await workflow.sleep(f"{busy_delay} seconds")
                busy_delay = min(busy_delay * 2, 60)
                continue
            # A busy worker did not claim or consume the human response.
            pending_resume = None
            busy_delay = 5
            transitions += 1
            if state in ("done", "waiting_input"):
                return {"runId": run_id, "state": state}
            if state.startswith('next:'):
                generation = int(state.split(':',1)[1])
            elif state != "next":
                raise ValueError("Unexpected graph scheduling state")
    except Exception as exc:
        # Never hide a workflow/runtime exception behind a generic pause. The
        # checkpoint remains authoritative, while the bounded diagnostic makes
        # provider, serialization, and resume failures actionable to the user
        # and visible in Vercel runtime logs.
        diagnostic = f"{type(exc).__name__}: {exc}"
        # Keep unsanitized exception payloads out of platform logs. The step
        # records a bounded, redacted reason and verifies the dispatch owner.
        state = await pause_interrupted(run_id=run_id, worker=worker, diagnostic=diagnostic)
        return {"runId": run_id, "state": state or ("interruption_pending" if research else "paused")}
    if research:
        if generation is None:
            raise ValueError('Research continuation has no completed transition fencing token')
        dispatch_state = await continue_research(run_id=run_id,worker=worker,generation=generation)
        return {"runId":run_id,"state":"continued" if dispatch_state=='dispatched'
                else 'continuation_pending','dispatchState':dispatch_state}
    # The workflow's transition cap is a scheduling boundary, not a terminal
    # graph state. Clear its lease and expose Continue so the same LangGraph
    # thread can be resumed by a fresh Workflow invocation.
    state = await pause_interrupted(run_id=run_id, worker=worker,
        diagnostic="The workflow transition limit was reached; Continue to resume the saved design.")
    return {"runId": run_id, "state": state or "paused"}
