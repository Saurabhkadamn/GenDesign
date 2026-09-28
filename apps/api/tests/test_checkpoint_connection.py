import pytest

from forma_api.services import checkpoints


@pytest.mark.asyncio
async def test_checkpoint_saver_leases_checked_connections(monkeypatch):
    configured = {}

    class FakePool:
        check_connection = object()

        def __init__(self, url, **kwargs):
            configured.update(url=url, **kwargs)

        async def __aenter__(self):
            configured["opened"] = True
            return self

        async def __aexit__(self, *_args):
            configured["closed"] = True

    class FakeSaver:
        def __init__(self, pool):
            configured["pool"] = pool

    monkeypatch.setenv("SUPABASE_DATABASE_URL", "postgresql://test.invalid/checkpoints")
    monkeypatch.setattr(checkpoints, "AsyncConnectionPool", FakePool)
    monkeypatch.setattr(checkpoints, "AsyncPostgresSaver", FakeSaver)

    async with checkpoints.checkpoint_saver() as saver:
        assert isinstance(saver, FakeSaver)
        assert configured["pool"] is not None
        assert configured["opened"] is True
        assert not configured.get("closed", False)

    assert configured["closed"] is True
    assert configured["url"] == "postgresql://test.invalid/checkpoints"
    assert configured["min_size"] == 0
    assert configured["check"] is FakePool.check_connection
    assert configured["kwargs"]["autocommit"] is True
    assert configured["kwargs"]["prepare_threshold"] is None
