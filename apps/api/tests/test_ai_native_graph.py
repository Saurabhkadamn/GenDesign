import json

import pytest

from forma_api.graphs import design


def state(**updates):
    value = {
        "run_id": "00000000-0000-0000-0000-000000000001",
        "project_id": "00000000-0000-0000-0000-000000000002",
        "owner_id": "00000000-0000-0000-0000-000000000003",
        "base_revision_id": None,
        "original_request": "Create a block.",
        "model_calls": 0,
        "search_count": 0,
        "cad_history": [{"role": "user", "content": "Create a block."}],
        "requirements": [],
    }
    value.update(updates)
    return value


@pytest.fixture
def graph_mocks(monkeypatch):
    saved = {}

    async def run_row(_state):
        return {"id": _state["run_id"]}

    async def settings():
        return design.AppSettings()

    async def configuration(_role):
        return {"model_id": "test/model", "version": 1, "api_key": "unused"}

    async def execute(_run, _key, _kind, callback, **_kwargs):
        return await callback()

    async def insert(*_args, **_kwargs):
        return []

    async def event(*_args, **_kwargs):
        return None

    async def load_candidate(_run_id):
        return saved.get("candidate", {"manifest": {
            "schemaVersion": 1, "units": "mm", "components": [], "instances": [],
            "rootComponentId": None, "references": [], "joints": [],
            "configurations": [], "featureOperations": [],
        }, "files": {}})

    async def save_candidate(_run_id, candidate, candidate_hash):
        saved["candidate"] = candidate
        saved["hash"] = candidate_hash

    monkeypatch.setattr(design, "run_row", run_row)
    monkeypatch.setattr(design, "app_settings", settings)
    monkeypatch.setattr(design.models, "configuration", configuration)
    monkeypatch.setattr(design, "operation", execute)
    monkeypatch.setattr(design.db, "insert", insert)
    monkeypatch.setattr(design.repo, "event", event)
    monkeypatch.setattr(design.run_service, "load_candidate", load_candidate)
    monkeypatch.setattr(design.run_service, "save_candidate", save_candidate)
    return saved


