from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from vercel._internal.workflow import world

from forma_api import api, db
from forma_api.services import dispatch, research_execution
from forma_api import workflows as workflows  # Register SDK handlers before replacing its transport.


@pytest.mark.asyncio
async def test_known_scheduler_is_requeued_with_same_identity_after_lost_queue_reply(monkeypatch):
    item = {
        "id": "receipt",
        "status": "prepared",
        "workflow_id": "known-workflow",
        "worker_id": "creator",
        "payload": {},
        "dispatch_metadata": {"workflowName": "workflow//design", "deploymentId": "deployment"},
    }
    queued, saved = [], []

    async def one(table, params):
        if table == "app_settings":
            return {"settings": {}}
        return {"status": "queued", "workflow_id": "known-workflow"}

    async def rpc(name, value):
        if name == "reserve_research_dispatch":
            return deepcopy(item)
        assert name == "complete_research_dispatch"
        saved.append(value)
        return True

    async def queue(*args, **kwargs):
        queued.append((args, kwargs))
        if len(queued) == 1:
            raise TimeoutError("reply lost after queue acceptance")

    monkeypatch.setattr(db, "one", one)
    monkeypatch.setattr(db, "rpc", rpc)
    monkeypatch.setattr(world, "get_world", lambda: SimpleNamespace(queue=queue))
    assert await dispatch.dispatch_research({"id": "run"}) == "prepared"
    assert saved == []
    assert await dispatch.dispatch_research({"id": "run"}) == "dispatched"
    assert queued[0] == queued[1]
    assert queued[0][1]["idempotency_key"] == "forma-dispatch:receipt"
    assert saved == [{"p_id": "receipt", "p_workflow": "known-workflow"}]


@pytest.mark.asyncio
async def test_lost_scheduler_creation_reply_is_not_replayed(monkeypatch):
    item = {"id": "receipt", "status": "pending", "payload": {}}
    created = []

    async def rpc(name, value):
        if name == "reserve_research_dispatch":
            if item["status"] == "pending":
                item.update(status="creating", worker_id=value["p_worker"])
            return deepcopy(item)
        assert name == "fail_research_dispatch" and value["p_uncertain"] is True
        item["status"] = "ambiguous"

    async def deployment():
        return "deployment"

    async def create(*args):
        created.append(args)
        raise TimeoutError("scheduler may have been created")

    monkeypatch.setattr(db, "rpc", rpc)
    monkeypatch.setattr(
        world,
        "get_world",
        lambda: SimpleNamespace(get_deployment_id=deployment, events_create=create),
    )
    assert await dispatch.dispatch_research({"id": "run"}) == "ambiguous"
    assert await dispatch.dispatch_research({"id": "run"}) == "ambiguous"
    assert len(created) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["cancelled", "emergency"])
async def test_no_queue_after_cancellation_or_emergency_stop(monkeypatch, stop):
    async def rpc(*args):
        return {"id": "receipt", "status": "prepared", "workflow_id": "known", "worker_id": "w"}

    async def one(table, params):
        if table == "app_settings":
            return {"settings": {"emergencyStop": True}}
        return {"status": "cancelled" if stop == "cancelled" else "queued", "workflow_id": "known"}

    monkeypatch.setattr(db, "rpc", rpc)
    monkeypatch.setattr(db, "one", one)
    monkeypatch.setattr(world, "get_world", lambda: SimpleNamespace())
    assert await dispatch.dispatch_research({"id": "run"}) in {"not_dispatchable", "stopped"}


@pytest.mark.asyncio
async def test_rollover_uses_original_token_and_does_not_claim_ambiguous_dispatch_complete(
    monkeypatch,
):
    from forma_api.graphs import runner

    recorded = []

    async def rpc(name, value):
        recorded.append((name, value))
        return "outbox"

    async def dispatch_run(run_id):
        return "ambiguous"

    monkeypatch.setattr(db, "rpc", rpc)
    monkeypatch.setattr(runner, "dispatch_run", dispatch_run)
    assert await research_execution.rollover("run", "original", 7) == "ambiguous"
    assert recorded == [
        (
            "rollover_research_run",
            {
                "p_run": "run",
                "p_worker": "original",
                "p_generation": 7,
                "p_payload": {"kind": "workflow_continuation"},
            },
        )
    ]


@pytest.mark.asyncio
async def test_reconciliation_endpoint_requires_secret_and_rollout_switch(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "local-test-only")
    monkeypatch.delenv("FORMA_RESEARCH_RECONCILIATION", raising=False)

    def request(auth):
        return Request({"type": "http", "headers": [(b"authorization", auth.encode())]})

    with pytest.raises(HTTPException) as failure:
        await api.reconcile_research_dispatch(request("Bearer wrong"))
    assert failure.value.status_code == 401
    assert await api.reconcile_research_dispatch(request("Bearer local-test-only")) == {
        "enabled": False
    }
