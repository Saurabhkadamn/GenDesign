"""LangGraph checkpoint connections.

Production uses Supabase's PostgreSQL transaction pooler. Tests and local API
work may use the in-memory saver when no database URL is configured.
"""
import os
from contextlib import asynccontextmanager

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row


class CheckpointConfigurationError(RuntimeError):
    pass


@asynccontextmanager
async def checkpoint_saver():
    database_url = os.getenv("SUPABASE_DATABASE_URL", "").strip()
    if not database_url:
        if os.getenv("VERCEL") == "1":
            raise CheckpointConfigurationError(
                "SUPABASE_DATABASE_URL is required for hosted LangGraph checkpoints."
            )
        yield InMemorySaver()
        return
    connection = await AsyncConnection.connect(
        database_url,
        autocommit=True,
        prepare_threshold=None,
        row_factory=dict_row,
    )
    try:
        yield AsyncPostgresSaver(connection)
    finally:
        await connection.close()


async def setup_checkpoints() -> None:
    database_url = os.getenv("SUPABASE_DATABASE_URL", "").strip()
    if not database_url:
        raise CheckpointConfigurationError("SUPABASE_DATABASE_URL is not configured.")
    async with checkpoint_saver() as saver:
        await saver.setup()


async def setup_and_harden() -> None:
    """Create official saver tables, then deny browser roles access."""
    await setup_checkpoints()
    database_url = os.environ["SUPABASE_DATABASE_URL"]
    connection = await AsyncConnection.connect(database_url, autocommit=True,
        prepare_threshold=None)
    try:
        async with connection.cursor() as cursor:
            for table in ("checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes"):
                await cursor.execute(f'ALTER TABLE IF EXISTS public."{table}" ENABLE ROW LEVEL SECURITY')
                await cursor.execute(f'REVOKE ALL ON public."{table}" FROM PUBLIC, anon, authenticated')
    finally:
        await connection.close()
