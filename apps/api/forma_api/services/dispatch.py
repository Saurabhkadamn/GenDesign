"""Persist a Workflow identity before queueing it; reconcile only known identities."""

from uuid import uuid4

from .. import db
from ..config import settings


async def dispatch_research(run, resume=None):
    from vercel._internal.workflow import world as w, serialization as ser
    from ..workflows import design_workflow

    worker = uuid4().hex
    item = await db.rpc(
        "reserve_research_dispatch", {"p_run": run["id"], "p_worker": worker, "p_resume": resume}
    )
    if not item:
        return "not_dispatchable"
    if item["status"] in {"dispatched", "complete", "cancelled"}:
        return item["status"]
    if item["status"] == "ambiguous" or (
        item["status"] == "creating" and item["worker_id"] != worker
    ):
        # Workflow SDK run creation has no client idempotency key. Do not create
        # a second scheduler when its first creation may have succeeded.
        return item["status"]
    world = w.get_world()
    if item["status"] == "creating":
        requested = False
        try:
            deployment_id = await world.get_deployment_id()
            namespace = design_workflow._resolve_queue_namespace()
            args, kwargs = design_workflow.bind_arguments(
                (),
                {
                    "run_id": run["id"],
                    "worker": worker,
                    "resume": item["payload"].get("resume"),
                    "research": run.get("research_mode", False),
                },
            )
            event = w.RunCreatedEventData(
                deploymentId=deployment_id,
                workflowName=design_workflow.workflow_id,
                input=ser.dehydrate(ser.argument_array(args, kwargs)),
                executionContext={"queueNamespace": namespace} if namespace is not None else None,
            )
            requested = True
            created = await world.events_create(None, event.into_event())
            if not created.run:
                raise ValueError("Workflow creation returned no scheduler identity")
            metadata = {
                "deploymentId": deployment_id,
                "workflowName": design_workflow.workflow_id,
                "queueNamespace": namespace,
            }
            queued = await db.rpc(
                "prepare_research_dispatch",
                {
                    "p_id": item["id"],
                    "p_worker": worker,
                    "p_workflow": created.run.run_id,
                    "p_metadata": metadata,
                },
            )
            if not queued:
                return "cancelled"
            item = {
                **item,
                "status": "prepared",
                "workflow_id": created.run.run_id,
                "dispatch_metadata": metadata,
            }
        except Exception:
            # A definite pre-request failure is retryable. A lost create reply
            # requires external reconciliation and preserves the complete project.
            await db.rpc(
                "fail_research_dispatch",
                {"p_id": item["id"], "p_worker": worker, "p_uncertain": requested},
            )
            return "ambiguous" if requested else "pending"
    if item["status"] == "prepared":
        # A queue interruption can safely repeat the same workflow identity and
        # outbox idempotency key, including after process restart.
        current = await db.one("runs", {"id": f"eq.{run['id']}"})
        if (
            current["status"] not in {"queued", "running"}
            or current.get("workflow_id") != item["workflow_id"]
        ):
            return "not_dispatchable"
        config = await db.one("app_settings", {"id": "eq.true"})
        if config["settings"].get("emergencyStop"):
            return "stopped"
        metadata = item["dispatch_metadata"]
        try:
            await world.queue(
                w.get_queue_name(metadata["workflowName"], metadata.get("queueNamespace")),
                w.WorkflowInvokePayload(runId=item["workflow_id"]),
                deployment_id=metadata["deploymentId"],
                idempotency_key="forma-dispatch:" + item["id"],
            )
        except Exception:
            return "prepared"  # The same queue key can be retried by reconciliation.
        confirmed = await db.rpc(
            "complete_research_dispatch", {"p_id": item["id"], "p_workflow": item["workflow_id"]}
        )
        return "dispatched" if confirmed else "not_dispatchable"
    return item["status"]


async def reconcile_prepared_dispatches():
    """For a scheduler maintenance invocation: never replay unknown run creation."""
    await db.rpc("expire_research_dispatches", {"p_environment": settings().environment})
    items = await db.rest(
        "execution_outbox",
        params={
            "status": "in.(pending,prepared)",
            "select": "*,runs!inner(execution_environment,status)",
            "runs.execution_environment": f"eq.{settings().environment}",
            "runs.status": "eq.queued",
            "order": "updated_at.asc",
            "limit": 20,
        },
    )
    outcomes = []
    for item in items:
        run = await db.one("runs", {"id": f"eq.{item['run_id']}"})
        if run.get("harness_version") == 4 and run["status"] == "queued":
            try:
                outcome = await dispatch_research(run)
            except Exception:
                outcome = "retry_pending"
            outcomes.append({"runId": run["id"], "state": outcome})
    return outcomes
