"""Exercise Workflow persistence in a fresh process with an isolated SDK world."""

import os
import subprocess
import sys
from pathlib import Path


def test_python_workflow_runs_steps_and_durable_sleep(tmp_path):
    # The SDK caches its world at import time. A prior API test may have set a
    # different receipt directory, so use a fresh process for this integration.
    script = """
import asyncio
import inspect
import os
from pathlib import Path

import httpx
from vercel import workflow
from forma_api.workflows import smoke_workflow

async def main():
    timeout = inspect.signature(httpx.AsyncClient).parameters['timeout'].default
    assert isinstance(timeout, httpx.Timeout) and timeout.read >= 30
    run = await workflow.start(smoke_workflow, value=40)
    for _ in range(100):
        state = await run.status()
        if state in {'completed', 'failed'}:
            break
        await asyncio.sleep(0.2)
    assert state == 'completed'
    assert await run.return_value() == {'value': 42, 'backend': 'python'}
    assert list((Path(os.environ['WORKFLOW_LOCAL_DATA_DIR']) / 'steps').glob('*.json'))

asyncio.run(main())
"""
    environment = {**os.environ, "WORKFLOW_LOCAL_DATA_DIR": str(tmp_path),
                   "WORKFLOW_TARGET_WORLD": "local"}
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).parents[1],
        env=environment, capture_output=True, text=True, timeout=40,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert list(Path(tmp_path, "steps").glob("*.json"))
