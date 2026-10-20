from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from forma_api import workflow_recovery as recovery


def fixtures(monkeypatch, *, state='queued', lease=None, event_type='wait_created', overdue=True):
    sent=[]
    run={'id':'cad-run','workflow_id':'workflow-run','status':state}
    event=recovery.workflow_world.WaitCreatedEvent.model_validate({'eventType':'wait_created',
        'correlationId':'wait','eventId':'overdue-event','runId':'workflow-run',
        'createdAt':datetime.now(timezone.utc).isoformat(),'eventData':{
        'resumeAt':(datetime.now(timezone.utc)+timedelta(seconds=-600 if overdue else 60)).isoformat()}})
    if event_type != 'wait_created': event=SimpleNamespace(event_type=event_type)
    async def one(*_): return {'lease_until':lease}
    async def get(_): return SimpleNamespace(status='running',workflow_name='workflow//saved',deployment_id='original-deployment',execution_context=None)
    async def events(*_,**__): return SimpleNamespace(data=[event])
    async def queue(*args,**kwargs): sent.append((args,kwargs));return 'message'
    async def log(*_,**__): pass
    world=SimpleNamespace(runs_get=get,events_list=events,queue=queue)
    monkeypatch.setattr(recovery.db,'one',one)
    monkeypatch.setattr(recovery.repo,'event',log)
    monkeypatch.setattr(recovery.workflow_world,'get_world',lambda:world)
    return run,sent,world


@pytest.mark.asyncio
async def test_overdue_queue_wakes_existing_workflow_and_original_deployment(monkeypatch):
    run,sent,_=fixtures(monkeypatch)
    before=dict(run)
    result=await recovery.wake_queued_run(run)
    assert result['woken'] and not result['newRunCreated'] and run==before
    args,kwargs=sent[0]
    assert args[1].run_id=='workflow-run' and kwargs['deployment_id']=='original-deployment'
    assert kwargs['idempotency_key']=='forma-wake:workflow-run:overdue-event'


@pytest.mark.asyncio
@pytest.mark.parametrize('kwargs',[
    {'state':'running'},
    {'lease':(datetime.now(timezone.utc)+timedelta(minutes=3)).isoformat()},
    {'event_type':'step_started'},
    {'overdue':False},
])
async def test_active_or_unconfirmed_work_is_never_reenqueued(monkeypatch,kwargs):
    run,sent,_=fixtures(monkeypatch,**kwargs)
    with pytest.raises(HTTPException) as error: await recovery.wake_queued_run(run)
    assert error.value.status_code==409 and not sent


@pytest.mark.asyncio
async def test_completed_scheduler_requires_reconciliation_not_duplicate_dispatch(monkeypatch):
    run,sent,world=fixtures(monkeypatch)
    async def get(_): return SimpleNamespace(status='completed')
    world.runs_get=get
    with pytest.raises(HTTPException): await recovery.wake_queued_run(run)
    assert not sent


@pytest.mark.parametrize('role,expected',[('designer',403),('admin',200)])
def test_recovery_endpoint_requires_admin_and_owned_run(monkeypatch,role,expected):
    from fastapi.testclient import TestClient
    from forma_api import api
    from forma_api.main import app
    called=[]
    async def profile(*_,**__): return {'id':'owner','role':role}
    async def owned(run_id,owner):
        assert run_id=='run' and owner=='owner'
        return {'id':run_id,'backend_version':3,'execution_environment':api.settings().environment}
    async def wake(run): called.append(run);return {'woken':True}
    monkeypatch.setattr(api,'require_profile',profile)
    monkeypatch.setattr(api.repo,'owned_run',owned)
    monkeypatch.setattr(recovery,'wake_queued_run',wake)
    with TestClient(app) as client:
        response=client.post('/api/runs/run/wake',headers={'Origin':'http://localhost:3000'})
    assert response.status_code==expected and bool(called)==(role=='admin')
