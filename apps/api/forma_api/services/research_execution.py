"""Fenced external operations and a durable continuation outbox."""

import asyncio
from contextlib import asynccontextmanager, suppress

from .. import db
from .runs import lease_context


async def renew_fence():
    lease = lease_context.get()
    if lease:
        await db.rpc(
            "check_research_fence",
            {
                "p_run": lease["run_id"],
                "p_worker": lease["worker"],
                "p_generation": lease["generation"],
            },
        )


@asynccontextmanager
async def fenced_operation():
    lease = lease_context.get()
    if not lease:
        yield
        return
    await renew_fence()
    owner = asyncio.current_task()
    failure = []

    async def heartbeat():
        try:
            while True:
                await asyncio.sleep(15)
                await renew_fence()
        except Exception as exc:
            failure.append(exc)
            owner.cancel()

    task = asyncio.create_task(heartbeat())
    try:
        yield
        await renew_fence()
    except asyncio.CancelledError:
        if failure:
            raise failure[0]
        raise
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def rollover(run_id: str, worker: str, generation: int):
    from ..graphs.runner import dispatch_run

    # The token comes from this workflow's last completed transition. Reading the
    # current token here could let an expired scheduler impersonate its successor.
    await db.rpc(
        "rollover_research_run",
        {
            "p_run": run_id,
            "p_worker": worker,
            "p_generation": generation,
            "p_payload": {"kind": "workflow_continuation"},
        },
    )
    return await dispatch_run(run_id)
