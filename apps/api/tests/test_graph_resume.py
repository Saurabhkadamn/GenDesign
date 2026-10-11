from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from forma_api.graphs import runner
from forma_api.engine import Pause


class SavedState(TypedDict, total=False):
    phase: str
    coordinator_actions: int
    model_calls: int
    candidate_hash: str
    coordinator_contract_repair: dict


@pytest.mark.asyncio
@pytest.mark.parametrize("real_interrupt", [False, True])
async def test_continue_resumes_actual_checkpoint_without_losing_evidence(monkeypatch, real_interrupt):
    observed = []

    async def session(state):
        if real_interrupt:
            answer = interrupt({"kind": "recovery", "message": "Continue?"})
            assert answer["kind"] == "continue"
        elif state["coordinator_actions"] >= 12:
            raise Pause("Local action budget reached")
        observed.append(dict(state))
        return {"phase": "done", "model_calls": state["model_calls"] + 1}

    saver = MemorySaver()
    builder = StateGraph(SavedState)
    builder.add_node("coordinator_session", session)
    builder.add_edge(START, "coordinator_session")
    builder.add_edge("coordinator_session", END)
    graph = builder.compile(checkpointer=saver)
    monkeypatch.setattr(runner, "settings", lambda: SimpleNamespace(environment="test"))
    config = runner.graph_config("retained-run")
    initial = {"phase": "coordinator_session", "coordinator_actions": 12,
               "model_calls": 23, "candidate_hash": "saved-source-identity",
               "coordinator_contract_repair": {"issues": [{"message": "missing count"}]}}
    if real_interrupt:
        await graph.ainvoke(initial, config)
    else:
        with pytest.raises(Pause):
            await graph.ainvoke(initial, config)

    @asynccontextmanager
    async def checkpoint():
        yield saver

    async def one(*_args, **_kwargs):
        return {"id": "retained-run", "status": "queued"}

    async def claim(*_args, **_kwargs):
        return True

    async def update(*_args, **_kwargs):
        return None

    monkeypatch.setattr(runner, "checkpoint_saver", checkpoint)
    monkeypatch.setattr(runner, "build_graph", lambda _saver: graph)
    monkeypatch.setattr(runner.db, "one", one)
    monkeypatch.setattr(runner.db, "rpc", claim)
    monkeypatch.setattr(runner.db, "update", update)
    assert await runner.advance_graph("retained-run", "worker", {"kind": "continue"}) == "done"
    assert len(observed) == 1
    assert observed[0]["coordinator_actions"] == (12 if real_interrupt else 0)
    assert observed[0]["model_calls"] == 23
    assert observed[0]["candidate_hash"] == initial["candidate_hash"]
    assert observed[0]["coordinator_contract_repair"] == initial["coordinator_contract_repair"]
    assert (await graph.aget_state(config)).values["model_calls"] == 24
