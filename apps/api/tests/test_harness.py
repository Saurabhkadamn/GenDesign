import json
from copy import deepcopy

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from forma_api import engine
from forma_api.contracts import AppSettings, Snapshot
from forma_api.execution import digest, identity
from forma_api.graphs import design
from forma_api.harness import acceptance_context, component_snapshot, probe_context, recovery_key
from forma_api.tools import ApplyChanges, json_schema, model_tools, provider_tools, source_patch, updated_manifest


def workspace():
    return Snapshot.model_validate({"files": {
        "parts/base.py": "def build(parameters, dependencies):\n    return parameters['length']",
        "parts/lid.py": "def build(parameters, dependencies):\n    return None",
    }, "manifest": {"components": [
        {"id": "base", "name": "Base", "kind": "solid", "source": "parts/base.py", "parameters": {"length": 10}},
        {"id": "lid", "name": "Lid", "kind": "solid", "source": "parts/lid.py"}],
        "rootComponentId": "base"}}).model_dump()


def test_provider_schema_exposes_runtime_constraints_and_gemini_retains_instructions():
    tools = model_tools("cad")
    raw = next(t["function"]["parameters"] for t in tools if t["function"]["name"] == "apply_changes")
    component = raw["properties"]["manifest"]["anyOf"][0]["properties"]["components"]["items"]
    assert component["properties"]["color"]["pattern"] == r"^#[0-9a-fA-F]{6}$"
    density = component["properties"]["material"]["anyOf"][0]["properties"]["densityKgM3"]
    assert density["anyOf"][0]["exclusiveMinimum"] == 0
    assert {"type": "null"} in density["anyOf"]
    assert provider_tools({"model_id": "nvidia/Nemotron-3-Ultra-550b-a55b"}, tools) == tools
    gemini = provider_tools({"model_id": "google/gemini-test"}, tools)
    portable = next(t["function"]["parameters"] for t in gemini if t["function"]["name"] == "apply_changes")
    props = portable["properties"]["manifest"]["properties"]["components"]["items"]["properties"]
    assert "#RRGGBB" in props["color"]["description"]
    text = props["material"]["properties"]["densityKgM3"]["description"]
    assert "greater than" in text and "null" in text
    assert "anyOf" not in json.dumps(portable)
    # Adaptation never erases the canonical model-facing contract in place.
    assert "anyOf" in json.dumps(raw)
    assert set(json_schema({"type": "object", "properties": {"title": {"type": "string"},
        "default": {"type": "number"}}})["properties"]) == {"title", "default"}


@pytest.mark.asyncio
async def test_large_rejected_proposal_is_selectively_readable_without_saved_edits(monkeypatch):
    original = workspace()
    source = "def build(parameters, dependencies):\n    return None\n" + "# unexecuted proposal\n" * 500
    arguments = {"files": {"parts/proposed.py": source}, "manifest": {"components": [{
        "id": "proposed", "name": "Proposed", "kind": "solid", "source": "parts/proposed.py", "color": "0x999999"}]}}
    call = {"id": "bad-colour", "name": "apply_changes", "input": arguments}
    async def load(_): return deepcopy(original)
    async def turn(state, **kwargs):
        history = [{"role": "assistant", "tool_calls": [{"id": call["id"], "type": "function", "function": {
            "name": call["name"], "arguments": json.dumps(call["input"])}}]}]
        return deepcopy(call), history, {"model_calls": state.get("model_calls", 0) + 1}
    monkeypatch.setattr(design.run_service, "load_candidate", load)
    monkeypatch.setattr(design, "agent_tool_turn", turn)
    state = {"run_id": "run", "original_request": "Design this assembly"}
    rejected = await design.cad_session(state)
    assert rejected["rejected_files"] == {"parts/proposed.py": source}
    assert source not in json.dumps(rejected["cad_history"])
    state.update(rejected)
    call.update(id="read-proposal", name="read_file", input={"path": "parts/proposed.py", "version": "rejected"})
    result = await design.cad_session(state)
    feedback = json.loads(result["cad_history"][-1]["content"])
    assert feedback["content"] == source and not feedback["saved"]
    assert "validation" not in result and "candidate_hash" not in result
    assert original == workspace()