@pytest.mark.asyncio
async def test_cad_session_applies_one_incremental_source_patch(monkeypatch, graph_mocks):
    manifest = {
        "schemaVersion": 1, "units": "mm",
        "components": [{"id": "block", "name": "Block", "source": "parts/block.py",
            "kind": "solid", "dependencies": [], "parameters": {}, "color": "#b9c4ad"}],
        "instances": [], "rootComponentId": "block", "references": [], "joints": [],
        "configurations": [], "featureOperations": [],
    }
    arguments = {"files": [{"path": "parts/block.py",
        "content": "import cadquery as cq\ndef build(p,d): return cq.Workplane('XY').box(10,10,10)"}],
        "manifest": manifest, "deletePaths": []}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "patch-1", "type": "function", "function": {
                "name": "apply_changes", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "patch-1", "name": "apply_changes", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.cad_session(state())
    assert result["phase"] == "cad_session"
    assert graph_mocks["candidate"]["manifest"]["rootComponentId"] == "block"
    assert "parts/block.py" in graph_mocks["candidate"]["files"]
    assert result["validation"] == {}


@pytest.mark.asyncio
async def test_cad_session_normalizes_model_root_sentinels(monkeypatch, graph_mocks):
    manifest = {
        "schemaVersion": 1, "units": "mm",
        "components": [
            {"id": "assembly", "name": "Assembly", "source": "assemblies/main.py",
             "kind": "assembly", "dependencies": ["block"], "parameters": {}, "color": "#b9c4ad"},
            {"id": "block", "name": "Block", "source": "parts/block.py",
             "kind": "solid", "dependencies": [], "parameters": {}, "color": "#b9c4ad"},
        ],
        "instances": [
            {"id": "assembly_inst", "definitionId": "assembly", "parentId": "__root__",
             "name": "Assembly", "frame": {"position": [0, 0, 0], "rotation": [0, 0, 0]}},
            {"id": "block_inst", "definitionId": "block", "parentId": "assembly_inst",
             "name": "Block", "frame": {"position": [0, 0, 0], "rotation": [0, 0, 0]}},
        ],
        "rootComponentId": "assembly", "references": [], "joints": [],
        "configurations": [], "featureOperations": [],
    }
    arguments = {"files": {
        "parts/block.py": "import cadquery as cq\ndef build(p,d): return cq.Workplane('XY').box(10,10,10)",
        "assemblies/main.py": "def build(p,d): return d['block']",
    }, "manifest": manifest, "deletePaths": []}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "patch-root", "type": "function", "function": {
                "name": "apply_changes", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "patch-root", "name": "apply_changes", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.cad_session(state())
    stored = {item["id"]: item for item in graph_mocks["candidate"]["manifest"]["instances"]}
    assert stored["assembly_inst"]["parentId"] is None
    assert stored["block_inst"]["parentId"] == "assembly_inst"
    assert '"hierarchyNormalized": true' in result["cad_history"][-1]["content"]


@pytest.mark.asyncio
async def test_cad_session_enforces_calculation_role_boundary(monkeypatch, graph_mocks):
    arguments = {"files": {"calculations/analysis.py": "def calculate(): return {}"},
                 "manifest": None, "deletePaths": []}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "bad-cad-edit", "type": "function", "function": {
                "name": "apply_changes", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "bad-cad-edit", "name": "apply_changes", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.cad_session(state())
    assert "role_boundary" in result["cad_history"][-1]["content"]
    assert "candidate" not in graph_mocks


@pytest.mark.asyncio
async def test_cad_session_does_not_repeat_engineering_for_unchanged_workspace(monkeypatch, graph_mocks):
    arguments = {"task": "Rerun the same calculation."}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "repeat-engineering", "type": "function", "function": {
                "name": "request_engineering", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "repeat-engineering", "name": "request_engineering", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    empty = await design.run_service.load_candidate(state()["run_id"])
    result = await design.cad_session(state(
        engineering_summary="Already analyzed.", engineering_candidate_hash=design.digest(empty)))
    assert result["phase"] == "cad_session"
    assert "unchanged_engineering_request" in result["cad_history"][-1]["content"]


@pytest.mark.asyncio
async def test_reviewer_returns_measured_defect_to_cad(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = {
        "manifest": {"schemaVersion": 1, "units": "mm", "components": [], "instances": [],
            "rootComponentId": None, "references": [], "joints": [],
            "configurations": [], "featureOperations": []},
        "files": {},
    }
    review = {"summary": "A gear intersects its housing.", "action": "repair", "findings": [{
        "id": "gear_overlap", "statement": "The output gear must clear the housing",
        "status": "observed_mismatch", "severity": "error",
        "evidence": ["intersection volume 1200 mm^3"],
        "explanation": "The exact imported STEP solids overlap.",
        "repair_instruction": "Cut a housing cavity around the output gear.",
    }]}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "review-1", "type": "function", "function": {
                "name": "submit_review", "arguments": json.dumps(review)}}]},
            "calls": [{"id": "review-1", "name": "submit_review", "input": review}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.review_session(state(
        validation={"identity": {}, "report": {"inspection": {"configurations": []}}, "artifacts": []},
    ))
    assert result["phase"] == "cad_session"
    assert result["review"]["action"] == "repair"
    assert "output gear" in result["cad_history"][-1]["content"]
    assert result["validation"]["report"]["review"]["findings"][0]["id"] == "gear_overlap"


@pytest.mark.asyncio
async def test_second_reviewer_repair_publishes_findings_for_user_edit(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = {
        "manifest": {"schemaVersion": 1, "units": "mm", "components": [], "instances": [],
            "rootComponentId": None, "references": [], "joints": [],
            "configurations": [], "featureOperations": []}, "files": {},
    }
    review = {"summary": "One overlap remains.", "action": "repair", "findings": [{
        "id": "overlap", "statement": "Parts should clear", "status": "observed_mismatch",
        "severity": "error", "evidence": ["10 mm3 overlap"], "explanation": "Measured overlap.",
        "repair_instruction": "Adjust the cavity.",
    }]}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "review-final", "type": "function", "function": {
                "name": "submit_review", "arguments": json.dumps(review)}}]},
            "calls": [{"id": "review-final", "name": "submit_review", "input": review}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.review_session(state(
        validation={"report": {"inspection": {}}}, review_repairs=1))
    assert result["phase"] == "publish"
    assert result["review"]["action"] == "publish"
    assert "remaining findings" in result["review"]["summary"]


@pytest.mark.asyncio
async def test_reviewer_preflight_rejects_uninspectable_assembly_inventory(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = {
        "manifest": {"schemaVersion": 1, "units": "mm", "components": [{
            "id": "root", "name": "Root", "source": "assemblies/root.py", "kind": "assembly",
            "dependencies": [], "parameters": {}, "color": "#b9c4ad",
        }], "instances": [{"id": "root_inst", "definitionId": "root", "parentId": None,
            "name": "Root", "frame": {"position": [0, 0, 0], "rotation": [0, 0, 0]}}],
            "rootComponentId": "root", "references": [], "joints": [],
            "configurations": [], "featureOperations": []},
        "files": {"assemblies/root.py": "def build(p,d): return None"},
    }

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("deterministic evidence gap must be handled before a model review")

    monkeypatch.setattr(design.models, "turn", unexpected)
    result = await design.review_session(state(validation={"report": {"inspection": {
        "components": {"root": {"solidCount": 3}},
        "configurations": [{"id": "as_built", "instanceCount": 0}],
    }}}))
    assert result["phase"] == "cad_session"
    assert result["review"]["findings"][0]["id"] == "assembly_instance_inventory"


@pytest.mark.asyncio
async def test_reviewer_tool_budget_forces_a_decision(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = {
        "manifest": {"schemaVersion": 1, "units": "mm", "components": [], "instances": [],
            "rootComponentId": None, "references": [], "joints": [],
            "configurations": [], "featureOperations": []},
        "files": {},
    }
    review = {"summary": "Enough evidence gathered.", "action": "publish", "findings": []}

    async def turn(*_args, **kwargs):
        assert [tool["function"]["name"] for tool in _args[2]] == ["submit_review"]
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "review-budget", "type": "function", "function": {
                "name": "submit_review", "arguments": json.dumps(review)}}]},
            "calls": [{"id": "review-budget", "name": "submit_review", "input": review}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.review_session(state(
        validation={"report": {"inspection": {}}}, review_reads=3, review_inspected=True))
    assert result["phase"] == "publish"


@pytest.mark.asyncio
async def test_cad_session_forces_build_after_three_edits(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = {
        "manifest": {"schemaVersion": 1, "units": "mm",
            "components": [{"id": "block", "name": "Block", "source": "parts/block.py",
                "kind": "solid", "dependencies": [], "parameters": {}, "color": "#b9c4ad"}],
            "instances": [], "rootComponentId": "block", "references": [], "joints": [],
            "configurations": [], "featureOperations": []},
        "files": {"parts/block.py": "def build(p,d): return None"},
    }
    seen = {}
    async def turn(_config, _messages, tools, **_kwargs):
        seen["tools"] = [item["function"]["name"] for item in tools]
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "forced-build", "type": "function", "function": {
                "name": "build", "arguments": "{}"}}]},
            "calls": [{"id": "forced-build", "name": "build", "input": {}}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}
    monkeypatch.setattr(design.models, "turn", turn)
    async def build(_state):
        return {"phase": "validate", "build_result": {"ok": True}}
    monkeypatch.setattr(design, "build", build)
    result = await design.cad_session(state(cad_edits_since_build=3))
    assert "apply_changes" not in seen["tools"]
    assert result["phase"] == "validate"
