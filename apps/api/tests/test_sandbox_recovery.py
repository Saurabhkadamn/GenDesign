from types import SimpleNamespace
from uuid import uuid4

import pytest

from forma_api import engine
from forma_api.contracts import AppSettings, Snapshot
from forma_api.execution import SandboxExpired, identity


@pytest.mark.asyncio
async def test_ready_checkpoint_recreates_expired_sandbox(monkeypatch):
    run = {"id": str(uuid4())}
    checkpoint = {"sandbox": "expired", "sandboxReady": True}
    retired = []
    created = []

    class FakeExecutor:
        async def is_running(self, name):
            if name == "expired":
                raise SandboxExpired("stopped")
            return True

        async def create(self, name):
            created.append(name)
            return name

    async def release(name):
        retired.append(name)

    async def operation(_run, _key, _kind, callback, **_kwargs):
        return await callback()

    async def event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(engine, "executor", lambda: FakeExecutor())
    monkeypatch.setattr(engine, "settings", lambda: SimpleNamespace(executor="test"))
    monkeypatch.setattr(engine, "operation", operation)
    monkeypatch.setattr(engine.repo, "event", event)
    monkeypatch.setattr(engine.tracing, "record", event)
    from forma_api import maintenance
    monkeypatch.setattr(maintenance, "release_sandbox", release)

    await engine.ensure_sandbox(run, checkpoint, AppSettings().limits)

    assert retired == ["expired"]
    assert len(created) == 1
    assert checkpoint["sandbox"] == created[0]
    assert checkpoint["sandbox"] != "expired"
    assert checkpoint["sandboxReady"] is True


@pytest.mark.asyncio
async def test_expiry_during_stage_retries_same_candidate_once(monkeypatch):
    run = {"id": str(uuid4())}
    snapshot = Snapshot().model_dump()
    checkpoint = {"snapshot": snapshot, "requirements": [], "sandbox": "first",
                  "sandboxReady": True, "attempts": 0, "validated": {}}
    staged = []
    retired = []

    class FakeExecutor:
        async def stage(self, name, files):
            staged.append((name, files))
            if len(staged) == 1:
                raise SandboxExpired("stopped")

        async def execute(self, name, _operation, _timeout):
            return {"identity": identity(snapshot, []), "exitCode": 1,
                    "clean": True, "timedOut": False, "diagnostic": "SyntaxError"}

    async def ensure(_run, _checkpoint, _limits):
        return None

    async def replace(_run, cp):
        retired.append(cp["sandbox"])
        cp["sandbox"] = "second"

    async def event(*_args, **_kwargs):
        return None

    async def reject(_run, cp, _expected, _error, _limits):
        return {"ok": False, "attempt": cp["attempts"]}

    monkeypatch.setattr(engine, "executor", lambda: FakeExecutor())
    monkeypatch.setattr(engine, "ensure_sandbox", ensure)
    monkeypatch.setattr(engine, "replace_expired_sandbox", replace)
    monkeypatch.setattr(engine.repo, "event", event)
    monkeypatch.setattr(engine.tracing, "record", event)
    monkeypatch.setattr(engine, "reject_candidate", reject)

    result = await engine.build_candidate(run, checkpoint, AppSettings().limits, "test")

    assert result == {"ok": False, "attempt": 1}
    assert retired == ["first"]
    assert [name for name, _files in staged] == ["first", "second"]
    assert staged[0][1] == staged[1][1]
