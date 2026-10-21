import json
from copy import deepcopy

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from forma_api.contracts import AppSettings, Snapshot
from forma_api.graphs import design
from forma_api.graphs.state import AgentState
from forma_api.harness import (recovery_source_context, record_observation_action, recovery_key,
                               focused_recovery_paths, observation_context, observation_epoch,
                               record_observation_decision)
from forma_api.agent_memory import memory_context, fit_dialogue, estimate_tokens


def test_active_build_failure_is_the_authoritative_repair_target():
    failure = design.active_build_failure({"ok": False, "error": {
        "category": "geometry", "location": {"file": "parts/drive_gear_shaft.py", "line": 49},
        "diagnostic": "No rear face selected"}})
    assert failure["file"] == "parts/drive_gear_shaft.py"
    assert failure["line"] == 49
    assert "newest failed build operation" in failure["instruction"]
    assert design.active_build_failure({"ok": True, "error": {"file": "parts/old.py"}}) is None
    assert "activeFailure" in design.system_prompt("cad")


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


def test_recovery_source_selection_prioritizes_current_failure_over_old_read_inventory():
    snapshot = workspace(14)
    paths = list(snapshot["files"])
    state = {"build_result": {"error": {"location": {"file": paths[8]}}},
             "last_read_path": paths[7],
             "cad_history": [read_result(path, source) for path, source in snapshot["files"].items()],
             "recovery_source_paths": paths}
    selected = focused_recovery_paths(state, snapshot)
    assert selected[:2] == [paths[8], paths[7]]
    context = recovery_source_context(state, snapshot, paths=selected, max_files=4, max_bytes=12_000)
    assert paths[8] in context["files"]
    assert len(context["files"]) <= 4
    assert len(context["files"]) < len(paths)


def test_large_recovery_context_fits_budget_while_preserving_request_and_current_failure():
    files = {f"parts/p{i}.py": "# saved source " + ("x" * 1800) for i in range(14)}
    files["assemblies/pump.py"] = "# assembly source " + ("y" * 1800)
    components = [{"id": f"p{i}", "name": f"Part {i}", "kind": "solid", "source": f"parts/p{i}.py",
                   "parameters": {"dimension": "34.000 +/-0.010 mm"}} for i in range(14)]
    components.append({"id": "root", "name": "Pump", "kind": "assembly", "source": "assemblies/pump.py"})
    instances = [{"id": f"item-{i}", "definitionId": f"p{i % 14}", "name": f"Occurrence {i}",
                  "frame": {"position": [i, 0, 0], "rotation": [0, 0, 0]}} for i in range(43)]
    snapshot = Snapshot.model_validate({"files": files, "manifest": {
        "components": components, "instances": instances, "rootComponentId": "root"}}).model_dump()
    request = "Exact engineering request and requirements. " + "R" * 9000
    updates = [{"kind": "continuation", "message": f"Historical follow-up {i}: " + "U" * 900}
               for i in range(8)]
    state = {"original_request": request, "user_updates": updates,
        "build_result": {"error": {"location": {"file": "parts/p3.py"}, "diagnostic": "fillet failed " * 400}},
        "working_memory": {"outcomes": {str(i): {"tool": "build", "candidateHash": str(i),
            "diagnostic": "D" * 4000, "guidance": "G" * 4000} for i in range(12)}},
        "requirements": [{"id": f"r{i}", "description": "Exact requirement " + "Q" * 120}
                         for i in range(24)]}
    selected = focused_recovery_paths(state, snapshot)
    source = recovery_source_context(state, snapshot, paths=selected, max_bytes=12_000, max_files=4)
    context = {
        "currentCandidate": design.current_candidate_facts(snapshot),
        "request": request,
        "humanUpdates": updates,
        "acceptanceLedger": design.acceptance_context(state, snapshot),
        "componentTests": design.compact_probe_context(state, snapshot),
        "recoverySourceContext": source,
        "workspace": {"manifest": design.compact_manifest_context(snapshot["manifest"]),
                      "files": sorted(files), "candidateHash": design.digest(snapshot)},
        "lastBuild": design.compact_build_context(state["build_result"]),
        "workspaceStatus": design.compact_workspace_status(design.workspace_status(snapshot)),
        "workingMemory": memory_context(state, design.digest(snapshot), max_outcomes=8, text_limit=1500),
    }
    tools = [tool for tool in design.model_tools("cad", working_memory=True)
             if tool["function"]["name"] in design.CAD_SESSION_TOOL_NAMES]
    messages = [{"role": "system", "content": design.system_prompt("cad")},
        {"role": "system", "content": "Current private design context: " + json.dumps(context)},
        {"role": "user", "content": request}, {"role": "assistant", "content": "old tool transcript " * 30000}]
    fitted = fit_dialogue(messages, tools, 48_000, design.protocol_safe_history)
    assert estimate_tokens([*fitted, tools]) <= 48_000
    assert request in fitted[1]["content"]
    assert "parts/p3.py" in fitted[1]["content"]
    assert "Historical follow-up 7" in fitted[1]["content"]


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


