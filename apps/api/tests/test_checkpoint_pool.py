import pytest


@pytest.mark.asyncio
async def test_checkpoint_saver_uses_a_pool_for_long_graph_steps(monkeypatch):
    import forma_api.services.checkpoints as checkpoints

    class FakePool:
        instances = []

        def __init__(self, url, **kwargs):
            self.url = url
            self.kwargs = kwargs
            self.opened = False
            self.closed = False
            self.__class__.instances.append(self)

        async def open(self):
            self.opened = True

        async def close(self):
            self.closed = True

        async def __aenter__(self):
            await self.open()
            return self

        async def __aexit__(self, *args):
            await self.close()

        @staticmethod
        async def check_connection(connection):
            return None

    class FakeSaver:
        def __init__(self, conn):
            self.conn = conn

    monkeypatch.setenv("SUPABASE_DATABASE_URL", "postgresql://pooler.test/postgres")
    monkeypatch.setattr(checkpoints, "AsyncConnectionPool", FakePool)
    monkeypatch.setattr(checkpoints, "FormaPostgresSaver", FakeSaver)

    async with checkpoints.checkpoint_saver() as saver:
        assert isinstance(saver, FakeSaver)
        assert isinstance(saver.conn, FakePool)
        assert saver.conn.opened is True
        assert saver.conn.kwargs["min_size"] == 0
        assert saver.conn.kwargs["max_size"] == 1
        assert saver.conn.kwargs["check"] is FakePool.check_connection

    assert saver.conn.closed is True
