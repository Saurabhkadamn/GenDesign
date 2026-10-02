import json

import pytest

from forma_api.graphs import design
from forma_api.contracts import Limits


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
async def test_new_run_loads_prior_brief_and_revision_for_followup(monkeypatch, graph_mocks):
    async def run_row(_state):
        return {"id": _state["run_id"], "project_id": _state["project_id"],
            "owner_id": _state["owner_id"], "base_revision_id": "revision-1",
            "selected_ids": ["outer_race"]}

    async def snapshot(_revision_id):
        return {"manifest": {"schemaVersion": 1, "units": "mm", "components": [],
            "instances": [], "rootComponentId": None, "references": [], "joints": [],
            "configurations": [], "featureOperations": []}, "files": {"parts/race.py": "def build(p,d): pass"}}

    async def context(*_args):
        return {"previousMessages": [{"role": "user", "content": "Design the original bearing."}],
            "revision": {"id": "revision-1", "allRequirementsVerified": False}}

    monkeypatch.setattr(design, "run_row", run_row)
    monkeypatch.setattr(design.repo, "load_snapshot", snapshot)
    monkeypatch.setattr(design.repo, "agent_context", context)
    result = await design.coordinator(state(original_request="Change the outer race groove."))
    assert result["phase"] == "coordinator_session"
    assert result["project_context"]["previousMessages"][0]["content"] == "Design the original bearing."
    assert result["project_context"]["sourceFiles"] == ["parts/race.py"]


