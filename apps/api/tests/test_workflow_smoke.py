import asyncio
import inspect
from pathlib import Path

import httpx
import pytest


@pytest.mark.asyncio
async def test_python_workflow_runs_steps_and_durable_sleep(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WORKFLOW_TARGET_WORLD", "local")
    from vercel import workflow
    from forma_api.workflows import smoke_workflow

    timeout = inspect.signature(httpx.AsyncClient).parameters["timeout"].default
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read >= 30

    run = await workflow.start(smoke_workflow, value=40)
    for _ in range(100):
        state = await run.status()
        if state in {"completed", "failed"}:
            break
        await asyncio.sleep(0.2)
    assert state == "completed"
    assert await run.return_value() == {"value": 42, "backend": "python"}
    assert list(Path(tmp_path, "steps").glob("*.json"))
