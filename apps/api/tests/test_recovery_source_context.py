import json
from copy import deepcopy

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from forma_api.contracts import AppSettings, Snapshot
from forma_api.graphs import design
from forma_api.graphs.state import AgentState
from forma_api.harness import recovery_source_context, record_observation_action, recovery_key


def workspace(count=14):
    files = {f"parts/p{i}.py": f"def build(parameters, dependencies):\n    return {i}\n" for i in range(count)}
    return Snapshot.model_validate({"files": files, "manifest": {"components": [
        {"id": f"p{i}", "name": f"Part {i}", "kind": "solid", "source": f"parts/p{i}.py"}
        for i in range(count)]}}).model_dump()


def read_result(path, source, **extra):
    return {"role": "tool", "tool_call_id": path, "content": json.dumps({
        "ok": True, "path": path, "saved": True, "content": source, **extra})}


def test_recovery_source_context_preserves_whole_current_reads_with_explicit_bounds():
    snapshot = workspace(4)
    paths = list(snapshot["files"])
    history = [read_result(p, snapshot["files"][p]) for p in paths]
    history += [read_result(paths[0], "stale source"),
                read_result("parts/rejected.py", "unaccepted source", saved=False),
                {"role": "tool", "content": "not JSON"}]
    state = {"cad_history": history}
    context = recovery_source_context(state, snapshot, max_files=2)
    assert len(context["files"]) == 2 and len(context["omittedPaths"]) == 2
    assert all(snapshot["files"][p] == s for p, s in context["files"].items())
    assert "parts/rejected.py" not in context["files"]
    bounded = recovery_source_context(state, snapshot, max_bytes=1)
    assert bounded["files"] == {} and len(bounded["omittedPaths"]) == 4
    changed = deepcopy(snapshot)
    changed["files"][paths[0]] = "def build(parameters, dependencies):\n    return 9"
    assert paths[0] not in recovery_source_context(state, changed)["files"]
    # Explicitly remembered paths fetch the current saved source, not an old transcript.
    refreshed = recovery_source_context({}, changed, paths=[paths[0]])
    assert refreshed["files"][paths[0]] == changed["files"][paths[0]]
    assert "not build or geometry evidence" in context["instruction"]


def test_one_fourteen_read_response_counts_as_one_repeated_model_decision():
    snapshot = workspace()
    state = {"original_request": "Keep every specified part"}
    for p in snapshot["files"]:
        state["cad_observation_progress"] = record_observation_action(
            state, snapshot, "read_file", {"path": p, "version": "saved"}, decision=1)
    assert state["cad_observation_progress"]["repeatDecisions"] == 0
    for decision in [2, 3, 4]:
        for p in snapshot["files"]:
            state["cad_observation_progress"] = record_observation_action(
                state, snapshot, "read_file", {"path": p, "version": "saved"}, decision=decision)
        assert state["cad_observation_progress"]["repeatDecisions"] == decision - 1
    assert state["cad_observation_progress"]["repeats"] == 42


@pytest.mark.asyncio
async def test_replayed_read_batch_reaches_next_model_decision_without_premature_recovery(monkeypatch):
    snapshot = workspace()
    state = {"run_id": "run", "original_request": "Keep every specified part", "model_calls": 7}
    actions = []
    for p in snapshot["files"]:
        args = {"path": p, "version": "saved"}
        state["cad_observation_progress"] = record_observation_action(state, snapshot, "read_file", args, decision=1)
        call = {"id": p, "name": "read_file", "input": args}
        actions.append({"call": call, "message": {"role": "assistant", "tool_calls": [{
            "id": p, "type": "function", "function": {"name": "read_file", "arguments": json.dumps(args)}}]}})
    state["cad_pending_actions"] = actions
    async def load(_): return deepcopy(snapshot)
    async def no_model(*_, **__): raise AssertionError("No new model call while draining reads")
    monkeypatch.setattr(design.run_service, "load_candidate", load)
    monkeypatch.setattr(design, "agent_tool_turn", no_model)
    for _ in actions:
        result = await design.cad_session(state)
        assert result["phase"] == "cad_session"
        state.update(result)
    assert state["cad_pending_actions"] == [] and state["model_calls"] == 7
    assert state["cad_observation_progress"]["repeatDecisions"] == 1
    assert len(recovery_source_context(state, snapshot)["files"]) == 14


