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
    model_calls_this_cycle: int
    candidate_hash: str
    coordinator_contract_repair: dict
    original_request: str
    clarified_request: str
    user_updates: list[dict]
    coordinator_history: list[dict]
    cad_history: list[dict]


@pytest.mark.asyncio
@pytest.mark.parametrize("real_interrupt", [False, True])
@pytest.mark.parametrize("role", ["coordinator", "cad"])
async def test_continue_resumes_actual_checkpoint_without_losing_evidence(monkeypatch, real_interrupt, role):
    observed = []

    async def session(state):
        if real_interrupt:
            answer = interrupt({"kind": "recovery", "message": "Continue?"})
            assert answer["kind"] == "continue"
            assert answer["message"] == "Preserve all parts and fix the keyway side cut."
        elif state["coordinator_actions"] >= 12:
            if role == "coordinator" or not state.get("clarified_request"):
                raise Pause("Local action budget reached")
        assert state["model_calls_this_cycle"] == 0
        observed.append(dict(state))
        return {"phase": "done", "model_calls": state["model_calls"] + 1,
                "model_calls_this_cycle": state["model_calls_this_cycle"] + 1}

    saver = MemorySaver()
    builder = StateGraph(SavedState)
    phase = role + "_session"
    history_key = role + "_history"
    builder.add_node(phase, session)
    builder.add_edge(START, phase)
    builder.add_edge(phase, END)
    graph = builder.compile(checkpointer=saver)
    monkeypatch.setattr(runner, "settings", lambda: SimpleNamespace(environment="test"))
    config = runner.graph_config("retained-run")
    initial = {"phase": phase, "coordinator_actions": 12,
               "model_calls": 96, "model_calls_this_cycle": 96, "candidate_hash": "saved-source-identity",
               "coordinator_contract_repair": {"issues": [{"message": "missing count"}]},
               "original_request": "Build the complete pump.",
               history_key: [{"role": "user", "content": "Build the complete pump."}]}
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
    from forma_api.graphs import design_v3
    monkeypatch.setattr(design_v3, "build_graph", lambda _saver: graph)
    monkeypatch.setattr(runner.db, "one", one)
    monkeypatch.setattr(runner.db, "rpc", claim)
    monkeypatch.setattr(runner.db, "update", update)
    guidance = "Preserve all parts and fix the keyway side cut."
    assert await runner.advance_graph("retained-run", "worker", {
        "kind": "continue", "message": guidance}) == "done"
    assert len(observed) == 1
    assert observed[0]["coordinator_actions"] == (0 if not real_interrupt and role == "coordinator" else 12)
    assert observed[0]["model_calls"] == 96
    assert observed[0]["model_calls_this_cycle"] == 0
    assert observed[0]["candidate_hash"] == initial["candidate_hash"]
    assert observed[0]["coordinator_contract_repair"] == initial["coordinator_contract_repair"]
    if real_interrupt:
        # Actual interrupt nodes consume their own payload, without duplicate injection.
        assert "clarified_request" not in observed[0]
        assert observed[0][history_key] == initial[history_key]
    else:
        assert observed[0]["clarified_request"] == "Build the complete pump.\n\nUser continuation: " + guidance
        assert observed[0][history_key][-1] == {"role": "user", "content": guidance}
        assert observed[0]["user_updates"] == [{"kind":"continuation", "message":guidance}]
    final = (await graph.aget_state(config)).values
    assert final["model_calls"] == 97
    assert final["model_calls_this_cycle"] == 1


@pytest.mark.asyncio
async def test_unexpected_transition_error_keeps_provider_text_out_of_run_status(monkeypatch):
    secret = "provider-token-must-not-enter-run-status"
    finished = []

    class FailingGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={}, tasks=[], next=[])

        async def ainvoke(self, _input, _config):
            raise RuntimeError(secret)

    @asynccontextmanager
    async def checkpoint():
        yield object()

    async def one(table, _params, **_kwargs):
        if table == "runs":
            return {"id": "retained-run", "project_id": "project", "owner_id": "owner",
                    "harness_version": 4, "research_mode": True, "status": "queued",
                    "base_revision_id": None, "message": "Design a part", "selected_ids": []}
        if table == "run_candidates":
            return {"candidate_version": 1}
        raise AssertionError(table)

    async def rpc(name, _params):
        assert name == "claim_research_run"
        return 2

    async def finish(_run, _worker, status, message):
        finished.append((status, message))

    from forma_api.graphs import design

    monkeypatch.setattr(runner, "settings", lambda: SimpleNamespace(environment="test"))
    monkeypatch.setattr(runner, "checkpoint_saver", checkpoint)
    monkeypatch.setattr(runner.db, "one", one)
    monkeypatch.setattr(runner.db, "rpc", rpc)
    monkeypatch.setattr(runner.run_service, "finish", finish)
    monkeypatch.setattr(design, "build_graph", lambda _saver: FailingGraph())

    assert await runner.advance_graph("retained-run", "worker") == "done"
    assert finished[0][0] == "paused"
    assert "RuntimeError" in finished[0][1]
    assert "preserved" in finished[0][1]
    assert secret not in finished[0][1]