def test_atomic_upsert_and_exact_patch_preserve_unrelated_state():
    original = workspace()
    before = deepcopy(original)
    change = ApplyChanges.model_validate({"mergeById": True, "manifest": {"components": [
        {**original["manifest"]["components"][0], "parameters": {"length": 12}}]},
        "patches": [{"path": "parts/base.py", "oldText": "return parameters['length']", "newText": "return parameters['length'] * 2"}]})
    change = source_patch(original["files"], change)
    result = Snapshot.model_validate({"files": {**original["files"], **change.files},
                                      "manifest": updated_manifest(original["manifest"], change)}).model_dump()
    assert result["manifest"]["components"][1] == original["manifest"]["components"][1]
    assert result["files"]["parts/lid.py"] == original["files"]["parts/lid.py"]
    assert result["manifest"]["rootComponentId"] == "base"
    assert result["manifest"]["components"][0]["parameters"] == {"length": 12}
    assert original == before
    with pytest.raises(ValueError, match="exactly once"):
        source_patch(original["files"], ApplyChanges(patches=[{"path": "parts/base.py", "oldText": "absent", "newText": "oops"}]))
    bad = ApplyChanges(mergeById=True, manifest={"rootComponentId": "not_registered"})
    with pytest.raises(ValueError, match="root"):
        Snapshot.model_validate({"files": original["files"], "manifest": updated_manifest(original["manifest"], bad)})
    assert original == before


@pytest.mark.asyncio
async def test_edit_preview_and_engineering_deletions_are_atomic():
    original = workspace()
    cp = {"role": "cad", "snapshot": deepcopy(original), "validated": {"existing": True}}
    result = await engine.execute_tool({}, cp, {"name": "apply_changes", "input": {
        "dryRun": True, "files": {"parts/lid.py": "def build(parameters, dependencies):\n    return 1"}}}, AppSettings(), "worker")
    assert result["previewOnly"] and cp["snapshot"] == original and cp["validated"] == {"existing": True}
    cp["role"] = "engineering"
    with pytest.raises(ValueError, match="Engineering can edit only"):
        await engine.execute_tool({}, cp, {"name": "apply_changes", "input": {"deletePaths": ["parts/base.py"]}}, AppSettings(), "worker")
    assert cp["snapshot"] == original


def test_probe_scope_and_acceptance_never_promote_stale_evidence():
    snapshot = workspace()
    scoped = component_snapshot(snapshot, "lid")
    assert [c["id"] for c in scoped["manifest"]["components"]] == ["lid"]
    assert scoped["manifest"]["instances"] == [] and snapshot["manifest"]["rootComponentId"] == "base"
    state = {"component_probes": {"lid": {"ok": True, "identity": identity(scoped, []), "requirements": []}}}
    assert probe_context(state, snapshot)[0]["currentSource"]
    snapshot["files"]["parts/lid.py"] += "\n# edited"
    assert not probe_context(state, snapshot)[0]["currentSource"]
    requirement = {"id": "size", "kind": "dimensions", "dimensions": [10, 20, 5]}
    state.update(requirements=[requirement], validation={"identity": identity(snapshot, [requirement]), "report": {
        "requirements": [{"id": "size", "status": "passed", "evidence": {"measured": [10, 20, 5]}}]}})
    assert acceptance_context(state, snapshot)["requirements"][0]["status"] == "passed"
    snapshot["files"]["parts/lid.py"] += "\n# another edit"
    assert acceptance_context(state, snapshot)["requirements"][0]["status"] == "unverified"