@pytest.mark.asyncio
async def test_recovery_source_handoff_survives_checkpoint_and_reaches_cad(monkeypatch):
    snapshot = workspace(2)
    reads = [read_result(p, s) for p, s in snapshot["files"].items()]
    initial = {"run_id": "run", "original_request": "Keep exact requirements", "model_calls": 10,
               "cad_history": [{"role": "assistant", "content": "obsolete failed proposal" * 1000}, *reads],
               "cad_observation_progress": {"seen": {}, "repeats": 20, "repeatDecisions": 3}}
    async def load(_): return deepcopy(snapshot)
    async def settings(): return AppSettings()
    async def event(*_, **__): pass
    async def turn(state, **kwargs):
        assert kwargs["context"]["recoverySourceContext"]["files"] == snapshot["files"]
        if kwargs["prompt_role"] == "recovery":
            return {"name": "submit_recovery", "input": {"diagnosis": "Sources exist; root missing",
                "nextTask": "Test a saved component", "changesFromFailedApproach": "Use retained source",
                "verification": "Independent part test"}}, [], {"model_calls": 11}
        assert "obsolete failed proposal" not in json.dumps(kwargs["history"])
        return {"id": "diagnose", "name": "diagnose_workspace", "input": {}}, [], {"model_calls": 12}
    monkeypatch.setattr(design.run_service, "load_candidate", load)
    monkeypatch.setattr(design, "app_settings", settings)
    monkeypatch.setattr(design.repo, "event", event)
    monkeypatch.setattr(design, "agent_tool_turn", turn)
    def graph(saver):
        g = StateGraph(AgentState)
        g.add_node("replan", design.cad_replan)
        g.add_node("cad", design.cad_session)
        g.add_edge(START, "replan"); g.add_edge("replan", "cad"); g.add_edge("cad", END)
        return g.compile(checkpointer=saver, interrupt_after="*")
    saver = InMemorySaver(); config = {"configurable": {"thread_id": "source-handoff"}}
    first = await graph(saver).ainvoke(initial, config)
    assert set(first["recovery_source_paths"]) == set(snapshot["files"])
    assert first["cad_observation_progress"]["repeatDecisions"] == 0
    final = await graph(saver).ainvoke(None, config)
    assert final["model_calls"] == 12 and final["recovery_plan_count"] == 1
    # The repair window remains bounded for unchanged candidates/failures.
    limited = await design.cad_replan({**final, "recovery_counts": {recovery_key(final, snapshot): 2}})
    assert limited["phase"] == "cad_recovery" and "model_calls" not in limited


@pytest.mark.asyncio
async def test_specific_continue_retains_source_and_renews_only_recovery_allowance(monkeypatch):
    snapshot = workspace(2)
    state = {"run_id": "run", "original_request": "Keep exact requirements", "final_message": "Read cycle",
             "model_calls": 27, "recovery_plan_count": 3, "recovery_counts": {"unchanged": 2},
             "recovery_plan": {"nextTask": "Obsolete plan"},
             "cad_observation_progress": {"seen": {"source": {}}, "repeats": 15, "repeatDecisions": 3},
             "cad_history": [read_result(p, s) for p, s in snapshot["files"].items()]}
    async def load(_): return deepcopy(snapshot)
    monkeypatch.setattr(design.run_service, "load_candidate", load)
    monkeypatch.setattr(design, "interrupt", lambda _: {"kind": "continue", "message": "Use the saved source and test"})
    result = await design.cad_recovery(state)
    assert set(result["recovery_source_paths"]) == set(snapshot["files"])
    assert result["recovery_plan"] == {} and result["cad_observation_progress"]["repeatDecisions"] == 0
    assert result["cad_observation_progress"]["seen"] == state["cad_observation_progress"]["seen"]
    merged = {**state, **result}
    assert merged["model_calls"] == 27 and merged["recovery_plan_count"] == 0
    assert merged["recovery_counts"] == state["recovery_counts"]
    assert "Use the saved source and test" in merged["clarified_request"]


@pytest.mark.asyncio
async def test_generic_continue_keeps_recovery_allowance_bounded(monkeypatch):
    snapshot = workspace(1)
    state = {"run_id": "run", "original_request": "Keep exact requirements",
             "final_message": "Read cycle",
             "model_calls": 27, "recovery_plan_count": 3,
             "cad_observation_progress": {"seen": {}, "repeats": 8, "repeatDecisions": 3}}
    async def load(_): return deepcopy(snapshot)
    monkeypatch.setattr(design.run_service, "load_candidate", load)
    monkeypatch.setattr(design, "interrupt", lambda _: {"kind": "continue", "message": "Continue."})
    result = await design.cad_recovery(state)
    merged = {**state, **result}
    assert merged["model_calls"] == 27 and merged["recovery_plan_count"] == 3
    assert merged["clarified_request"].endswith("User continuation: Continue.")


def test_fresh_recovery_window_allows_one_inspection_pass_across_three_batches():
    snapshot = workspace()
    state = {"original_request": "Preserve every part"}
    paths = list(snapshot["files"])
    for path in paths:
        state["cad_observation_progress"] = record_observation_action(state, snapshot,
            "read_file", {"path": path, "version": "saved"}, decision=1)
    receipts = deepcopy(state["cad_observation_progress"]["seen"])
    # Recovery gives a new hypothesis one inspection pass, even though durable
    # receipts still record reads completed under earlier, discarded dialogue.
    state["cad_observation_progress"].update(windowSeen={}, repeats=0,
        repeatDecisions=0, lastRepeatedDecision=None)
    for decision, batch in enumerate([paths[:5], paths[5:9], paths[9:]], start=2):
        for path in batch:
            state["cad_observation_progress"] = record_observation_action(state, snapshot,
                "read_file", {"path": path, "version": "saved"}, decision=decision)
        assert state["cad_observation_progress"]["repeatDecisions"] == 0
    assert state["cad_observation_progress"]["seen"] == receipts
    for decision in [5, 6, 7]:
        state["cad_observation_progress"] = record_observation_action(state, snapshot,
            "read_file", {"path": paths[0], "version": "saved"}, decision=decision)
    assert state["cad_observation_progress"]["repeatDecisions"] == 3
