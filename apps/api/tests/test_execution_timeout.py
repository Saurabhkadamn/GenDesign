import pytest

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
