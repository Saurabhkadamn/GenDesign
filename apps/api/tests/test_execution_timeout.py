import pytest
from contextlib import asynccontextmanager
from types import SimpleNamespace

from forma_api.execution import VercelExecutor


@pytest.mark.asyncio
async def test_execute_scopes_sandbox_sdk_to_command_deadline_plus_buffer(monkeypatch):
    from vercel import api

    captured = {}

    class Session:
        async def __aenter__(self):
            captured["client"] = captured["factory"]()
            captured["active"] = True

        async def __aexit__(self, *_args):
            captured["active"] = False
            await captured["client"].aclose()

    def fake_session(*, httpx_client_factory):
        captured["factory"] = httpx_client_factory
        return Session()

    async def box(name):
        assert captured["active"] is True
        assert name == "cad-run"
        return object()

    async def command(_box, args, timeout):
        assert captured["active"] is True
        captured["args"] = args
        captured["command_timeout"] = timeout
        return {"ok": True}

    monkeypatch.setattr(api, "session", fake_session)
    executor = VercelExecutor()
    monkeypatch.setattr(executor, "box", box)
    monkeypatch.setattr(executor, "command", command)

    result = await executor.execute("cad-run", "build", 180, "parts/engine.py")

    assert result == {"ok": True}
    assert captured["client"].timeout.read == 240
    assert captured["client"].timeout.connect == 30
    assert captured["command_timeout"] == 195
    assert captured["args"] == [
        "execute", "--operation", "build", "--timeout", "180", "--path", "parts/engine.py"
    ]
    assert captured["active"] is False


@pytest.mark.asyncio
async def test_sandbox_sdk_read_timeout_is_capped_for_maximum_cad_timeout(monkeypatch):
    from vercel import api

    captured = {}

    class Session:
        async def __aenter__(self):
            captured["client"] = captured["factory"]()

        async def __aexit__(self, *_args):
            await captured["client"].aclose()

    def fake_session(*, httpx_client_factory):
        captured["factory"] = httpx_client_factory
        return Session()

    async def box(_name):
        return object()

    async def command(_box, _args, _timeout):
        return {"ok": True}

    monkeypatch.setattr(api, "session", fake_session)
    executor = VercelExecutor()
    monkeypatch.setattr(executor, "box", box)
    monkeypatch.setattr(executor, "command", command)

    await executor.execute("cad-run", "build", 300)

    assert captured["client"].timeout.read == 360


@pytest.mark.asyncio
async def test_detached_cad_command_reconnects_using_receipt_when_sdk_status_is_stale(monkeypatch):
    from vercel import sandbox

    executor = VercelExecutor()
    process = SimpleNamespace(id="process-1", status=sandbox.ProcessStatus.RUNNING,
                              returncode=None, args=["execute"])

    async def refresh():
        return process

    process.refresh = refresh
    receipt_exists = False
    launched = {}

    class Box:
        async def create_process(self, command, args, **kwargs):
            launched.update(command=command, args=args, kwargs=kwargs)
            return process

        async def get_process(self, process_id, *, wait):
            assert process_id == "process-1" and wait is False
            return process

        async def run_process(self, command, args, **kwargs):
            assert command == "test" and args[1] == "/job/receipt.json"
            if args[0] == "-e":
                return SimpleNamespace(returncode=1)
            assert args[0] == "-f"
            return SimpleNamespace(returncode=0 if receipt_exists else 1)

        async def query_processes(self):
            return []

    @asynccontextmanager
    async def session(**_kwargs):
        yield

    async def box(_name):
        return Box()

    async def command(_box, args):
        assert args == ["inspect"]
        return {"exitCode": 0, "clean": True}

    monkeypatch.setattr(executor, "sdk_session", session)
    monkeypatch.setattr(executor, "box", box)
    monkeypatch.setattr(executor, "command", command)

    started = await executor.start_execution("cad-run", "build", 120)
    assert started["processId"] == "process-1"
    assert launched["kwargs"]["kill_after"] == 135
    assert await executor.inspect_execution("cad-run", "process-1") == {
        "status": "running", "processId": "process-1",
    }
    receipt_exists = True
    assert await executor.inspect_execution("cad-run", "process-1") == {
        "status": "complete", "processId": "process-1",
        "receipt": {"exitCode": 0, "clean": True},
    }
