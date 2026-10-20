"""Recover an overdue queue sleep without creating another CAD run or bypassing its ledger."""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from vercel._internal.workflow import world as workflow_world

from . import db, repository as repo


async def wake_queued_run(run: dict) -> dict:
    if run.get('status') != 'queued' or not run.get('workflow_id'):
        raise HTTPException(409, 'Only an already-dispatched queued run can be woken.')
    now = datetime.now(timezone.utc)
    private = await db.one('run_private', {'run_id': f"eq.{run['id']}"})
    lease = private.get('lease_until')
    if lease and datetime.fromisoformat(lease.replace('Z', '+00:00')) > now:
        raise HTTPException(409, 'A worker still owns this run; no duplicate wake was sent.')
    world = workflow_world.get_world()
    workflow = await world.runs_get(run['workflow_id'])
    if workflow.status != 'running':
        raise HTTPException(409, 'The scheduler is not in a running wait state. Inspect its diagnostic before redispatch.')
    page = await world.events_list(run['workflow_id'], pagination=workflow_world.PaginationOptions(limit=1, sort_order='desc'))
    event = page.data[0] if page.data else None
    if not event or event.event_type != 'wait_created' or event.event_data.resume_at + timedelta(seconds=120) > now:
        raise HTTPException(409, 'No overdue queue sleep was confirmed. Active operations were not retried.')
    namespace = (workflow.execution_context or {}).get('queueNamespace')
    workflow_world.validate_queue_namespace(namespace)
    if not event.server_props:
        raise HTTPException(409, 'The scheduler wait has no persisted identity; no wake was sent.')
    key = f"forma-wake:{run['workflow_id']}:{event.server_props.event_id}"
    # Re-enqueue the same scheduler identity on its original deployment. Its
    # completed steps replay from the ledger, and the elapsed wait completes
    # normally. No new workflow, candidate, model ordinal or retry consent.
    message = await world.queue(workflow_world.get_queue_name(workflow.workflow_name, namespace),
        workflow_world.WorkflowInvokePayload(runId=run['workflow_id']), deployment_id=workflow.deployment_id,
        idempotency_key=key)
    await repo.event(run['id'], 'Re-enqueued the existing scheduler after confirming an overdue queue sleep; saved design and model usage retained.', stage='coordination')
    return {'runId': run['id'], 'workflowId': run['workflow_id'], 'woken': True,
            'duplicateWake': str(message).startswith('msg_duplicate_'), 'newRunCreated': False}
