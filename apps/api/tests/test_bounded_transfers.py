import asyncio

import pytest

from forma_api import engine
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


@pytest.mark.asyncio
async def test_validation_reopens_step_from_builder_without_checkpointing_bytes(monkeypatch):
    snapshot = {
        "manifest": {"rootComponentId": "block", "components": [
            {"id": "block", "name": "Block", "kind": "solid",
             "source": "parts/block.py", "parameters": {"width": 7.25}},
        ]},
        "files": {"parts/block.py": "def build(parameters, dependencies):\n    return None\n"},
    }
    checkpoint = {"snapshot": snapshot, "requirements": [], "sandbox": "builder"}
    captured = {}

    class Reader:
        async def read(self, sandbox, filename):
            assert (sandbox, filename) == ("builder", "block.step")
            return b"independent STEP bytes"

    async def validate(_run, _checkpoint, _snapshot, metadata, step_files, _limits, _key,
                       *, defer_upload):
        assert defer_upload is False
        captured.update(metadata=metadata, step_files=step_files)
        return {"ok": True}

    monkeypatch.setattr(engine, "executor", Reader)
    monkeypatch.setattr(engine, "validate_step_candidate", validate)
    result = await engine.validate_built_candidate(
        {"harness_version": 4}, checkpoint, object(), "test")

    assert result == {"ok": True}
    assert captured["step_files"] == {"block.step": b"independent STEP bytes"}
    assert set(captured["metadata"]) == {
        "manifest.json", "requirements.json", "identity.json",
    }
