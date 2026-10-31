import asyncio

import pytest

from forma_api.engine import bounded_operations


@pytest.mark.asyncio
async def test_bounded_transfers_preserve_order_and_settle_before_failure():
    active = 0
    maximum = 0
    completed = []

    async def transfer(index):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(0.01)
            completed.append(index)
            if index == 3:
                raise RuntimeError("transfer failed")
            return index * 2
        finally:
            active -= 1

    with pytest.raises(RuntimeError, match="transfer failed"):
        await bounded_operations(range(8), 4, transfer)
    assert maximum == 4
    assert active == 0
    assert sorted(completed) == list(range(8))

    async def ordered(index):
        await asyncio.sleep((8 - index) * 0.001)
        return index

    assert await bounded_operations(range(8), 4, ordered) == list(range(8))