@pytest.mark.asyncio
async def test_followup_cad_delegation_preserves_original_brief_and_selection(monkeypatch, graph_mocks):
    arguments = {"role": "cad", "task": "Enlarge only the outer race groove."}
    async def turn(_config, messages, _tools, **_kwargs):
        assert "Design the original bearing" in messages[1]["content"]
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "delegate-1", "type": "function", "function": {
                "name": "delegate", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "delegate-1", "name": "delegate", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.coordinator_session(state(
        original_request="Change its outer race groove.", selected_ids=["outer_race"],
        project_context={"previousMessages": [{"role": "user",
            "content": "Design the original bearing."}]},
        coordinator_history=[{"role": "user", "content": "Change its outer race groove."}],
    ))
    assert result["phase"] == "cad_session"
    delegated = json.loads(result["cad_history"][0]["content"])
    assert delegated["originalBrief"] == "Design the original bearing."
    assert delegated["selectedIds"] == ["outer_race"]
    assert "outer race" in delegated["delegatedTask"]


@pytest.mark.asyncio
async def test_cad_session_rejects_malformed_source_before_persisting(monkeypatch, graph_mocks):
    manifest = {
        "schemaVersion": 1, "units": "mm",
        "components": [{"id": "block", "name": "Block", "source": "parts/block.py",
            "kind": "solid", "dependencies": [], "parameters": {}, "color": "#b9c4ad"}],
        "instances": [], "rootComponentId": "block", "references": [], "joints": [],
        "configurations": [], "featureOperations": [],
    }
    arguments = {"files": [{"path": "parts/block.py",
        "content": "import cadquery as cq  def build(p,d): return cq.Workplane(XY).box(10,10,10)"}],
        "manifest": manifest, "deletePaths": []}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "bad-source", "type": "function", "function": {
                "name": "apply_changes", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "bad-source", "name": "apply_changes", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.cad_session(state())
    assert "python_syntax" in result["cad_history"][-1]["content"]
    assert "candidate" not in graph_mocks


@pytest.mark.asyncio
async def test_cad_session_feedback_breaks_repeated_read_loop(monkeypatch, graph_mocks):
    arguments = {"path": "parts/block.py"}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "read-again", "type": "function", "function": {
                "name": "read_file", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "read-again", "name": "read_file", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    prior = [
        {"role": "user", "content": "Create a block."},
        {"role": "assistant", "tool_calls": [{"id": "old-1", "type": "function",
            "function": {"name": "read_file", "arguments": json.dumps(arguments)}}]},
        {"role": "tool", "tool_call_id": "old-1", "content": "{}"},
    ]
    result = await design.cad_session(state(cad_history=prior))
    assert "repeated_tool_action" in result["cad_history"][-1]["content"]


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
async def test_engineering_contract_failure_continues_to_cad_as_unverified(monkeypatch, graph_mocks):
    request = """Design Task: a mounting bracket with a 2g static load, material selection,
    and a minimum factor of safety of 2. Create the complete CAD design."""

    async def structured_failure(*_args, **_kwargs):
        raise design.Pause("The engineering model returned an invalid analysis result twice. summary: missing")

    monkeypatch.setattr(design, "structured_turn", structured_failure)
    result = await design.engineering_analysis(state(original_request=request))

    assert result["phase"] == "cad_session"
    assert "typed analysis contract" in result["engineering_summary"]
    assert any("unverified" in item.lower() for item in result["engineering_remarks"])
    assert result["model_calls"] == 2


@pytest.mark.asyncio
async def test_coordinator_engineering_handoff_goes_to_cad_without_another_model_call(
        monkeypatch, graph_mocks):
    async def analysis(*_args, **_kwargs):
        return design.Analysis(
            summary="Axis and fit calculated.",
            selected_material="Assembly components use different materials.",
            manufacturing_method="Machined mount, purchased bushings and fasteners.",
            recommendations=["Preserve the compound-angle axis."],
        ), {"model_calls": 3, "search_count": 0}

    monkeypatch.setattr(design, "structured_turn", analysis)
    request = "Design the suspension assembly."
    result = await design.engineering_analysis(state(
        original_request=request,
        engineering_from_coordinator=True,
        coordinator_pending_call={"id": "engineering-call"},
        coordinator_history=[{"role": "user", "content": request}],
    ))
    assert result["phase"] == "cad_session"
    assert result["coordinator_task"] == request
    assert result["engineering_summary"] == "Axis and fit calculated."
    assert result["model_calls"] == 3
    assert result["coordinator_pending_call"] == {}


def test_engineering_analysis_accepts_component_specific_material_choices():
    value = design.Analysis.model_validate({
        "summary": "Assembly material choices.",
        "selected_material": "Control arm: aluminum. " * 30,
        "manufacturing_method": "Mount: machined. Bushing: purchased. " * 25,
    })
    assert "Control arm" in value.selected_material
    assert "Bushing" in value.manufacturing_method


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
async def test_two_reviewer_repair_cycles_publish_remaining_findings(monkeypatch, graph_mocks):
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
        validation={"report": {"inspection": {}}}, review_repairs=2))
    assert result["phase"] == "publish"
    assert result["review"]["action"] == "publish"
    assert "remaining findings" in result["review"]["summary"]


@pytest.mark.asyncio
async def test_repeated_review_finding_stops_repair_loop(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = {
        "manifest": {"schemaVersion": 1, "units": "mm", "components": [], "instances": [],
            "rootComponentId": None, "references": [], "joints": [],
            "configurations": [], "featureOperations": []}, "files": {},
    }
    review = {"summary": "The same overlap remains.", "action": "repair", "findings": [{
        "id": "gear_overlap", "statement": "Output gear intersects housing", "status": "observed_mismatch",
        "severity": "error", "evidence": ["10 mm3 overlap"], "explanation": "Measured overlap.",
        "repair_instruction": "Increase the housing cavity.",
    }]}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "review-repeat", "type": "function", "function": {
                "name": "submit_review", "arguments": json.dumps(review)}}]},
            "calls": [{"id": "review-repeat", "name": "submit_review", "input": review}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    state_value = state(validation={"report": {"inspection": {}}}, review_repairs=1,
        review_fingerprint=design.review_fingerprint(review), candidate_hash="new-candidate")
    result = await design.review_session(state_value)
    assert result["phase"] == "publish"
    assert result["review"]["action"] == "publish"
    assert "same actionable finding remained" in result["review"]["summary"]


@pytest.mark.asyncio
async def test_review_repair_context_separates_passes_from_targets(graph_mocks):
    review = {"summary": "One issue.", "action": "repair", "findings": [
        {"id": "hole", "status": "observed_match", "statement": "Hole count correct"},
        {"id": "cavity", "status": "observed_mismatch", "statement": "Gear cavity missing",
            "evidence": ["The imported solids overlap by 10 mm3."],
            "repair_instruction": "Increase the housing cavity clearance."},
        {"id": "fatigue", "status": "requires_engineering", "statement": "Fatigue"},
    ]}
    result = await design.record_review_result(state(), review,
        {"manifest": {}, "files": {}}, {"report": {}}, [])
    repair_plan = json.loads(result["cad_history"][-1]["content"])
    assert [item["id"] for item in repair_plan["alreadyPassing"]] == ["hole"]
    assert [item["id"] for item in repair_plan["repairTargets"]] == ["cavity"]
    assert [item["id"] for item in repair_plan["unverifiedOrNonActionable"]] == ["fatigue"]


def test_review_finding_without_evidence_cannot_trigger_cad_repair():
    finding = {"id": "possible_gap", "status": "observed_mismatch",
        "statement": "A support may be missing", "evidence": [], "repair_instruction": "Add support."}
    assert not design.is_review_repair_target(finding)


@pytest.mark.asyncio
async def test_reviewer_allows_second_repair_for_a_different_finding(graph_mocks):
    review = {"summary": "A separate issue remains.", "action": "repair", "findings": [{
        "id": "missing_boss", "statement": "PCB boss is absent", "status": "not_observed",
        "severity": "warning", "evidence": ["No boss feature in source"],
        "explanation": "The PCB mounting boss is missing.", "repair_instruction": "Add the PCB boss.",
    }]}
    result = await design.record_review_result(
        state(review_repairs=1, review_fingerprint="different-prior-finding"), review,
        {"manifest": {}, "files": {}}, {"report": {}}, [])
    assert result["phase"] == "cad_session"
    assert result["review_repairs"] == 2


@pytest.mark.asyncio
async def test_reviewer_model_outage_does_not_block_publish(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = {
        "manifest": {"schemaVersion": 1, "units": "mm", "components": [], "instances": [],
            "rootComponentId": None, "references": [], "joints": [],
            "configurations": [], "featureOperations": []}, "files": {},
    }

    async def outage(*_args, **_kwargs):
        raise design.Pause("The review model is rate-limited.")

    monkeypatch.setattr(design, "agent_tool_turn", outage)
    result = await design.review_session(state(validation={"report": {"inspection": {}}}))
    assert result["phase"] == "publish"
    assert result["review"]["findings"][0]["id"] == "review_unavailable"
    assert "still published" in result["review"]["summary"]


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
    async def turn(*_args, **_kwargs):
        raise AssertionError("CAD must build without another model call")
    monkeypatch.setattr(design.models, "turn", turn)
    async def build(build_state):
        seen.update(build_state)
        return {"phase": "validate", "build_result": {"ok": True}}
    monkeypatch.setattr(design, "build", build)
    result = await design.cad_session(state(cad_edits_since_build=3))
    assert seen["pending_cad_call"]["name"] == "build"
    assert seen["cad_edits_since_build"] == 0
    assert result["phase"] == "validate"


def test_model_step_budget_bounds_each_tool_turn_not_the_whole_design():
    config = {"max_output_tokens": 64000}
    assert design.model_step_token_budget(config, "cad-session") == 64000
    assert design.model_step_token_budget(config, "coordinator-session") == 8192
    assert design.model_step_token_budget({"max_output_tokens": 4096}, "cad-session") == 4096
    assert Limits(maxModelCalls=96).maxModelCalls == 96


@pytest.mark.asyncio
async def test_incomplete_assembly_build_remains_an_intermediate_milestone(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = {
        "manifest": {"schemaVersion": 1, "units": "mm",
            "components": [
                {"id": "assembly", "name": "Assembly", "source": "assemblies/root.py",
                 "kind": "assembly", "dependencies": ["mount", "bushing"],
                 "parameters": {}, "color": "#b9c4ad"},
                {"id": "mount", "name": "Mount", "source": "parts/mount.py",
                 "kind": "solid", "dependencies": [], "parameters": {}, "color": "#b9c4ad"},
                {"id": "bushing", "name": "Bushing", "source": "parts/bushing.py",
                 "kind": "solid", "dependencies": [], "parameters": {}, "color": "#b9c4ad"}],
            "instances": [], "rootComponentId": "assembly", "references": [],
            "joints": [], "configurations": [], "featureOperations": []},
        "files": {"assemblies/root.py": "def build(p,d): return None",
                  "parts/mount.py": "def build(p,d): return None",
                  "parts/bushing.py": "def build(p,d): return None"},
    }
    async def turn(_config, _messages, _tools, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "build-1", "type": "function",
            "function": {"name": "build", "arguments": '{"final":true}'}}]},
            "calls": [{"id": "build-1", "name": "build", "input": {"final": True}}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}
    async def capture_build(next_state):
        return {"phase": "validate", "build_final": next_state["build_final"],
                "requested_part_types": next_state["requested_part_types"],
                "built_part_types": next_state["built_part_types"]}
    monkeypatch.setattr(design.models, "turn", turn)
    monkeypatch.setattr(design, "build", capture_build)
    result = await design.cad_session(state(original_request=(
        "Design Task: suspension. Components (10 part types, ~22 instances).")))
    assert result["phase"] == "validate"
    assert result["build_final"] is False
    assert result["requested_part_types"] == 10
    assert result["built_part_types"] == 2


@pytest.mark.asyncio
async def test_successful_intermediate_build_returns_to_cad():
    result = await design.validate(state(
        build_result={"ok": True, "inspection": {}}, build_final=False,
        candidate_hash="partial-hash", pending_cad_call={"id": "build-1"},
    ))
    assert result["phase"] == "cad_session"
    assert result["last_milestone_hash"] == "partial-hash"
    assert result["pending_cad_call"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_final,requested_final,finalizes", [
    (False, True, True), (True, True, False), (False, False, False),
])
async def test_unchanged_successful_milestone_can_enter_final_review(
        monkeypatch, graph_mocks, previous_final, requested_final, finalizes):
    graph_mocks["candidate"] = {"manifest": {
        "components": [{"id": "plate", "kind": "solid"}],
        "rootComponentId": "plate"}, "files": {"parts/plate.py": "saved source"}}

    async def turn(*_args, **_kwargs):
        arguments = {"final": requested_final}
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "final-build", "type": "function", "function": {
                "name": "build", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "final-build", "name": "build", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    build_states = []

    async def checked_build(build_state):
        build_states.append(build_state)
        return {"phase": "validate", "build_final": build_state["build_final"],
                "build_result": {"ok": True, "inspection": {}}}

    monkeypatch.setattr(design.models, "turn", turn)
    monkeypatch.setattr(design, "build", checked_build)
    inputs = state(build_result={"ok": True}, build_final=previous_final,
                   cad_edits_since_build=0)
    result = await design.cad_session(inputs)
    if finalizes:
        assert len(build_states) == 1 and build_states[0]["build_final"] is True
        assert (await design.validate({**inputs, **result}))["phase"] == "review_session"
    else:
        assert build_states == [] and result["phase"] == "cad_session"
        feedback = json.loads(result["cad_history"][-1]["content"])
        assert feedback["category"] == "unchanged_successful_candidate"


@pytest.mark.asyncio
async def test_inline_build_persists_consumed_model_turn_and_milestone(monkeypatch, graph_mocks):
    async def build_candidate(_run, checkpoint, _limits, _key):
        checkpoint["attempts"] = 1
        return {"ok": True, "inspection": {}}

    monkeypatch.setattr(design, "build_candidate", build_candidate)
    output = await design.build(state(
        model_calls=7, search_count=1, build_final=False,
        requested_part_types=10, built_part_types=2,
        pending_cad_call={"id": "build-1"},
        cad_history=[{"role": "user", "content": "Stage two parts."}],
    ))
    assert output["phase"] == "validate"
    assert output["model_calls"] == 7
    assert output["search_count"] == 1
    assert output["build_final"] is False
    assert output["pending_cad_call"]["id"] == "build-1"
    assert output["built_part_types"] == 2

def test_numbered_component_inventory_for_incremental_assembly():
    request = """Design Task: mechanism
## Components (2 types)
1. Base plate — fixed structure.
2. Guide rail — two instances.

## Assembly Requirements
- Keep both rails aligned.
"""
    assert design.requested_component_labels(request) == ["Base plate", "Guide rail"]
    assert design.requested_component_labels("Create a single 80 x 50 mm plate.") == []


@pytest.mark.asyncio
async def test_first_multi_component_cad_turn_exposes_incremental_milestone(monkeypatch, graph_mocks):
    request = """Design Task: mechanism
Components:
1. Base plate — fixed structure.
2. Guide rail — two instances.
Assembly Requirements:
- Keep both rails aligned.
"""
    manifest = {
        "schemaVersion": 1, "units": "mm",
        "components": [{"id": "base", "name": "Base plate", "source": "parts/base.py",
            "kind": "solid", "dependencies": [], "parameters": {}, "color": "#b9c4ad"}],
        "instances": [], "rootComponentId": "base", "references": [], "joints": [],
        "configurations": [], "featureOperations": [],
    }
    arguments = {"files": [{"path": "parts/base.py",
        "content": "import cadquery as cq\ndef build(p,d): return cq.Workplane('XY').box(10,10,2)"}],
        "manifest": manifest, "deletePaths": []}
    seen = {}

    async def turn(_config, messages, tools, **_kwargs):
        seen["tools"] = [item["function"]["name"] for item in tools]
        seen["context"] = json.loads(messages[1]["content"].split(": ", 1)[1])
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "base-patch", "type": "function", "function": {
                "name": "apply_changes", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "base-patch", "name": "apply_changes", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.cad_session(state(original_request=request,
        cad_history=[{"role": "user", "content": request}]))
    assert "apply_changes" in seen["tools"]
    assert "read_file" in seen["tools"]
    assert seen["context"]["componentMilestone"]["requestedTypes"] == ["Base plate", "Guide rail"]
    assert seen["context"]["request"] == request
    assert result["phase"] == "cad_session"
    assert len(graph_mocks["candidate"]["manifest"]["components"]) == 1


@pytest.mark.asyncio
async def test_second_part_requires_real_assembly_root_before_rebuild(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = {"manifest": {
        "schemaVersion": 1, "units": "mm", "rootComponentId": "base",
        "components": [{"id": "base", "kind": "solid"}, {"id": "gear", "kind": "solid"}],
        "instances": [{"id": "base_inst", "definitionId": "base"},
                      {"id": "gear_inst", "definitionId": "gear"}],
    }, "files": {"parts/base.py": "pass", "parts/gear.py": "pass"}}
    seen = {}

    async def turn(_config, messages, tools, **_kwargs):
        seen["tools"] = [item["function"]["name"] for item in tools]
        seen["context"] = json.loads(messages[1]["content"].split(": ", 1)[1])
        return {"message": {"role": "assistant", "content": ""}, "calls": [],
                "inputTokens": 1, "outputTokens": 1, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.cad_session(state(build_result={"ok": False,
        "error": {"category": "assembly_root_mismatch"}}))
    assert result["phase"] == "cad_session"
    assert seen["tools"] == ["apply_changes"]
    assert "rootComponentId" in seen["context"]["assemblyRootRepair"]


@pytest.mark.asyncio
async def test_partial_assembly_build_continues_until_all_types_staged(graph_mocks):
    request = """Design Task: mechanism
Components:
1. Base plate — fixed structure.
2. Guide rail — two instances.
Assembly Requirements:
- Keep both rails aligned.
"""
    graph_mocks["candidate"] = {"manifest": {"components": [
        {"id": "base", "kind": "solid"}]}, "files": {}}
    history = [{"role": "user", "content": request}, {"role": "assistant", "content": "",
        "tool_calls": [{"id": "build-1", "type": "function", "function": {
            "name": "build", "arguments": "{}"}}]}]
    inputs = state(original_request=request, cad_history=history,
        pending_cad_call={"id": "build-1"}, build_result={"ok": True})
    partial = await design.validate(inputs)
    assert partial["phase"] == "cad_session"
    assert "1 of 2" in partial["cad_history"][-1]["content"]
    graph_mocks["candidate"]["manifest"]["components"].append(
        {"id": "rail", "kind": "solid"})
    complete = await design.validate(inputs)
    assert complete["phase"] == "review_session"


@pytest.mark.asyncio
async def test_inline_build_keeps_model_call_ordinal_for_next_cad_turn(monkeypatch, graph_mocks):
    request = """Design Task: mechanism
Components:
1. Base plate — fixed structure.
2. Guide rail — two instances.
"""
    graph_mocks["candidate"] = {"manifest": {
        "schemaVersion": 1, "units": "mm",
        "components": [{"id": "base", "name": "Base plate", "kind": "solid"}],
        "instances": [], "rootComponentId": "base", "references": [], "joints": [],
        "configurations": [], "featureOperations": [],
    }, "files": {"parts/base.py": "def build(p,d): pass"}}
    arguments = {}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "build-2", "type": "function", "function": {
                "name": "build", "arguments": "{}"}}]},
            "calls": [{"id": "build-2", "name": "build", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    seen = {}

    async def inline_build(build_state):
        seen["model_calls"] = build_state["model_calls"]
        return {"phase": "validate", "attempts": 2}

    monkeypatch.setattr(design.models, "turn", turn)
    monkeypatch.setattr(design, "build", inline_build)
    result = await design.cad_session(state(original_request=request, model_calls=4,
        cad_edits_since_build=1, cad_history=[{"role": "user", "content": request}]))

    assert seen["model_calls"] == 5
    assert result["phase"] == "validate"
    assert result["model_calls"] == 5
    assert result["cad_edits_since_build"] == 0
    assert result["pending_cad_call"]["id"] == "build-2"


@pytest.mark.asyncio
async def test_build_ledger_key_changes_with_candidate_and_requirements(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = {"manifest": {"components": [{"id": "base", "kind": "solid"}],
        "rootComponentId": "base"}, "files": {"parts/base.py": "source version 1"}}
    keys = []

    async def capture_operation(_run, key, _kind, callback, **_kwargs):
        keys.append(key)
        return await callback()

    async def fake_build(_run, cp, _limits, _key):
        cp["attempts"] += 1
        return {"ok": True}

    monkeypatch.setattr(design, "operation", capture_operation)
    monkeypatch.setattr(design, "build_candidate", fake_build)
    await design.build(state(attempts=2))
    await design.build(state(attempts=2))
    graph_mocks["candidate"]["files"]["parts/base.py"] = "source version 2"
    await design.build(state(attempts=2))
    await design.build(state(attempts=2, requirements=[{"id": "new-check",
        "kind": "unverified", "description": "Additional advisory check"}]))

    assert keys[0] == keys[1]
    assert keys[1] != keys[2]
    assert keys[2] != keys[3]


@pytest.mark.asyncio
async def test_cad_session_registers_buildable_part_when_manifest_is_omitted(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = {"manifest": {
        "schemaVersion": 1, "units": "mm",
        "components": [{"id": "base", "name": "Base", "source": "parts/base.py",
            "kind": "solid", "dependencies": [], "parameters": {}}],
        "instances": [], "rootComponentId": "base",
    }, "files": {"parts/base.py": "def build(parameters, dependencies):\n    return None"}}
    arguments = {"files": [{"path": "parts/sector_gear.py",
        "content": "def build(parameters, dependencies):\n    return None"}],
        "manifest": None, "deletePaths": []}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "new-part", "type": "function", "function": {
                "name": "apply_changes", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "new-part", "name": "apply_changes", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.cad_session(state())
    components = graph_mocks["candidate"]["manifest"]["components"]
    assert [(item["id"], item["source"]) for item in components] == [
        ("base", "parts/base.py"), ("sector_gear", "parts/sector_gear.py")]
    assert "sector_gear" in result["cad_history"][-1]["content"]
    assert graph_mocks["candidate"]["manifest"]["rootComponentId"] == "base"


def test_auto_registration_skips_helpers_without_build_and_existing_components():
    manifest = {"components": [{"id": "base", "source": "parts/base.py", "kind": "solid"}]}
    files = {
        "parts/base.py": "def build(parameters, dependencies):\n    return None",
        "parts/helper.py": "def radius():\n    return 1",
        "parts/rail.py": "def build(parameters, dependencies):\n    return None",
    }
    updated, added = design.register_unlisted_part_sources(manifest, files)
    assert added == ["rail"]
    assert len(updated["components"]) == 2
    assert len(manifest["components"]) == 1


@pytest.mark.asyncio
async def test_cad_session_stops_repeated_text_instead_of_tool_call(monkeypatch, graph_mocks):
    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "[[{invalid tool text]"},
            "calls": [], "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    first = await design.cad_session(state())
    assert first["phase"] == "cad_session"
    assert first["cad_invalid_tool_attempts"] == 1
    stopped = await design.cad_session(state(cad_invalid_tool_attempts=2))
    assert stopped["phase"] == "final"
    assert stopped["terminal_status"] == "failed"
    assert "no usable tool call three times" in stopped["final_message"]


@pytest.mark.asyncio
async def test_cad_session_stops_repeated_malformed_tool_arguments(monkeypatch, graph_mocks):
    arguments = {"path": ""}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "empty-path", "type": "function", "function": {
                "name": "read_file", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "empty-path", "name": "read_file", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.cad_session(state(cad_invalid_tool_attempts=2))
    assert result["phase"] == "final"
    assert result["terminal_status"] == "failed"
    assert "invalid tool action three times" in result["final_message"]


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
        validation={"report": {"inspection": {}}}, review_repairs=2))
    assert result["phase"] == "publish"
    assert result["review"]["action"] == "publish"
    assert "remaining findings" in result["review"]["summary"]


def test_engineering_material_and_process_can_be_explanatory_text():
    result = design.Analysis(
        summary="Preliminary engineering assessment.",
        selected_material="7075-T6 with certified minimum yield, density and temper-specific properties " * 12,
        manufacturing_method="Forged case and carrier, finish-machined at the bearing bores and mounting faces " * 12,
    )
    assert len(result.selected_material) > 200
    assert len(result.manufacturing_method) > 200


@pytest.mark.asyncio
async def test_pending_contract_correction_resumes_without_replaying_primary(monkeypatch, graph_mocks):
    diagnostic = "selected_material and manufacturing_method exceeded their field limits"
    rows = {
        "graph:analysis:0": {"status": "failed", "result": {
            "category": "tool_protocol", "diagnostic": diagnostic}},
        "graph:analysis:contract-repair:1": {"status": "ambiguous", "result": None},
    }

    async def one(_table, params, *, required=True):
        key = params["operation_key"].removeprefix("eq.")
        return rows.get(key)

    monkeypatch.setattr(design.db, "one", one)
    calls = []
    payload = {"summary": "Conservative concept assessment.",
               "selected_material": "7075-T6", "manufacturing_method": "CNC machining"}

    async def turn(_config, messages, tools, **_kwargs):
        calls.append(messages)
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "analysis-correction", "type": "function", "function": {
                "name": "submit_analysis", "arguments": json.dumps(payload)}}]},
            "calls": [{"id": "analysis-correction", "name": "submit_analysis", "input": payload}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    value, usage = await design.structured_turn(
        state(), "engineering", "analysis", "Assess the design.", "submit_analysis", design.Analysis)

    assert value.selected_material == "7075-T6"
    assert usage["model_calls"] == 2
    assert len(calls) == 1  # Only the correction call; the primary call is not replayed.
    assert diagnostic in calls[0][-1]["content"]


@pytest.mark.asyncio
async def test_resumed_coordinator_answer_reaches_context_and_cad_delegation(monkeypatch, graph_mocks):
    original = "Ask me for the new plate thickness before editing."
    answer = "Use 3 mm and preserve all 60 occurrences."
    monkeypatch.setattr(design, "interrupt", lambda _payload: {"message": answer})
    initial = state(original_request=original, question="New thickness?",
        coordinator_pending_call={"id": "question", "name": "ask_user"},
        coordinator_history=[{"role": "user", "content": original},
            {"role": "assistant", "tool_calls": [{"id": "question", "type": "function",
                "function": {"name": "ask_user", "arguments": '{"question":"New thickness?"}'}}]}])
    resumed = await design.coordinator_question(initial)
    assert resumed["coordinator_history"][-1] == {"role": "user", "content": answer}

    async def turn(_config, messages, _tools, **_kwargs):
        context = json.loads(messages[1]["content"].split(": ", 1)[1])
        assert answer in context["latestRequest"]
        assert messages[-1] == {"role": "user", "content": answer}
        arguments = {"role": "cad", "task": "Set thickness to 3 mm."}
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "delegate", "type": "function", "function": {"name": "delegate", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "delegate", "name": "delegate", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.coordinator_session({**initial, **resumed})
    assert result["phase"] == "cad_session"
    task = json.loads(result["cad_history"][0]["content"])
    assert answer in task["latestRequest"]
    assert initial["original_request"] == original


@pytest.mark.asyncio
async def test_resumed_cad_answer_is_presented_as_user_input(monkeypatch, graph_mocks):
    original = "Ask me for the thickness."
    answer = "The thickness is 3 mm."
    monkeypatch.setattr(design, "interrupt", lambda _payload: {"message": answer})
    initial = state(original_request=original, pending_cad_call={"id": "question", "name": "ask_user"},
        cad_history=[{"role": "user", "content": original},
            {"role": "assistant", "tool_calls": [{"id": "question", "type": "function",
                "function": {"name": "ask_user", "arguments": '{"question":"Thickness?"}'}}]}])
    resumed = await design.cad_question(initial)
    assert resumed["cad_history"][-1] == {"role": "user", "content": answer}

    async def tool_turn(_state, **kwargs):
        assert answer in kwargs["context"]["request"]
        assert kwargs["history"][-1] == {"role": "user", "content": answer}
        return None, kwargs["history"], {"model_calls": 1, "search_count": 0}

    monkeypatch.setattr(design, "agent_tool_turn", tool_turn)
    await design.cad_session({**initial, **resumed})
