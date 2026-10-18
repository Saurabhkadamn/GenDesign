"""Exercise the actual scheduler body without external model calls or real sleeps."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


def scheduler(advance, sleep, pause):
    # The production decorator registers a WorkflowDefinition instead of a
    # directly callable function. Compile its exact undecorated body for these
    # state-sequence tests. test_workflow_smoke exercises SDK sleep/step replay.
    module = ast.parse((Path(__file__).parents[1]/'forma_api/workflows.py').read_text(encoding='utf-8'))
    function = next(node for node in module.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'design_workflow')
    function.decorator_list = []
    tree = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    namespace = {'advance': advance, 'workflow': SimpleNamespace(sleep=sleep), 'pause_interrupted': pause}
    exec(compile(tree, 'workflow-scheduler-test', 'exec'), namespace)
    return namespace['design_workflow']


@pytest.mark.asyncio
async def test_queue_waits_do_not_consume_transition_budget_or_human_response():
    calls, delays, pauses = [], [], []
    response = {'kind': 'answer', 'message': 'Keep exact dimensions'}
    async def advance(**kwargs):
        calls.append(kwargs)
        return 'busy' if len(calls) <= 180 else 'next' if len(calls) == 181 else 'done'
    async def sleep(delay): delays.append(delay)
    async def pause(**kwargs): pauses.append(kwargs)
    result = await scheduler(advance, sleep, pause)(run_id='run', worker='worker', resume=response)
    assert result['state'] == 'done' and not pauses
    assert len(delays) == 180 and delays[:5] == ['5 seconds', '10 seconds', '20 seconds', '40 seconds', '60 seconds']
    assert delays[-1] == '60 seconds'
    assert all(call['resume'] == response for call in calls[:181])
    assert calls[-1]['resume'] is None


@pytest.mark.asyncio
async def test_completed_transitions_remain_bounded_without_premature_120_step_stop():
    calls, pauses = [], []
    async def advance(**kwargs):
        calls.append(kwargs)
        return 'next'
    async def sleep(_): pytest.fail('Active progress should not sleep')
    async def pause(**kwargs): pauses.append(kwargs)
    result = await scheduler(advance, sleep, pause)(run_id='run', worker='worker')
    assert len(calls) == 512 and result['state'] == 'paused'
    assert len(pauses) == 1 and 'transition limit' in pauses[0]['diagnostic']


@pytest.mark.asyncio
async def test_invalid_scheduler_result_is_diagnosed_instead_of_spinning():
    pauses = []
    async def advance(**_): return 'invalid'
    async def sleep(_): pytest.fail('Invalid state must be diagnosed')
    async def pause(**kwargs): pauses.append(kwargs)
    result = await scheduler(advance, sleep, pause)(run_id='run', worker='worker')
    assert result['state'] == 'paused' and len(pauses) == 1
    assert 'Unexpected graph scheduling state' in pauses[0]['diagnostic']