@pytest.mark.asyncio
async def test_probe_uses_independent_pipeline_without_replacing_project_validation(monkeypatch):
    snapshot = workspace()
    state = {"run_id": "run", "original_request": "Design an assembly", "model_calls": 9, "attempts": 3,
             "validation": {"old": True}, "requirements": [], "candidate_hash": digest(snapshot)}
    original = deepcopy(state)
    async def row(_): return {"id": "11111111-1111-1111-1111-111111111111"}
    async def settings(): return AppSettings()
    async def op(_run, _key, _kind, callback, **_): return await callback()
    async def build(_run, cp, _limits, _key):
        assert cp["snapshot"]["manifest"]["rootComponentId"] == "lid"
        cp["attempts"] += 1
        cp["validated"] = {"identity": identity(cp["snapshot"], []), "report": {"part": True}}
        return {"ok": True, "inspection": {"solidCount": 1}}
    async def event(*_, **__): pass
    monkeypatch.setattr(design, "run_row", row)
    monkeypatch.setattr(design, "app_settings", settings)
    monkeypatch.setattr(design, "operation", op)
    monkeypatch.setattr(design, "build_candidate", build)
    monkeypatch.setattr(design.repo, "event", event)
    result = await design.probe_component(state, snapshot, {"id": "probe"}, [], "lid")
    assert result["attempts"] == 4 and result["model_calls"] == 9
    assert result["component_probes"]["lid"]["scope"] == "component_only"
    assert not {"validation", "requirements", "candidate_hash", "build_result"}.intersection(result)
    assert state == original


@pytest.mark.asyncio
async def test_automatic_replan_survives_checkpoint_and_preserves_total_usage(monkeypatch):
    snapshot = workspace()
    plan = {"diagnosis": "Root was not registered", "nextTask": "Upsert the missing assembly definition",
            "changesFromFailedApproach": "Send only the missing definition and source", "verification": "Validate staging then build"}
    async def load(_): return deepcopy(snapshot)
    async def settings(): return AppSettings()
    async def event(*_, **__): pass
    async def turn(state, **kwargs):
        assert kwargs["prompt_role"] == "recovery"
        assert kwargs["context"]["request"] == "Keep exact 34.000 +/-0.010"
        return {"name": "submit_recovery", "input": plan}, [], {"model_calls": state["model_calls"] + 1}
    async def seed(_): return {"phase": "cad_session"}
    async def failed(_): return {"phase": "cad_recovery", "final_message": "Root is missing"}
    monkeypatch.setattr(design.run_service, "load_candidate", load)
    monkeypatch.setattr(design, "app_settings", settings)
    monkeypatch.setattr(design.repo, "event", event)
    monkeypatch.setattr(design, "agent_tool_turn", turn)
    monkeypatch.setattr(design, "coordinator", seed)
    monkeypatch.setattr(design, "coordinator_session", seed)
    monkeypatch.setattr(design, "cad_session", failed)
    saver = InMemorySaver()
    graph = design.build_graph(saver)
    config = {"configurable": {"thread_id": "automatic-replan"}}
    initial = {"run_id": "run", "original_request": "Keep exact 34.000 +/-0.010", "candidate_hash": digest(snapshot),
               "model_calls": 8, "attempts": 4, "repairs": 3, "cad_invalid_tool_attempts": 3}
    await graph.ainvoke(initial, config)
    await graph.ainvoke(None, config)
    await graph.ainvoke(None, config)
    # New process/graph sees the pending automatic recovery, not a human-input interrupt.
    graph = design.build_graph(saver)
    result = await graph.ainvoke(None, config)
    assert result["phase"] == "cad_session" and not result.get("__interrupt__")
    assert result["model_calls"] == 9 and result["attempts"] == 4
    assert result["candidate_hash"] == initial["candidate_hash"] and result["repairs"] == 0
    assert result["recovery_plan_count"] == 1
    # Repeated planning of unchanged failures is bounded; source remains available.
    limited = await design.cad_replan({**result, "recovery_counts": {recovery_key(result, snapshot): 2}})
    assert limited["phase"] == "cad_recovery" and "model_calls" not in limited