def test_observation_only_budget_counts_model_decisions_and_forces_progress():
    snapshot = workspace(2)
    state = {"original_request": "Preserve the requested tolerances",
             "cad_observation_progress": {"epoch": observation_epoch(
                 {"original_request": "Preserve the requested tolerances"}, snapshot),
                 "seen": {}, "repeats": 0, "observationOnlyDecisions": 5}}

    # A provider can return a batch of many file reads in one generation.
    progress = record_observation_decision(state["cad_observation_progress"], batch_complete=False)
    assert progress["observationOnlyDecisions"] == 5
    progress = record_observation_decision(progress, batch_complete=True)
    state["cad_observation_progress"] = progress
    assert observation_context(state, snapshot)["observationOnlyDecisions"] == 6

    tools = [{"function": {"name": name}} for name in (
        "read_file", "search_files", "inspect_geometry", "diagnose_workspace",
        "read_working_memory", "apply_changes", "test_component", "build",
        "request_engineering", "ask_user",
    )]
    bounded = design.bound_cad_observation_tools(tools, 6)
    assert [tool["function"]["name"] for tool in bounded] == [
        "apply_changes", "test_component", "build", "request_engineering", "ask_user"]
    assert observation_context(state, workspace(3))["observationOnlyDecisions"] == 0


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
    initial = {"run_id": "run", "original_request": "Keep exact requirements",
               "clarified_request": "Keep exact requirements\n\nUser continuation: 14 parts are missing",
               "user_updates": [{"kind": "continuation", "message": "14 parts are missing; earlier repair targeted parts/p0.py"}],
               "model_calls": 10,
               "build_result": {"ok": False, "error": {"category": "geometry",
                   "location": {"file": "parts/p1.py", "line": 12}, "diagnostic": "Current edge fillet failed"}},
               "cad_history": [{"role": "assistant", "content": "obsolete failed proposal" * 1000}, *reads],
               "cad_observation_progress": {"seen": {}, "repeats": 20, "repeatDecisions": 3}}
    async def load(_): return deepcopy(snapshot)
    async def settings(): return AppSettings()
    async def event(*_, **__): pass
    async def turn(state, **kwargs):
        assert list(kwargs["context"]["recoverySourceContext"]["files"]) == ["parts/p1.py"]
        assert kwargs["context"]["request"] == "Keep exact requirements"
        assert kwargs["context"]["humanUpdates"] == initial["user_updates"]
        assert kwargs["context"]["activeFailure"]["file"] == "parts/p1.py"
        assert kwargs["context"]["currentCandidate"]["componentCount"] == 2
        assert kwargs["context"]["currentCandidate"]["instanceCount"] == 0
        assert "savedSourcePaths" not in kwargs["context"]["currentCandidate"]
        if kwargs["prompt_role"] == "recovery":
            return {"name": "submit_recovery", "input": {"diagnosis": "Sources exist; root missing",
                "nextTask": "Test a saved component", "changesFromFailedApproach": "Use retained source",
                "verification": "Independent part test"}}, [], {"model_calls": 11}
        assert "obsolete failed proposal" not in json.dumps(kwargs["history"])
        assert "Current saved candidate" in json.dumps(kwargs["history"])
        assert "AUTHORITATIVE CURRENT BUILD FAILURE" in kwargs["history"][-1]["content"]
        assert "parts/p1.py" in kwargs["history"][-1]["content"]
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
    assert first["recovery_source_paths"] == ["parts/p1.py"]
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
             "model_calls": 27, "recovery_plan_count": 3,
             "recovery_plan": {"nextTask": "Obsolete plan"},
             "cad_observation_progress": {"seen": {"source": {}}, "repeats": 15, "repeatDecisions": 3},
             "cad_history": [read_result(p, s) for p, s in snapshot["files"].items()]}
    async def load(_): return deepcopy(snapshot)
    state["recovery_counts"] = {recovery_key(state, snapshot): 2, "unrelated-failure": 1}
    monkeypatch.setattr(design.run_service, "load_candidate", load)
    monkeypatch.setattr(design, "interrupt", lambda _: {"kind": "continue", "message": "Use the saved source and test"})
    result = await design.cad_recovery(state)
    assert set(result["recovery_source_paths"]) == set(snapshot["files"])
    assert result["recovery_plan"] == {} and result["cad_observation_progress"]["repeatDecisions"] == 0
    assert result["cad_observation_progress"]["seen"] == state["cad_observation_progress"]["seen"]
    merged = {**state, **result}
    assert merged["model_calls"] == 27 and merged["recovery_plan_count"] == 0
    assert merged["recovery_counts"] == {"unrelated-failure": 1}
    assert "Use the saved source and test" in merged["clarified_request"]


@pytest.mark.asyncio
async def test_generic_continue_keeps_recovery_allowance_bounded(monkeypatch):
    snapshot = workspace(1)
    state = {"run_id": "run", "original_request": "Keep exact requirements",
             "final_message": "Read cycle",
             "model_calls": 27, "recovery_plan_count": 3,
             "cad_observation_progress": {"seen": {}, "repeats": 8, "repeatDecisions": 3}}
    async def load(_): return deepcopy(snapshot)
    state["recovery_counts"] = {recovery_key(state, snapshot): 2}
    monkeypatch.setattr(design.run_service, "load_candidate", load)
    monkeypatch.setattr(design, "interrupt", lambda _: {"kind": "continue", "message": "Continue."})
    result = await design.cad_recovery(state)
    merged = {**state, **result}
    assert merged["model_calls"] == 27 and merged["recovery_plan_count"] == 3
    assert merged["recovery_counts"] == state["recovery_counts"]
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
