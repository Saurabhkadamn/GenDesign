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
    assert checkpoints.conninfo_to_dict(configured["url"])["host"] == "test.invalid"
    assert checkpoints.conninfo_to_dict(configured["url"])["sslmode"] == "require"
    assert configured["min_size"] == 0
    assert configured["max_size"] == 1
    assert configured["check"] is FakePool.check_connection
    assert configured["kwargs"]["autocommit"] is True
    assert configured["kwargs"]["prepare_threshold"] is None


def test_shared_supabase_transaction_url_uses_session_port_without_losing_credentials():
    original = ("postgresql://postgres.ref:p%40ss@"
                "aws-0-ap-south-1.pooler.supabase.com:6543/postgres")
    parsed = checkpoints.conninfo_to_dict(checkpoints.checkpoint_database_url(original))
    assert parsed["host"] == "aws-0-ap-south-1.pooler.supabase.com"
    assert parsed["port"] == "5432"
    assert parsed["user"] == "postgres.ref"
    assert parsed["password"] == "p@ss"
    assert parsed["sslmode"] == "require"


def test_dedicated_transaction_pooler_is_rejected_for_pipelined_checkpoints():
    with pytest.raises(checkpoints.CheckpointConfigurationError, match="transaction pooler"):
        checkpoints.checkpoint_database_url(
            "postgresql://postgres:secret@db.example.supabase.co:6543/postgres"
        )
